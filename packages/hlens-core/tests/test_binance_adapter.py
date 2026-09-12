"""Binance adapter against recorded responses (respx-mocked; no network)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from hlens_core.adapters import BinanceFutures, GeoBlocked, RateLimited, VenueAdapter
from hlens_core.adapters.binance import kline_weight
from hlens_core.contracts import Completeness, InstrumentStatus, LSKind, Semantic, Side, Transport

BASE = "https://fapi.binance.com"
MIRROR = "https://www.binance.com"


@pytest.fixture
def adapter(clock):
    # A fake clock on the budget: a 429 fills the window on purpose, and waiting out a
    # real 60 s window inside a unit test would be absurd.
    from hlens_core.ratelimit import Budget, venue

    budget = Budget("203.0.113.9", venue("binance"), clock=clock, sleep=clock.sleep)
    a = BinanceFutures(budget=budget)
    a.http.retries = 1  # keep failure tests quick
    a.http._sleep = _nosleep
    return a


async def _nosleep(_seconds: float) -> None:
    return None


def route(path: str, payload, *, status=200, headers=None, base=BASE):
    return respx.get(base + path).mock(
        return_value=httpx.Response(status, json=payload, headers=headers or {})
    )


def test_adapter_satisfies_the_protocol(adapter):
    assert isinstance(adapter, VenueAdapter)


def test_capabilities_are_three_separate_answers(adapter):
    caps = adapter.capabilities()
    from hlens_core.adapters import Capability, Mode

    liq = caps.get(Capability.liquidation_stream)
    assert (liq.supported, liq.mode, liq.completeness) == (
        True, Mode.ws, Completeness.lower_bound,
    )
    oi = caps.get(Capability.open_interest)
    assert oi.supported and oi.per_symbol, "Binance has no all-market OI endpoint"
    assert not caps.supports(Capability.wallet_fills)


# -- instruments -------------------------------------------------------------


@respx.mock
async def test_discover_instruments(adapter, fx):
    route("/fapi/v1/exchangeInfo", fx("binance/exchangeInfo"))
    route("/fapi/v1/fundingInfo", fx("binance/fundingInfo"))
    instruments = await adapter.discover_instruments()
    by_symbol = {i.symbol: i for i in instruments}

    btc = by_symbol["BTC"]
    assert btc.venue_symbol == "BTCUSDT"
    assert btc.tick == Decimal("0.10")
    assert btc.qty_step == Decimal("0.001")
    assert btc.status is InstrumentStatus.trading
    assert btc.funding_interval_h == Decimal(8)
    assert btc.instrument_id == "binance:BTCUSDT"
    assert btc.listed_ts == 1567965300000

    # 1000PEPEUSDT is PEPE with a multiplier, not a coin called "1000PEPE"
    pepe = by_symbol["PEPE"]
    assert (pepe.venue_symbol, pepe.mult) == ("1000PEPEUSDT", Decimal(1000))
    assert adapter.symbols.venue_symbol("PEPE") == "1000PEPEUSDT"


@respx.mock
async def test_discover_instruments_skips_non_perpetual_contracts(adapter, fx):
    info = fx("binance/exchangeInfo")
    info["symbols"] = info["symbols"] + [
        dict(info["symbols"][0], symbol="BTCUSDT_260626", contractType="CURRENT_QUARTER"),
        dict(info["symbols"][0], symbol="BTCTRADFI", contractType="TRADIFI_PERPETUAL"),
    ]
    route("/fapi/v1/exchangeInfo", info)
    route("/fapi/v1/fundingInfo", [])
    got = {i.venue_symbol for i in await adapter.discover_instruments()}
    assert "BTCUSDT" in got
    assert "BTCUSDT_260626" not in got
    assert "BTCTRADFI" not in got, "TRADIFI_PERPETUAL is a different product"


@respx.mock
async def test_non_trading_status_is_mapped_not_dropped(adapter, fx):
    route("/fapi/v1/exchangeInfo", fx("binance/exchangeInfo"))
    route("/fapi/v1/fundingInfo", fx("binance/fundingInfo"))
    statuses = {i.symbol: i.status for i in await adapter.discover_instruments()}
    assert InstrumentStatus.trading in statuses.values()
    assert statuses.get("OMG") in (InstrumentStatus.halted, InstrumentStatus.delisted, None)


@respx.mock
async def test_funding_interval_comes_from_funding_info_not_a_default(adapter, fx):
    info = fx("binance/fundingInfo")
    info[0]["fundingIntervalHours"] = 4
    route("/fapi/v1/exchangeInfo", fx("binance/exchangeInfo"))
    route("/fapi/v1/fundingInfo", info)
    await adapter.discover_instruments()
    route("/fapi/v1/premiumIndex", fx("binance/premiumIndex"))
    rates = {f.symbol: f for f in await adapter.fetch_funding_rates()}
    assert rates["PEPE"].interval_h == Decimal(4)
    assert rates["PEPE"].rate_8h == rates["PEPE"].rate * 2
    assert rates["BTC"].interval_h == Decimal(8)
    assert rates["BTC"].rate_8h == rates["BTC"].rate


# -- premium index -----------------------------------------------------------


@respx.mock
async def test_premium_index_yields_marks_and_funding_in_one_request(adapter, fx):
    r = route("/fapi/v1/premiumIndex", fx("binance/premiumIndex"))
    marks, fundings = await adapter.fetch_premium_index()
    assert r.call_count == 1
    assert len(marks) == len(fundings) == 4
    btc = next(m for m in marks if m.symbol == "BTC")
    assert btc.mark == Decimal("77293.97295352")
    assert btc.index == Decimal("77323.24369565")
    assert btc.source == "/fapi/v1/premiumIndex"
    assert btc.transport is Transport.rest
    f = next(f for f in fundings if f.symbol == "BTC")
    assert f.rate == Decimal("0.00005664")
    assert f.next_ts == 1789200000000


@respx.mock
async def test_all_market_premium_index_costs_10_weight(adapter, fx):
    route("/fapi/v1/premiumIndex", fx("binance/premiumIndex"))
    await adapter.fetch_mark_prices()
    assert adapter.budget.used() == 10
    await adapter.fetch_mark_prices(["BTC"])  # single symbol = weight 1
    assert adapter.budget.used() == 11


@respx.mock
async def test_used_weight_header_syncs_the_ledger(adapter, fx):
    route("/fapi/v1/premiumIndex", fx("binance/premiumIndex"),
          headers={"X-MBX-USED-WEIGHT-1M": "812"})
    await adapter.fetch_mark_prices()
    assert adapter.budget.used() == 812, "the venue's count wins over ours"


# -- open interest -----------------------------------------------------------


@respx.mock
async def test_open_interest_is_exchange_reported_without_marks(adapter, fx):
    route("/fapi/v1/openInterest", fx("binance/openInterest_BTCUSDT"))
    (oi,) = await adapter.fetch_open_interest(["BTC"])
    assert oi.oi_base == Decimal("103552.534")
    assert oi.oi_usd is None, "no mark was supplied; a USD figure would be invented"
    assert oi.semantic is Semantic.exchange_reported


@respx.mock
async def test_open_interest_usd_is_marked_derived(adapter, fx):
    route("/fapi/v1/openInterest", fx("binance/openInterest_BTCUSDT"))
    (oi,) = await adapter.fetch_open_interest(["BTC"], marks={"BTC": Decimal("77293.97")})
    assert oi.oi_usd == Decimal("103552.534") * Decimal("77293.97")
    assert oi.semantic is Semantic.derived


@respx.mock
async def test_open_interest_applies_the_contract_multiplier(adapter, fx):
    route("/fapi/v1/exchangeInfo", fx("binance/exchangeInfo"))
    route("/fapi/v1/fundingInfo", fx("binance/fundingInfo"))
    await adapter.discover_instruments()
    route("/fapi/v1/openInterest",
          {"symbol": "1000PEPEUSDT", "openInterest": "1000", "time": 1789196916632})
    (oi,) = await adapter.fetch_open_interest(["PEPE"])
    assert oi.oi_base == Decimal(1_000_000), "1000 contracts of 1000 PEPE each"


# -- ratios ------------------------------------------------------------------


@respx.mock
async def test_four_long_short_kinds(adapter, fx):
    for ep in ("globalLongShortAccountRatio", "topLongShortAccountRatio",
               "topLongShortPositionRatio", "takerlongshortRatio"):
        route(f"/futures/data/{ep}", fx(f"binance/{ep}"))
    rows = await adapter.fetch_long_short_ratios("BTC", limit=2)
    kinds = {r.kind for r in rows}
    assert kinds == {LSKind.account, LSKind.top_account, LSKind.top_position, LSKind.taker}
    acct = next(r for r in rows if r.kind is LSKind.account)
    assert acct.long_share == Decimal("0.6169")
    assert acct.semantic is Semantic.exchange_reported
    taker = next(r for r in rows if r.kind is LSKind.taker)
    # buyVol / (buyVol + sellVol) = 17.7170 / 44.4030
    assert taker.long_share == Decimal("17.7170") / (Decimal("17.7170") + Decimal("26.6860"))
    assert taker.semantic is Semantic.derived, "a share we computed is not exchange-reported"


@respx.mock
async def test_zero_weight_ratio_endpoints_still_consume_budget(adapter, fx):
    route("/futures/data/globalLongShortAccountRatio", fx("binance/globalLongShortAccountRatio"))
    await adapter.fetch_long_short_ratios("BTC", [LSKind.account])
    assert adapter.budget.used() == 1, "weight 0, but the IP request rate is not free"


@respx.mock
async def test_empty_ratio_response_is_not_an_error(adapter):
    route("/futures/data/globalLongShortAccountRatio", [])
    assert await adapter.fetch_long_short_ratios("SOMEALT", [LSKind.account]) == []


# -- klines / ticker ---------------------------------------------------------


def test_kline_weight_tiers():
    assert (kline_weight(99), kline_weight(100), kline_weight(499)) == (1, 2, 2)
    assert (kline_weight(500), kline_weight(1000), kline_weight(1500)) == (5, 5, 10)


@respx.mock
async def test_klines_carry_taker_buy_volume(adapter, fx):
    route("/fapi/v1/klines", fx("binance/klines_BTCUSDT_1h"))
    candles = await adapter.fetch_klines("BTC", "1h", limit=3)
    assert len(candles) == 3
    first = candles[0]
    assert (first.o, first.h, first.low, first.c) == (
        Decimal("77186.40"), Decimal("77226.20"), Decimal("77129.00"), Decimal("77177.70"),
    )
    assert first.v == Decimal("2522.932")
    assert first.taker_buy_v == Decimal("1288.305")
    assert first.taker_buy_share < 1
    assert first.trades == 44959
    assert first.closed is True
    assert candles[-1].close_ts > candles[-1].ts


@respx.mock
async def test_ticker_24h_all_market_costs_40(adapter, fx):
    route("/fapi/v1/ticker/24hr", fx("binance/ticker24hr"))
    tickers = await adapter.fetch_ticker_24h()
    assert adapter.budget.used() == 40
    btc = next(t for t in tickers if t.symbol == "BTC")
    assert btc.vol24h_usd == Decimal("15231716655.42")
    assert btc.chg24h_pct == Decimal("-0.021"), "percent, already; do not multiply by 100"


# -- websocket parsers -------------------------------------------------------


def test_parse_mark_price_stream(adapter, fx):
    marks = adapter._parse_mark_event(fx("binance/ws_markPrice"), None)
    assert marks and all(m.transport is Transport.ws for m in marks)
    btc = next(m for m in marks if m.symbol == "BTC")
    assert btc.mark > 0 and btc.index is not None
    assert btc.source == "ws:!markPrice@arr@1s"

    fundings = adapter.mark_event_funding(fx("binance/ws_markPrice"))
    assert {f.symbol for f in fundings} == {m.symbol for m in marks}
    assert all(f.rate_8h is not None for f in fundings)


def test_parse_force_order_stream(adapter, fx):
    events = fx("binance/ws_forceOrder")
    liqs = [liq for e in events for liq in adapter._parse_force_order(e, None)]
    assert liqs, "fixture should contain at least one liquidation"
    for liq in liqs:
        assert liq.throttled_source is True
        assert liq.completeness is Completeness.lower_bound
        assert liq.notional_usd == liq.price * liq.size
        assert liq.transport is Transport.ws
    # a SELL forceOrder is the venue closing a LONG
    sells = [
        e for e in events if (e.get("o") or {}).get("S") == "SELL"
    ]
    if sells:
        (liq,) = adapter._parse_force_order(sells[0], None)
        assert liq.side is Side.long


def test_force_order_filters_by_symbol(adapter, fx):
    e = fx("binance/ws_forceOrder")[0]
    assert adapter._parse_force_order(e, {"NOSUCHUSDT"}) == []


# -- transport behaviour -----------------------------------------------------


@respx.mock
async def test_geo_block_fails_over_to_the_mirror(adapter, fx):
    respx.get(BASE + "/fapi/v1/premiumIndex").mock(
        return_value=httpx.Response(451, text="Service unavailable from a restricted location.")
    )
    mirror = route("/fapi/v1/premiumIndex", fx("binance/premiumIndex"), base=MIRROR)
    marks = await adapter.fetch_mark_prices()
    assert mirror.called and marks
    assert BASE in adapter.http.blocked_bases


@respx.mock
async def test_geo_block_on_every_base_raises(adapter):
    for base in (BASE, MIRROR):
        respx.get(base + "/fapi/v1/premiumIndex").mock(return_value=httpx.Response(451))
    with pytest.raises(GeoBlocked):
        await adapter.fetch_mark_prices()


@respx.mock
async def test_429_feeds_the_budget_and_respects_retry_after(adapter, fx):
    slept: list[float] = []
    adapter.http._sleep = lambda s: slept.append(s) or _nosleep(s)
    respx.get(BASE + "/fapi/v1/premiumIndex").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}, text="Too many requests"),
            httpx.Response(200, json=fx("binance/premiumIndex")),
        ]
    )
    marks = await adapter.fetch_mark_prices()
    assert marks
    assert slept == [7.0]
    assert adapter.budget.factor == 0.75, "a 429 must shrink the budget, not just retry"


@respx.mock
async def test_429_that_never_clears_raises(adapter):
    adapter.http._sleep = _nosleep
    respx.get(BASE + "/fapi/v1/premiumIndex").mock(return_value=httpx.Response(429, text="no"))
    with pytest.raises(RateLimited):
        await adapter.fetch_mark_prices()


@respx.mock
async def test_circuit_opens_after_five_failures(adapter):
    from hlens_core.adapters import CircuitOpen

    adapter.http.retries = 0
    adapter.http._sleep = _nosleep
    for base in (BASE, MIRROR):
        respx.get(base + "/fapi/v1/openInterest").mock(return_value=httpx.Response(500))
    for _ in range(5):
        await adapter.fetch_open_interest(["BTC"])  # per-symbol errors are swallowed
    with pytest.raises(CircuitOpen):
        await adapter.http.get("/fapi/v1/openInterest", capability="open_interest")
    # a different capability is unaffected
    assert adapter.http.breakers.check("binance:klines", 0) == 0


@respx.mock
async def test_418_raises_banned_immediately_without_retrying(adapter):
    from hlens_core.adapters import Banned

    slept: list[float] = []
    adapter.http.retries = 3  # generous on purpose: none of them may be used
    adapter.http._sleep = lambda s: slept.append(s) or _nosleep(s)
    r = respx.get(BASE + "/fapi/v1/premiumIndex").mock(
        return_value=httpx.Response(
            418, headers={"Retry-After": "259200"}, text="Way too many requests"
        )
    )
    with pytest.raises(Banned) as excinfo:
        await adapter.fetch_mark_prices()

    assert r.call_count == 1, "retrying a ban is how 2 minutes becomes 3 days"
    assert slept == [], "a ban must not park the coroutine"
    assert excinfo.value.retryable is False
    assert excinfo.value.retry_after_s == 259200
    # the wait belongs to the shared ledger, so every other collector stops too
    assert adapter.budget.blocked_for == pytest.approx(259200)
    assert adapter.budget.available == 0
    assert MIRROR not in adapter.http.blocked_bases, "a ban is not a geo block"


@respx.mock
async def test_banned_is_still_caught_by_except_rate_limited(adapter):
    adapter.http.retries = 0
    respx.get(BASE + "/fapi/v1/premiumIndex").mock(return_value=httpx.Response(418))
    with pytest.raises(RateLimited):  # Banned subclasses RateLimited on purpose
        await adapter.fetch_mark_prices()


@respx.mock
async def test_html_429_from_binance_cuts_the_budget_not_the_concurrency(adapter):
    # Binance serves errors through its own proxy; an HTML body says nothing about
    # which limiter fired, so it must be treated as a plain weight rate limit.
    before_inflight = adapter.budget.max_inflight
    adapter.http.retries = 0
    respx.get(BASE + "/fapi/v1/premiumIndex").mock(
        return_value=httpx.Response(
            429, text="<html><head><title>429 Too Many Requests</title></head>"
                      "<body><center>nginx</center></body></html>",
        )
    )
    with pytest.raises(RateLimited) as excinfo:
        await adapter.fetch_mark_prices()

    assert excinfo.value.kind == "weight"
    assert adapter.budget.factor == 0.75, "the weight budget must take the hit"
    assert adapter.budget.max_inflight == before_inflight, "concurrency is not the problem"
    assert adapter.budget.stats.rate_limited_by_kind == {"weight": 1}


@respx.mock
async def test_retry_after_sleep_is_clamped_to_the_backoff_ceiling(adapter, clock):
    slept: list[float] = []
    adapter.http._sleep = lambda s: slept.append(s) or _nosleep(s)
    respx.get(BASE + "/fapi/v1/premiumIndex").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7200"}, text="slow down"),
            httpx.Response(
                200, json={"symbol": "BTCUSDT", "markPrice": "1", "time": 1789196919003}
            ),
        ]
    )
    t0 = clock.t
    await adapter.fetch_mark_prices(["BTC"])
    assert slept == [60.0], "two hours parked in a coroutine is a hung collector"
    # The real two-hour wait is enforced by the budget: the retry's reserve() sat on the
    # hard block until it expired, so no second request went out early.
    assert clock.t - t0 >= 7200
    assert adapter.budget.stats.wait_seconds >= 7200
