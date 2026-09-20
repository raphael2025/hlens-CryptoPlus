"""Every network-facing REST function, offline, against a trimmed fixture.

AGENTS §2.6: "every network function has an offline test with a trimmed
recorded fixture". The fixtures here are tagged ``source: documented`` rather
than ``live-recorded`` because AGENTS §3.3 requires a recording to be made from
the **production host's egress** (task ``M1-G``) — this development machine
shares that egress with a still-running legacy collector, and M1-A5 sent no
request of any kind. The field names come from ``04`` §2's table.

``respx`` intercepts at the transport layer, so the adapter builds the real URL
out of the real ``config/venues.yaml`` and nothing leaves the process.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from conftest import VENUES_PATH, binance_fixture_source, binance_payload
from hlens_core.adapters import (
    Admission,
    Capability,
    LanePriority,
    SymbolTable,
    UnsupportedCapability,
)
from hlens_core.adapters.binance import (
    AdmissionDenied,
    BinanceApiError,
    BinanceEndpoints,
    BinanceMarketDataAdapter,
    normalize_exchange_info,
    normalize_funding_info,
    symbol_table_of,
)
from hlens_core.adapters.binance.endpoints import (
    REST_EXCHANGE_INFO,
    REST_FUNDING_INFO,
    REST_FUNDING_RATE,
    REST_GLOBAL_LONG_SHORT_ACCOUNT_RATIO,
    REST_KLINES,
    REST_OPEN_INTEREST,
    REST_PREMIUM_INDEX,
    REST_TAKER_LONG_SHORT_RATIO,
    REST_TICKER_24H,
    REST_TOP_LONG_SHORT_POSITION_RATIO,
)
from hlens_core.contracts import LsRatioKind, Semantic, Venue
from hlens_core.ratelimit import FakeClock, Grant, LedgerConfig, Priority, RateLimitLedger

#: The fixtures' own instant: 2026-09-19T12:00:00Z.
T0 = 1_789_819_200_000
MIN = 60_000
HOUR = 3_600_000

#: A clock the test moves, so ``ingest_ts`` is a checkable number rather than
#: "roughly now".
INGEST_TS = T0 + 123

ENDPOINTS = BinanceEndpoints.load(VENUES_PATH)


def _symbols() -> SymbolTable:
    intervals = normalize_funding_info(binance_payload("funding_info"))
    return symbol_table_of(
        normalize_exchange_info(
            binance_payload("exchange_info"), ingest_ts=INGEST_TS, funding_interval_h=intervals
        )
    )


@pytest.fixture
def admission(ledger: RateLimitLedger) -> Admission[Priority, Grant]:
    """The real ledger, injected structurally. Seam ③: the adapter never
    imports it and the translation of the two priority vocabularies is the one
    lambda the caller owns."""
    return Admission(ledger, lambda lane: Priority(lane.value))


@pytest.fixture
def adapter() -> BinanceMarketDataAdapter:
    return BinanceMarketDataAdapter(
        client=httpx.AsyncClient(),
        endpoints=ENDPOINTS,
        symbols=_symbols(),
        clock=lambda: INGEST_TS,
    )


def _route(router: respx.Router, path: str, payload: object, **headers: str) -> respx.Route:
    return router.get(f"{ENDPOINTS.rest}{path}").mock(
        return_value=httpx.Response(200, json=payload, headers=headers)
    )


# --------------------------------------------------------------------------- #
# Provenance of the fixtures themselves
# --------------------------------------------------------------------------- #
def test_every_fixture_declares_where_it_came_from() -> None:
    """AGENTS §3.3. ``documented`` is not ``live-recorded`` and this step is not
    allowed to pretend otherwise — the dev machine shares the production egress,
    where a 429 escalates to a 418 that bans the whole machine."""
    for name in (
        "exchange_info",
        "funding_info",
        "premium_index",
        "open_interest",
        "ticker_24hr",
        "klines",
        "funding_rate",
        "global_long_short_account_ratio",
        "top_long_short_position_ratio",
        "taker_long_short_ratio",
        "ws_mark_price_arr",
        "ws_force_order",
    ):
        assert binance_fixture_source(name) == "documented"


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_fetch_instruments(
    adapter: BinanceMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    respx.get(f"{ENDPOINTS.rest}{REST_EXCHANGE_INFO}").mock(
        return_value=httpx.Response(200, json=binance_payload("exchange_info"))
    )
    respx.get(f"{ENDPOINTS.rest}{REST_FUNDING_INFO}").mock(
        return_value=httpx.Response(200, json=binance_payload("funding_info"))
    )

    records = await adapter.fetch_instruments(admission=admission)

    # The quarterly contract is filtered out: 04 §2 takes PERPETUAL only.
    assert [r.venue_symbol for r in records] == ["BTCUSDT", "1000PEPEUSDT"]
    btc, pepe = records
    assert btc.venue is Venue.BINANCE and btc.symbol == "BTC"
    assert btc.funding_interval_h == 8
    assert btc.mult == Decimal(1)
    assert btc.tick == Decimal("0.10")
    assert pepe.symbol == "PEPE" and pepe.venue_symbol == "1000PEPEUSDT"
    assert pepe.mult == Decimal(1000)
    assert pepe.funding_interval_h == 4  # fundingInfo lists only the exceptions
    # ts is exchangeInfo's own serverTime — F1's reconciliation instant.
    assert btc.ts == T0 and btc.ingest_ts == INGEST_TS
    # Not contract fields: the SCD-2 bounds are assigned by the persistence
    # boundary that compares against the currently open row (M1-E).
    assert "valid_from" not in btc.model_dump()
    assert "valid_to" not in btc.model_dump()


@pytest.mark.asyncio
@respx.mock
async def test_discovery_charges_both_buckets(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    respx.get(f"{ENDPOINTS.rest}{REST_EXCHANGE_INFO}").mock(
        return_value=httpx.Response(200, json=binance_payload("exchange_info"))
    )
    respx.get(f"{ENDPOINTS.rest}{REST_FUNDING_INFO}").mock(
        return_value=httpx.Response(200, json=binance_payload("funding_info"))
    )
    await adapter.fetch_instruments(admission=admission)
    assert ledger.snapshot("binance:fapi_weight").used_resident_per_min == 1
    assert ledger.snapshot("binance:funding_rate").used_resident_per_min == 1


# --------------------------------------------------------------------------- #
# Fast lane
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_fetch_mark_prices_whole_market(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    route = _route(respx, REST_PREMIUM_INDEX, binance_payload("premium_index"))

    records = await adapter.fetch_mark_prices(admission=admission)

    # No `symbol` parameter: the whole market in one 10-weight call (04 §2).
    assert "symbol" not in route.calls[0].request.url.params
    assert ledger.snapshot("binance:fapi_weight").used_fast_per_min == 10

    assert [r.symbol for r in records] == ["BTC", "PEPE"]
    btc = records[0]
    assert btc.mark == Decimal("64210.30000000")
    assert btc.index_px == Decimal("64198.74318182")
    assert btc.semantic is Semantic.MARK_PRICE
    # ts is the observation instant, NOT a minute bucket: date_trunc happens at
    # the persistence boundary (03 §5 / contracts.base).
    assert btc.ts == T0 and btc.obs_ts_fast == T0
    assert btc.ingest_ts == INGEST_TS
    # Slow-lane fields belong to the other statement and stay None.
    assert btc.oi_base is None and btc.vol24h_usd is None and btc.obs_ts_slow is None
    # 04 §2's premiumIndex field list has no premium; mark - index is a computed
    # spread and belongs to `compute` (F7), not to an observation.
    assert btc.premium is None


@pytest.mark.asyncio
@respx.mock
async def test_an_unknown_symbol_is_skipped_and_counted(
    adapter: BinanceMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    _route(respx, REST_PREMIUM_INDEX, binance_payload("premium_index"))
    records = await adapter.fetch_mark_prices(admission=admission)
    assert all(r.symbol != "NEWCOIN" for r in records)
    assert adapter.unknown_venue_symbols == ("NEWCOINUSDT",)


@pytest.mark.asyncio
@respx.mock
async def test_funding_stays_on_the_venues_native_interval(
    adapter: BinanceMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``03`` §5: the row stores the raw value plus ``funding_interval_h``; the
    8-hour figure is derived on read and never stored."""
    _route(respx, REST_PREMIUM_INDEX, binance_payload("premium_index"))
    records = await adapter.fetch_funding(admission=admission)
    pepe = next(r for r in records if r.symbol == "PEPE")

    assert pepe.funding_rate == Decimal("-0.00005000")  # raw, 4-hourly
    assert pepe.funding_interval_h == 4
    assert pepe.funding_rate_8h == Decimal("-0.00005000") * 8 / 4
    # The derived value is a property, not a column: model_dump() is the
    # storable row and must not carry it.
    assert "funding_rate_8h" not in pepe.model_dump()


