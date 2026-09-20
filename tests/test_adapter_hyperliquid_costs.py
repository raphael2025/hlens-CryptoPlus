"""The weight table, and the one bucket it charges.

Every number here comes from ``04`` §3. What the tests are really pinning is
the reasoning around them: this venue sends **no rate-limit header of any
kind**, so the local count is the only account there is, and the egress is
shared with a still-running collector of this same venue.
"""

from __future__ import annotations

import pytest

from hlens_core.adapters import (
    Capability,
    LanePriority,
    UnsupportedCapability,
)
from hlens_core.adapters.hyperliquid import (
    BUCKET_INFO_WEIGHT,
    HyperliquidCall,
    candle_snapshot_weight,
    cost_of,
    cost_of_call,
    funding_history_weight,
    stream_plan_for,
)


def test_every_call_charges_the_one_info_weight_bucket() -> None:
    """Unlike Binance there is no second request bucket: Hyperliquid meters one
    REST surface. The bucket name is spelled exactly as ``config/venues.yaml``
    names it, which is what preflight checks before anything is sent."""
    for call in HyperliquidCall:
        cost = cost_of_call(call, symbols=None if _is_market_wide(call) else 1)
        assert cost.bucket == BUCKET_INFO_WEIGHT == "hyperliquid:info_weight"
        assert cost.weight >= 1


def _is_market_wide(call: HyperliquidCall) -> bool:
    return call in (
        HyperliquidCall.META,
        HyperliquidCall.META_AND_ASSET_CTXS,
        HyperliquidCall.PREDICTED_FUNDINGS,
    )


def test_the_snapshot_endpoints_cost_twenty() -> None:
    """``04`` §3: "其余文档化请求一律 20"."""
    for call in (
        HyperliquidCall.META,
        HyperliquidCall.META_AND_ASSET_CTXS,
        HyperliquidCall.PREDICTED_FUNDINGS,
    ):
        assert cost_of_call(call).weight == 20


def test_the_fast_lane_is_forty_weight_a_minute() -> None:
    """``03`` §6's lane table: "快道 30 s | HL ``metaAndAssetCtxs``（W=20）| HL
    权重 | 40" — two calls a minute at 20 each, and **one** call answers mark,
    funding, open interest and the 24 h ticker together."""
    cost = cost_of_call(HyperliquidCall.META_AND_ASSET_CTXS)
    assert cost.weight * 2 == 40
    assert cost.priority is LanePriority.FAST_LANE
    for capability in (
        Capability.MARK_PRICE,
        Capability.FUNDING_RATE,
        Capability.OPEN_INTEREST,
        Capability.TICKER_24H,
    ):
        assert cost_of(capability).endpoint == cost.endpoint


def test_there_is_no_per_symbol_form_of_a_market_wide_endpoint() -> None:
    """The mirror image of Binance's ``openInterest``, which has only the
    per-symbol form. Charging a market-wide call as if a subset were cheaper is
    how a lane gets sized on a number that is not true."""
    for call in (
        HyperliquidCall.META,
        HyperliquidCall.META_AND_ASSET_CTXS,
        HyperliquidCall.PREDICTED_FUNDINGS,
    ):
        with pytest.raises(ValueError, match="no per-symbol form"):
            cost_of_call(call, symbols=1)


def test_funding_history_is_charged_by_the_row_and_not_by_the_call() -> None:
    """``04`` §3: "历史类每 20 条 +1". The weight moves with the row count, and
    the flat 20 is **not** added on top — ``03`` §6.1 sizes F11's two-year
    backfill at 157,680 weight, which is exactly
    ``(2 x 365 x 24 / 20) x 180 coins`` and leaves no room for a per-call
    constant (the other reading gives ~284,000). That arithmetic is the only
    thing in either document that settles the ambiguity."""
    assert funding_history_weight(1) == 1
    assert funding_history_weight(20) == 1
    assert funding_history_weight(21) == 2
    assert funding_history_weight(500) == 25
    assert funding_history_weight(500) != funding_history_weight(20)

    rows_per_coin = 2 * 365 * 24
    assert funding_history_weight(rows_per_coin) * 180 == 157_680


