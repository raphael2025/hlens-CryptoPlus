"""Every network-facing REST function, offline, against a trimmed fixture.

AGENTS §2.6: "every network function has an offline test with a trimmed
recorded fixture". The fixtures here are tagged ``source: documented`` rather
than ``live-recorded`` because AGENTS §3.3 requires a recording to be made from
the **production host's egress** (task ``M1-G``) — this development machine
shares that egress with a still-running collector **of this same venue**, and
M1-A6 sent no request of any kind. The field names come from ``04`` §3's table.

``respx`` intercepts at the transport layer, so the adapter builds the real URL
out of the real ``config/venues.yaml`` and nothing leaves the process.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from conftest import VENUES_PATH, hyperliquid_fixture_source, hyperliquid_payload
from hlens_core.adapters import (
    Admission,
    Capability,
    LanePriority,
    SymbolTable,
    UnsupportedCapability,
)
from hlens_core.adapters.base import KlineInterval
from hlens_core.adapters.hyperliquid import (
    SOURCE_META_AND_ASSET_CTXS,
    AdmissionDenied,
    HyperliquidApiError,
    HyperliquidEndpoints,
    HyperliquidMarketDataAdapter,
    normalize_meta,
    symbol_table_of,
)
from hlens_core.contracts import LsRatioKind, Semantic, Venue
from hlens_core.ratelimit import Grant, Priority, RateLimitLedger

#: The fixtures' own instant: 2026-09-19T12:00:00Z.
T0 = 1_789_819_200_000
MIN = 60_000
HOUR = 3_600_000

ENDPOINTS = HyperliquidEndpoints.load(VENUES_PATH)
INFO_URL = ENDPOINTS.info_url()


def _symbols() -> SymbolTable:
    return symbol_table_of(
        normalize_meta(hyperliquid_payload("meta"), ingest_ts=T0, observed_ts=T0)
    )


@pytest.fixture
def admission(ledger: RateLimitLedger) -> Admission[Priority, Grant]:
    """The real ledger, injected structurally. Seam ③: the adapter never
    imports it, and translating the two priority vocabularies is the one lambda
    the caller owns."""
    return Admission(ledger, lambda lane: Priority(lane.value))


@pytest.fixture
def adapter() -> HyperliquidMarketDataAdapter:
    return HyperliquidMarketDataAdapter(
        client=httpx.AsyncClient(),
        endpoints=ENDPOINTS,
        symbols=_symbols(),
        clock=lambda: T0,
    )


def _route(payload: object, **headers: str) -> respx.Route:
    return respx.post(INFO_URL).mock(
        return_value=httpx.Response(200, json=payload, headers=headers)
    )


# --------------------------------------------------------------------------- #
# Provenance of the fixtures themselves
# --------------------------------------------------------------------------- #
def test_every_fixture_declares_where_it_came_from() -> None:
    """AGENTS §3.3. ``documented`` is not ``live-recorded`` and this step may
    not pretend otherwise — the dev machine shares the production egress with a
    collector of this very venue, where a 429 is somebody else's problem too."""
    for name in (
        "meta",
        "meta_and_asset_ctxs",
        "funding_history",
        "predicted_fundings",
        "candle_snapshot",
        "ws_all_mids",
    ):
        assert hyperliquid_fixture_source(name) == "documented"


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_fetch_instruments_is_one_call_and_one_bucket(
    adapter: HyperliquidMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """Binance needs two calls against two buckets (``exchangeInfo`` +
    ``fundingInfo``). Hyperliquid needs one, because there are no funding
    interval exceptions to look up — every perpetual funds hourly."""
    route = _route(hyperliquid_payload("meta"))

    records = await adapter.fetch_instruments(admission=admission)

    assert route.call_count == 1
    assert route.calls[0].request.method == "POST"
    assert ledger.snapshot("hyperliquid:info_weight").used_resident_per_min == 20

    assert [r.venue_symbol for r in records] == ["BTC", "kPEPE", "HYPE"]
    btc, pepe, _hype = records
    assert btc.venue is Venue.HYPERLIQUID and btc.symbol == "BTC"
    assert btc.mult == Decimal(1)
    assert pepe.symbol == "PEPE" and pepe.venue_symbol == "kPEPE"
    assert pepe.mult == Decimal(1000)
    # Every symbol, hourly. No default of 8 anywhere on this venue.
    assert all(r.funding_interval_h == 1 for r in records)
    # 04 §3 publishes no status vocabulary and no price tick: unknown is None.
    assert all(r.status is None and r.tick is None for r in records)
    # Not contract fields: the SCD-2 bounds belong to the persistence boundary.
    assert "valid_from" not in btc.model_dump()


@pytest.mark.asyncio
@respx.mock
async def test_instrument_ts_is_our_own_clock_because_the_venue_sends_none(
    admission: Admission[Priority, Grant],
) -> None:
    """``04`` §3's ``meta`` field list has no timestamp at all. ``ts`` is the
    observation instant, so when the venue dates nothing the instant we saw it
    is the only one there is — and it is still not a bucket."""
    adapter = HyperliquidMarketDataAdapter(
        client=httpx.AsyncClient(),
        endpoints=ENDPOINTS,
        symbols=_symbols(),
        clock=lambda: T0 + 7,
    )
    _route(hyperliquid_payload("meta"))
    records = await adapter.fetch_instruments(admission=admission)
    assert records[0].ts == T0 + 7
    assert records[0].ingest_ts == T0 + 7


@pytest.mark.asyncio
@respx.mock
async def test_sz_decimals_and_max_leverage_are_exposed_but_not_invented_into_columns(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``04`` §3 names both as key fields of ``meta``; ``03`` §5's
    ``instruments`` has a column for neither. They are carried on the adapter
    rather than parked in a column that means something else — and the MMR
    approximation ``1/(2·maxLeverage)`` is **not** computed here."""
    _route(hyperliquid_payload("meta"))
    records = await adapter.fetch_instruments(admission=admission)

    metas = {m.venue_symbol: m for m in adapter.asset_metadata}
    assert metas["BTC"].sz_decimals == 5
    assert metas["kPEPE"].max_leverage == 10
    stored = records[0].model_dump()
    assert "sz_decimals" not in stored and "max_leverage" not in stored


# --------------------------------------------------------------------------- #
# The one call that answers four capabilities
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_one_call_answers_four_capabilities(
    adapter: HyperliquidMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """``03`` §6's lane table budgets 40 weight a minute for this venue's whole
    fast lane: two 20-weight calls, not eight. The record carries both of ``03``
    §5's live column groups because both really were observed at one instant."""
    route = _route(hyperliquid_payload("meta_and_asset_ctxs"))

    records = await adapter.fetch_mark_prices(admission=admission)

    assert route.call_count == 1
    assert route.calls[0].request.method == "POST"
    assert ledger.snapshot("hyperliquid:info_weight").used_fast_per_min == 20

    assert [r.symbol for r in records] == ["BTC", "PEPE"]
    btc = records[0]
    assert btc.mark == Decimal("64210.3")
    assert btc.index_px == Decimal("64198.0")
    assert btc.funding_rate == Decimal("0.0000125")
    assert btc.oi_base == Decimal("12345.678")
    assert btc.vol24h_usd == Decimal("9812345678.12")
    assert btc.obs_ts_fast == T0 and btc.obs_ts_slow == T0
    assert btc.semantic is Semantic.MARK_PRICE
    assert btc.source == SOURCE_META_AND_ASSET_CTXS


@pytest.mark.asyncio
@respx.mock
async def test_premium_is_a_real_observation_on_this_venue(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """The one column where the two adapters differ in kind. ``04`` §2's Binance
    ``premiumIndex`` publishes no premium and that adapter writes ``None``
    rather than synthesising ``mark − index``; ``04`` §3 lists ``premium`` among
    this endpoint's own fields, so here it is the venue's number."""
    _route(hyperliquid_payload("meta_and_asset_ctxs"))
    records = await adapter.fetch_mark_prices(admission=admission)

    btc, pepe = records
    assert btc.premium == Decimal("0.00018")
    assert pepe.premium == Decimal("-0.00005")
    # And it is the venue's own figure, not mark - index computed here.
    assert btc.premium != btc.mark - btc.index_px  # type: ignore[operator]


@pytest.mark.asyncio
@respx.mock
async def test_funding_is_hourly_and_is_never_pre_converted(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``03`` §5: the row stores the venue's raw value plus
    ``funding_interval_h``; the 8-hour figure is derived on read and never
    stored. A rate that had been multiplied by 8 here would be multiplied
    again downstream."""
    _route(hyperliquid_payload("meta_and_asset_ctxs"))
    records = await adapter.fetch_funding(admission=admission)

    btc = records[0]
    assert btc.funding_rate == Decimal("0.0000125")  # raw, hourly
    assert btc.funding_interval_h == 1
    assert btc.funding_rate_8h == Decimal("0.0000125") * 8
    assert "funding_rate_8h" not in btc.model_dump()
    # metaAndAssetCtxs carries no next settlement time; rounding the clock up
    # to the next hour would be a schedule, not an observation.
    assert btc.next_funding_ts is None


@pytest.mark.asyncio
@respx.mock
async def test_open_interest_is_converted_into_the_coins_own_units(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """Hyperliquid quotes open interest in units of the listed contract, so
    ``kPEPE``'s 1,250,000 is 1.25 billion PEPE. Without the multiplier an
    ``oi_share`` across two venues compares two different units."""
    _route(hyperliquid_payload("meta_and_asset_ctxs"))
    records = await adapter.fetch_open_interest(admission=admission)

    pepe = records[1]
    assert pepe.oi_base == Decimal("1250000.0") * 1000
    # markPx x openInterest would be exact here — both arrive in this one
    # response — but it is still a computation, and 03 §4 gives those to
    # `compute`. An adapter that multiplies observations together produces rows
    # nobody can tell from measurements.
    assert pepe.oi_usd is None


@pytest.mark.asyncio
@respx.mock
async def test_the_24h_change_is_unknown_rather_than_zero(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``04`` §3's field list has ``dayNtlVlm`` and no previous-day price. ``03``
    §5: 未知写 NULL，绝不写 0 — a 0 there would read as "the price is
    unchanged"."""
    _route(hyperliquid_payload("meta_and_asset_ctxs"))
    records = await adapter.fetch_ticker_24h(admission=admission)
    assert records[0].vol24h_usd == Decimal("9812345678.12")
    assert all(r.chg24h_pct is None for r in records)


@pytest.mark.asyncio
@respx.mock
async def test_an_unknown_coin_is_skipped_and_counted(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """A coin listed since the last universe reconciliation is an ordinary
    event, not an exception in the middle of a market-wide response."""
    _route(hyperliquid_payload("meta_and_asset_ctxs"))
    records = await adapter.fetch_mark_prices(admission=admission)
    assert all(r.symbol != "NEWCOIN" for r in records)
    assert adapter.unknown_venue_symbols == ("NEWCOIN",)


@pytest.mark.asyncio
@respx.mock
async def test_a_context_array_of_the_wrong_length_is_refused(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """The two halves are paired **by position** and no context repeats the coin
    name. Zipping a mismatch would attach every coin's price to the next coin's
    name, silently, across the whole market."""
    payload = hyperliquid_payload("meta_and_asset_ctxs")
    payload[1].pop()
    _route(payload)
    with pytest.raises(ValueError, match="paired by position"):
        await adapter.fetch_mark_prices(admission=admission)


@pytest.mark.asyncio
async def test_asking_for_a_subset_is_refused_rather_than_charged_as_cheaper(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``04`` §3: this endpoint has no per-symbol form. Accepting a subset and
    filtering locally would let a caller size a lane on a price that is not
    real — the call costs the same 20 either way."""
    with pytest.raises(ValueError, match="no per-symbol form"):
        await adapter.fetch_mark_prices(admission=admission, symbols=["BTC"])


# --------------------------------------------------------------------------- #
# Funding, forward and back
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_predicted_fundings_never_emits_another_venues_record(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``04`` §3: this response carries other exchanges' predictions, Binance's
    included. A Hyperliquid adapter returning a Binance record would be one
    adapter claiming to observe two venues — well-formed, plausible, and not
    ours to make."""
    _route(hyperliquid_payload("predicted_fundings"))
    records = await adapter.fetch_predicted_fundings(admission=admission)

    assert all(r.venue is Venue.HYPERLIQUID for r in records)
    assert [r.symbol for r in records] == ["BTC", "PEPE"]
    assert records[0].funding_rate == Decimal("0.0000138")
    assert records[0].funding_interval_h == 1
    # This is the one endpoint that publishes a real next settlement time.
    assert records[0].next_funding_ts == T0 + HOUR
    # The skipped entry is counted, not silently dropped: 04 §3 names none of
    # the per-venue keys, so M1-G's recording is what confirms the spelling.
    assert adapter.other_venue_keys == ("BinPerp",)


@pytest.mark.asyncio
@respx.mock
async def test_funding_history_lands_on_the_one_hour_grid(
    adapter: HyperliquidMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """``04`` §5: Hyperliquid settles on a 1-hour grid and Binance on an 8-hour
    one. ``grid_s`` is what keeps F8 from pooling two different populations
    into one percentile."""
    route = _route(hyperliquid_payload("funding_history"))
    records = await adapter.fetch_funding_history(
        admission=admission, symbol="PEPE", start_ms=T0 - 4 * HOUR, rows=3
    )

    body = route.calls[0].request.read().decode()
    assert '"coin": "kPEPE"' in body or '"coin":"kPEPE"' in body
    assert "fundingHistory" in body

    assert len(records) == 3
    assert [r.ts for r in records] == [T0 - 3 * HOUR, T0 - 2 * HOUR, T0 - HOUR]
    assert all(r.grid_s == 3600 for r in records)
    assert all(r.funding_interval_h == 1 for r in records)
    assert all(r.backfilled for r in records)
    # 04 §3 lists a premium on these rows too, and it is the venue's own.
    assert records[0].premium == Decimal("-0.00005")
    # 1 per 20 rows, so three rows is one weight — on the opportunistic tier
    # (决定 A7: backfill gives way to the resident lanes entirely).
    assert ledger.snapshot("hyperliquid:info_weight").used_opportunistic_per_min == 1


@pytest.mark.asyncio
@respx.mock
async def test_funding_history_can_be_run_on_a_resident_tier(
    adapter: HyperliquidMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    _route(hyperliquid_payload("funding_history"))
    await adapter.fetch_funding_history(
        admission=admission,
        symbol="PEPE",
        start_ms=T0 - 4 * HOUR,
        rows=3,
        priority=LanePriority.RESIDENT,
    )
    assert ledger.snapshot("hyperliquid:info_weight").used_resident_per_min == 1


@pytest.mark.asyncio
async def test_asking_for_more_rows_than_a_page_holds_is_refused(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``04`` §3: a ranged response returns at most 500 elements, and the venue
    does not say so — it just returns 500. A caller that asked for 2000 would
    lose three quarters of a window and be charged for one page."""
    with pytest.raises(ValueError, match="at most 500"):
        await adapter.fetch_funding_history(
            admission=admission, symbol="PEPE", start_ms=T0 - HOUR, rows=2000
        )


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_candles_drop_the_bar_that_is_still_forming(
    admission: Admission[Priority, Grant], ledger: RateLimitLedger
) -> None:
    """A partial bar's close is not a close. Writing it puts a number that is
    about to change into a row F8 later counts as a sample."""
    adapter = HyperliquidMarketDataAdapter(
        client=httpx.AsyncClient(),
        endpoints=ENDPOINTS,
        symbols=_symbols(),
        clock=lambda: T0 - MIN,  # the third bar has not closed yet
    )
    route = _route(hyperliquid_payload("candle_snapshot"))

    records = await adapter.fetch_klines(
        admission=admission,
        symbol="BTC",
        interval=KlineInterval.M1,
        start_ms=T0 - 3 * MIN,
        limit=3,
    )

    body = route.calls[0].request.read().decode()
    assert "candleSnapshot" in body and "startTime" in body

    assert len(records) == 2
    assert [r.mark for r in records] == [Decimal("64180.2"), Decimal("64210.3")]
    assert all(r.semantic is Semantic.CANDLE_CLOSE for r in records)
    assert all(r.grid_s == 60 and r.backfilled for r in records)
    # 1 per 60 candles, so three bars is one weight.
    assert ledger.snapshot("hyperliquid:info_weight").used_opportunistic_per_min == 1


@pytest.mark.asyncio
@respx.mock
async def test_a_candle_without_a_close_time_falls_back_to_the_same_instant(
    admission: Admission[Priority, Grant],
) -> None:
    """``04`` §3's field list is ``t,o,h,l,c,v,n`` — it does not mention the
    close time the venue actually sends. The normalizer prefers ``T`` and falls
    back to the interval's own arithmetic rather than assuming a field the
    document does not promise (reported in "Doc corrections").

    What is pinned here is that the two paths agree **to the millisecond**: the
    fallback is ``t + grid − 1``, because the venue's ``T`` is the last
    millisecond inside the bar. A fallback that landed on the next bar's open
    would give one bar two different ``ts``, and ``03`` §5's upsert would store
    it twice.
    """
    from hlens_core.adapters.hyperliquid import normalize_candle_snapshot

    payload = hyperliquid_payload("candle_snapshot")
    with_close_time = normalize_candle_snapshot(
        payload, symbol="BTC", interval="1m", ingest_ts=T0, now_ms=T0 - MIN
    )
    for bar in payload:
        bar.pop("T")
    without = normalize_candle_snapshot(
        payload, symbol="BTC", interval="1m", ingest_ts=T0, now_ms=T0 - MIN
    )
    assert without == with_close_time
    assert [r.ts for r in without] == [T0 - 2 * MIN - 1, T0 - MIN - 1]


@pytest.mark.asyncio
async def test_candles_require_a_start_instant(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """``04`` §3: the request is ``req{coin, interval, startTime, endTime}`` and
    there is no ``limit`` parameter to send. A silent default range would make
    the charged row count a guess."""
    with pytest.raises(ValueError, match="start instant is required"):
        await adapter.fetch_klines(
            admission=admission, symbol="BTC", interval=KlineInterval.M1
        )


# --------------------------------------------------------------------------- #
# The four refusals
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_a_ratio_request_raises_and_sends_nothing(
    adapter: HyperliquidMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """``04`` §1 / AGENTS §3.4. The refusal comes out of the capability sheet
    before anything is acquired: on a shared egress IP a pointless request is
    somebody else's 429."""
    route = respx.post(INFO_URL)
    for kind in LsRatioKind:
        with pytest.raises(UnsupportedCapability):
            await adapter.fetch_ls_ratio(admission=admission, kind=kind, symbol="BTC")
    assert route.call_count == 0
    assert ledger.snapshot("hyperliquid:info_weight").used_resident_per_min == 0


@pytest.mark.asyncio
async def test_an_unsupported_capability_has_no_stream_plan_and_no_cost(
    adapter: HyperliquidMarketDataAdapter,
) -> None:
    for capability in (
        Capability.LONG_SHORT_RATIO,
        Capability.TAKER_RATIO,
        Capability.LIQUIDATION_STREAM,
        Capability.TRADE_STREAM,
        Capability.BOOK_L2,
        Capability.SPOT,
    ):
        with pytest.raises(UnsupportedCapability):
            adapter.capabilities.require(capability)
        with pytest.raises(UnsupportedCapability):
            adapter.stream_plan(capability)


# --------------------------------------------------------------------------- #
# Admission, refusals and errors
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_a_denied_allowance_sends_nothing(
    adapter: HyperliquidMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """The ledger's whole job is to answer before the request, so a denial must
    not be followed by one."""
    route = _route(hyperliquid_payload("meta_and_asset_ctxs"))
    ledger.observe_response("hyperliquid:info_weight", status=418, retry_after_s=120)

    with pytest.raises(AdmissionDenied):
        await adapter.fetch_mark_prices(admission=admission)
    assert route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_a_429_hands_the_body_to_the_ledger_verbatim(
    adapter: HyperliquidMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """``04`` §3: Hyperliquid sends no rate-limit header at all, so a 429's
    **body type** is the only evidence of which limiter fired — a JSON ``null``
    is the weight limiter. The adapter passes it through and classifies
    nothing; the remedy, the AIMD cut and ``03`` §6.1's mandatory F4 private
    message all belong to the ledger."""
    respx.post(INFO_URL).mock(return_value=httpx.Response(429, text="null"))
    before = ledger.snapshot("hyperliquid:info_weight").resident_factor

    with pytest.raises(HyperliquidApiError) as caught:
        await adapter.fetch_mark_prices(admission=admission)

    assert caught.value.status == 429
    assert caught.value.body == "null"
    snapshot = ledger.snapshot("hyperliquid:info_weight")
    assert snapshot.resident_factor < before
    assert snapshot.opportunistic_frozen


@pytest.mark.asyncio
@respx.mock
async def test_the_two_429_body_types_are_told_apart_by_the_ledger(
    adapter: HyperliquidMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """The other half of ``04`` §3's measurement: an nginx HTML page means the
    **connection-rate** limiter, which needs fewer connections rather than less
    weight. They need opposite remedies, so the adapter must not merge them
    into "a 429"."""
    from hlens_core.ratelimit import HlBodyKind, classify_hl_429_body

    page = "<html><head><title>429 Too Many Requests</title></head></html>"
    respx.post(INFO_URL).mock(return_value=httpx.Response(429, text=page))

    with pytest.raises(HyperliquidApiError) as caught:
        await adapter.fetch_mark_prices(admission=admission)

    assert classify_hl_429_body(caught.value.body) is HlBodyKind.CONNECTION
    assert classify_hl_429_body("null") is HlBodyKind.WEIGHT
    assert ledger.snapshot("hyperliquid:info_weight").used_fast_per_min >= 0


@pytest.mark.asyncio
@respx.mock
async def test_a_truncated_html_body_still_classifies(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    """The body is trimmed before it reaches a log line, so the trimming must
    not destroy the one bit of information it carries."""
    from hlens_core.ratelimit import HlBodyKind, classify_hl_429_body

    respx.post(INFO_URL).mock(return_value=httpx.Response(429, text="<html>" + "x" * 5000))
    with pytest.raises(HyperliquidApiError) as caught:
        await adapter.fetch_mark_prices(admission=admission)
    assert len(caught.value.body) <= 512
    assert classify_hl_429_body(caught.value.body) is HlBodyKind.CONNECTION


@pytest.mark.asyncio
@respx.mock
async def test_a_transport_failure_hands_the_allowance_back(
    adapter: HyperliquidMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """The call never happened, so the weight is not ours to keep — otherwise a
    flapping network silently eats the budget a minute at a time."""
    respx.post(INFO_URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(httpx.ConnectError):
        await adapter.fetch_mark_prices(admission=admission)
    assert ledger.snapshot("hyperliquid:info_weight").used_fast_per_min == 0


@pytest.mark.asyncio
@respx.mock
async def test_a_full_backfill_page_does_not_fit_the_transitional_hard_cap(
    adapter: HyperliquidMarketDataAdapter,
    admission: Admission[Priority, Grant],
    ledger: RateLimitLedger,
) -> None:
    """A finding worth pinning, not a quirk of this test.

    ``04`` §3 caps a ranged response at 500 rows, which costs 25 weight; ``03``
    §6.1 presses this bucket's transitional opportunistic hard cap down to
    **20 weight a minute** on purpose. So a *full* page never fits and the
    ledger denies it outright rather than pacing it — F11's backfill has to
    page at ≤ 400 rows, which is exactly what makes §6.1's own "157,680 ÷ 20 ≈
    5.5 天" reachable. Reported in the PR.
    """
    route = _route(hyperliquid_payload("funding_history"))
    cap = ledger.snapshot("hyperliquid:info_weight").opportunistic_hard_cap_per_min
    assert cap == 20

    with pytest.raises(AdmissionDenied):
        await adapter.fetch_funding_history(
            admission=admission, symbol="PEPE", start_ms=T0 - 500 * HOUR, rows=500
        )
    assert route.call_count == 0

    # 400 rows is 20 weight, which is the largest page that fits.
    await adapter.fetch_funding_history(
        admission=admission, symbol="PEPE", start_ms=T0 - 400 * HOUR, rows=400
    )
    assert route.call_count == 1
    assert ledger.snapshot("hyperliquid:info_weight").used_opportunistic_per_min == 20


@pytest.mark.asyncio
async def test_an_unmapped_symbol_is_a_lookup_error_not_a_request(
    adapter: HyperliquidMarketDataAdapter, admission: Admission[Priority, Grant]
) -> None:
    with pytest.raises(KeyError):
        await adapter.fetch_funding_history(
            admission=admission, symbol="DOGE", start_ms=T0 - HOUR
        )


def test_the_adapter_is_hyperliquid_and_has_no_wallet_methods(
    adapter: HyperliquidMarketDataAdapter,
) -> None:
    """Seam ⑤, and it costs more here than on Binance: M5's wallet endpoints
    are Hyperliquid's and sit on this very ``/info`` path."""
    assert adapter.venue is Venue.HYPERLIQUID
    for name in dir(adapter):
        assert not any(
            word in name
            for word in ("wallet", "user_", "position", "fill", "clearinghouse", "ledger_update")
        ), name