@pytest.mark.asyncio
@respx.mock
async def test_mark_price_and_funding_come_from_one_charged_call(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """``03`` §6's lane table budgets 20 weight a minute for a 30-second lane —
    one 10-weight ``premiumIndex`` call, not two."""
    route = _route(respx, REST_PREMIUM_INDEX, binance_payload("premium_index"))
    records = await adapter.fetch_mark_prices(admission=admission)
    assert route.call_count == 1
    assert ledger.snapshot("binance:fapi_weight").used_fast_per_min == 10
    btc = records[0]
    assert btc.mark is not None and btc.funding_rate is not None


@pytest.mark.asyncio
@respx.mock
async def test_an_empty_funding_rate_is_none_and_never_zero(
    adapter: BinanceMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``03`` §5: 未知写 NULL，绝不写 0. Binance writes ``""`` for a contract
    with no funding, and a ``0`` there would read as "funding is flat"."""
    payload = binance_payload("premium_index")
    payload[0]["lastFundingRate"] = ""
    payload[0]["nextFundingTime"] = 0
    _route(respx, REST_PREMIUM_INDEX, payload)
    btc = (await adapter.fetch_mark_prices(admission=admission))[0]
    assert btc.funding_rate is None
    assert btc.next_funding_ts is None


# --------------------------------------------------------------------------- #
# Slow lane
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_open_interest_is_converted_into_the_coins_own_units(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """Binance reports open interest in units of the **listed contract**, so
    ``1000PEPEUSDT``'s 1,250,000 is 1.25 billion PEPE. Without the multiplier an
    ``oi_share`` across two venues compares two different units."""
    route = _route(respx, REST_OPEN_INTEREST, binance_payload("open_interest"))

    records = await adapter.fetch_open_interest(admission=admission, symbols=["PEPE"])

    assert route.calls[0].request.url.params["symbol"] == "1000PEPEUSDT"
    assert len(records) == 1
    record = records[0]
    assert record.oi_base == Decimal("1250000.000") * 1000
    # 04 §2: the endpoint returns base-unit OI and a timestamp, nothing else.
    # The USD figure lives on openInterestHist (M2's backfill).
    assert record.oi_usd is None
    assert record.obs_ts_slow == T0 and record.obs_ts_fast is None
    assert ledger.snapshot("binance:fapi_weight").used_resident_per_min == 1


@pytest.mark.asyncio
@respx.mock
async def test_open_interest_is_one_charged_call_per_symbol(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """``03`` §6: 逐币 OI 180 次 = 180 weight/min — three quarters of Binance's
    whole 241. One grant each, so the ledger can pace and deny them singly."""
    _route(respx, REST_OPEN_INTEREST, binance_payload("open_interest"))
    await adapter.fetch_open_interest(admission=admission)  # every mapped coin
    assert ledger.snapshot("binance:fapi_weight").used_resident_per_min == 2


@pytest.mark.asyncio
@respx.mock
async def test_ticker_24h_whole_market(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    _route(respx, REST_TICKER_24H, binance_payload("ticker_24hr"))
    records = await adapter.fetch_ticker_24h(admission=admission)

    assert ledger.snapshot("binance:fapi_weight").used_resident_per_min == 40
    btc = records[0]
    assert btc.chg24h_pct == Decimal("-1.249")
    assert btc.vol24h_usd == Decimal("9812345678.12")
    assert btc.obs_ts_slow == T0 and btc.obs_ts_fast is None
    assert btc.mark is None


# --------------------------------------------------------------------------- #
# Ratios — the separate request bucket
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_fetch_ls_ratio_account_kind(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    route = _route(
        respx,
        REST_GLOBAL_LONG_SHORT_ACCOUNT_RATIO,
        binance_payload("global_long_short_account_ratio"),
    )
    points = await adapter.fetch_ls_ratio(
        admission=admission, kind=LsRatioKind.GLOBAL_LONG_SHORT_ACCOUNT, symbol="BTC", limit=2
    )

    params = route.calls[0].request.url.params
    assert params["symbol"] == "BTCUSDT"
    assert params["period"] == "5m"  # 决定 A8's grid, not a caller's option
    assert params["limit"] == "2"

    assert len(points) == 2
    assert [p.ts for p in points] == [T0 - 10 * MIN, T0 - 5 * MIN]
    assert points[0].long_share == Decimal("0.6442")  # a fraction, never a percent
    assert points[0].period == 300
    assert points[0].semantic is None
    # No weight at all: its own request bucket (04 §2, 03 §6).
    assert ledger.snapshot("binance:futures_data").used_resident_per_min == 1
    assert ledger.snapshot("binance:fapi_weight").used_resident_per_min == 0


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ls_ratio_position_kind(
    adapter: BinanceMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    _route(
        respx,
        REST_TOP_LONG_SHORT_POSITION_RATIO,
        binance_payload("top_long_short_position_ratio"),
    )
    points = await adapter.fetch_ls_ratio(
        admission=admission, kind=LsRatioKind.TOP_LONG_SHORT_POSITION, symbol="BTC"
    )
    assert points[0].kind is LsRatioKind.TOP_LONG_SHORT_POSITION
    assert points[0].long_share == Decimal("0.5868")


@pytest.mark.asyncio
@respx.mock
async def test_the_taker_share_is_computed_and_an_empty_window_is_none(
    adapter: BinanceMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``04`` §2 gives ``buyVol`` and ``sellVol`` and **no symbol** on this
    endpoint, so the unified name comes from the request. Nobody trading is an
    unknown share, not a zero one — ``0`` would read as "everybody sold"."""
    _route(respx, REST_TAKER_LONG_SHORT_RATIO, binance_payload("taker_long_short_ratio"))
    points = await adapter.fetch_ls_ratio(
        admission=admission, kind=LsRatioKind.TAKER_LONG_SHORT, symbol="BTC"
    )
    assert points[0].symbol == "BTC"
    buy, sell = Decimal("387.3300"), Decimal("248.5100")
    assert points[0].long_share == buy / (buy + sell)
    assert points[1].long_share is None


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_klines_drop_the_bar_that_is_still_forming(
    admission: Admission[Priority, Grant], ledger: RateLimitLedger
) -> None:
    """A partial bar's close is not a close. Writing it puts a number that is
    about to change into a row F8 later counts as a sample."""
    from hlens_core.adapters.base import KlineInterval

    adapter = BinanceMarketDataAdapter(
        client=httpx.AsyncClient(),
        endpoints=ENDPOINTS,
        symbols=_symbols(),
        clock=lambda: T0 - MIN,  # the third bar has not closed yet
    )
    _route(respx, REST_KLINES, binance_payload("klines"))

    records = await adapter.fetch_klines(
        admission=admission, symbol="BTC", interval=KlineInterval.M1, limit=3
    )

    assert len(records) == 2
    assert [r.mark for r in records] == [Decimal("64180.20"), Decimal("64210.30")]
    assert all(r.semantic is Semantic.CANDLE_CLOSE for r in records)
    assert all(r.grid_s == 60 for r in records)
    # 03 §5's third upsert statement writes these, never the live ones.
    assert all(r.backfilled for r in records)
    # limit=3 → the cheapest rung of 04 §2's ladder.
    assert ledger.snapshot("binance:fapi_weight").used_opportunistic_per_min == 1


@pytest.mark.asyncio
@respx.mock
async def test_funding_history_keeps_the_native_interval(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    route = _route(respx, REST_FUNDING_RATE, binance_payload("funding_rate"))
    records = await adapter.fetch_funding_history(admission=admission, symbol="PEPE", limit=3)

    assert route.calls[0].request.url.params["symbol"] == "1000PEPEUSDT"
    assert len(records) == 3
    assert records[0].funding_rate == Decimal("-0.00005000")
    assert records[0].funding_interval_h == 4
    assert records[0].ts == T0 - 8 * HOUR
    assert records[0].grid_s == 4 * 3600
    # Its own request bucket, and no weight.
    assert ledger.snapshot("binance:funding_rate").used_resident_per_min == 1
    assert ledger.snapshot("binance:fapi_weight").used_resident_per_min == 0


@pytest.mark.asyncio
@respx.mock
async def test_funding_history_can_be_run_on_the_opportunistic_tier(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    _route(respx, REST_FUNDING_RATE, binance_payload("funding_rate"))
    await adapter.fetch_funding_history(
        admission=admission, symbol="PEPE", priority=LanePriority.OPPORTUNISTIC
    )
    assert ledger.snapshot("binance:funding_rate").used_opportunistic_per_min == 1


# --------------------------------------------------------------------------- #
# Admission, refusals and errors
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_a_denied_allowance_sends_nothing(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
    clock: FakeClock,
) -> None:
    """``03`` §6.1: a 418 bans the shared egress IP. The ledger's whole job is to
    answer before the request, so a denial must not be followed by one."""
    route = _route(respx, REST_OPEN_INTEREST, binance_payload("open_interest"))
    ledger.observe_response("binance:fapi_weight", status=418, retry_after_s=120)

    with pytest.raises(AdmissionDenied):
        await adapter.fetch_open_interest(admission=admission, symbols=["BTC"])
    assert route.call_count == 0
    assert clock.now_ms() >= 0


@pytest.mark.asyncio
@respx.mock
async def test_a_429_is_settled_with_its_status_and_body(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """The status goes to the ledger **before** the exception reaches the
    caller: a retry loop that swallowed it would keep knocking on a shared IP."""
    respx.get(f"{ENDPOINTS.rest}{REST_PREMIUM_INDEX}").mock(
        return_value=httpx.Response(
            429, json={"code": -1003, "msg": "Too many requests"}, headers={"Retry-After": "61"}
        )
    )
    before = ledger.snapshot("binance:fapi_weight").resident_factor

    with pytest.raises(BinanceApiError) as caught:
        await adapter.fetch_mark_prices(admission=admission)

    assert caught.value.status == 429
    assert ledger.snapshot("binance:fapi_weight").resident_factor < before


@pytest.mark.asyncio
@respx.mock
async def test_a_451_says_which_status_it_was(
    adapter: BinanceMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``04`` §6: ``fapi`` answers 451 from a US egress and the ``www`` mirror
    answers 200 on the same paths. The caller needs the status to tell that
    apart from a ban; the adapter does not switch hosts by itself."""
    respx.get(f"{ENDPOINTS.rest}{REST_PREMIUM_INDEX}").mock(return_value=httpx.Response(451))
    with pytest.raises(BinanceApiError) as caught:
        await adapter.fetch_mark_prices(admission=admission)
    assert caught.value.status == 451


@pytest.mark.asyncio
@respx.mock
async def test_a_transport_failure_hands_the_allowance_back(
    adapter: BinanceMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """The call never happened, so the weight is not ours to keep — otherwise a
    flapping network silently eats the budget a minute at a time."""
    respx.get(f"{ENDPOINTS.rest}{REST_PREMIUM_INDEX}").mock(
        side_effect=httpx.ConnectError("no route")
    )
    with pytest.raises(httpx.ConnectError):
        await adapter.fetch_mark_prices(admission=admission)
    assert ledger.snapshot("binance:fapi_weight").used_fast_per_min == 0


@pytest.mark.asyncio
async def test_an_unsupported_capability_costs_nothing(
    adapter: BinanceMarketDataAdapter,
) -> None:
    """On a shared egress a pointless request is somebody else's 429, so the
    refusal happens before anything is spent."""
    for capability in (Capability.TRADE_STREAM, Capability.BOOK_L2, Capability.SPOT):
        with pytest.raises(UnsupportedCapability):
            adapter.capabilities.require(capability)
        with pytest.raises(UnsupportedCapability):
            adapter.stream_plan(capability)


@pytest.mark.asyncio
@respx.mock
async def test_the_used_weight_header_is_exposed_for_preflight(
    adapter: BinanceMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``04`` §7 asks preflight to calibrate the local ledger against
    ``X-MBX-USED-WEIGHT-1M``. The adapter reads it and stops there — writing to
    the ledger is not its job (seam ③)."""
    assert adapter.last_used_weight_1m is None
    _route(
        respx,
        REST_PREMIUM_INDEX,
        binance_payload("premium_index"),
        **{"X-MBX-USED-WEIGHT-1M": "377"},
    )
    await adapter.fetch_mark_prices(admission=admission)
    assert adapter.last_used_weight_1m == 377


@pytest.mark.asyncio
async def test_an_unmapped_symbol_is_a_lookup_error_not_a_request(
    adapter: BinanceMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    with pytest.raises(KeyError):
        await adapter.fetch_open_interest(admission=admission, symbols=["DOGE"])


def test_the_adapter_is_binance_and_nothing_else(adapter: BinanceMarketDataAdapter) -> None:
    assert adapter.venue is Venue.BINANCE
    assert not hasattr(adapter, "fetch_positions")  # seam ⑤: no wallet methods
    assert not hasattr(adapter, "fetch_fills")


def test_the_config_the_tests_read_is_the_repositorys_own() -> None:
    LedgerConfig.load(VENUES_PATH, VENUES_PATH.parent / "egress-consumers.yaml")