def test_candles_are_charged_per_sixty_and_not_per_twenty() -> None:
    """``04`` §3: ``candleSnapshot`` 每 60 条 +1 — a different divisor from
    every other historical endpoint, and the kind of detail that is only ever
    wrong in one direction."""
    assert candle_snapshot_weight(60) == 1
    assert candle_snapshot_weight(61) == 2
    assert candle_snapshot_weight(500) == 9
    assert candle_snapshot_weight(120) < funding_history_weight(120)


def test_an_omitted_row_count_is_charged_at_the_response_cap() -> None:
    """An unbounded time range asks for as many rows as the venue will give,
    and a ranged response returns up to 500 (``04`` §3). An omitted bound is
    not a cheap call, and the ledger must not find that out afterwards."""
    assert funding_history_weight(None) == funding_history_weight(500)
    assert candle_snapshot_weight(None) == candle_snapshot_weight(500)


def test_zero_rows_is_refused_rather_than_charged_as_free() -> None:
    with pytest.raises(ValueError):
        funding_history_weight(0)
    with pytest.raises(ValueError):
        candle_snapshot_weight(0)


def test_the_backfill_endpoints_run_on_the_opportunistic_tier() -> None:
    """决定 A7: backfill gives way to the resident lanes entirely. ``03`` §6.1
    sizes F11's two-year Hyperliquid funding backfill at ~5.5 days under the
    transitional 20 weight/min hard cap — long, resumable, and by design."""
    for call in (HyperliquidCall.FUNDING_HISTORY, HyperliquidCall.CANDLE_SNAPSHOT):
        assert cost_of_call(call, symbols=1).priority is LanePriority.OPPORTUNISTIC


def test_a_caller_can_move_funding_history_onto_a_resident_tier() -> None:
    """One endpoint, two tiers, same price: the caller says which, and the
    adapter does not guess it from the arguments."""
    cost = cost_of_call(
        HyperliquidCall.FUNDING_HISTORY, symbols=1, rows=20, priority=LanePriority.RESIDENT
    )
    assert cost.priority is LanePriority.RESIDENT


def test_the_request_type_travels_in_the_endpoint_string() -> None:
    """One path serves every endpoint on this venue, so a log line that said
    only ``/info`` would not say what was called. And it is a path, never a
    URL: :class:`CallCost` refuses a scheme."""
    cost = cost_of_call(HyperliquidCall.META)
    assert cost.endpoint is not None
    assert cost.endpoint.startswith("/info (type=meta")
    assert "://" not in cost.endpoint


def test_an_unpublished_capability_has_no_cost_at_all() -> None:
    """"It is free" and "it does not exist" are different answers, and only one
    of them means do not call. On this venue the second is the common one."""
    for capability in (
        Capability.LONG_SHORT_RATIO,
        Capability.TAKER_RATIO,
        Capability.LIQUIDATION_STREAM,
        Capability.TRADE_STREAM,
        Capability.BOOK_L2,
        Capability.SPOT,
        Capability.MARK_PRICE_STREAM,
    ):
        with pytest.raises(UnsupportedCapability):
            cost_of(capability)


def test_the_ratio_refusal_says_no_substitute_is_derived() -> None:
    with pytest.raises(UnsupportedCapability, match="no substitute"):
        cost_of(Capability.LONG_SHORT_RATIO)


def test_the_stream_plan_asks_for_one_subscription_on_one_group() -> None:
    """``03`` §6.1's table budgets exactly 1 subscription for M1/M2, and the
    seats are zero-sum with the legacy collector (``04`` §4)."""
    plan = stream_plan_for(Capability.MARK_PRICE_STREAM)
    assert plan.group == "info"
    assert plan.streams == 1
    assert plan.priority is LanePriority.FAST_LANE


def test_there_is_no_liquidation_stream_to_plan_for() -> None:
    with pytest.raises(UnsupportedCapability, match="wallet sampling"):
        stream_plan_for(Capability.LIQUIDATION_STREAM)


def test_all_mids_has_no_per_symbol_subscription() -> None:
    with pytest.raises(ValueError, match="whole market in one subscription"):
        stream_plan_for(Capability.MARK_PRICE_STREAM, symbols=3)
