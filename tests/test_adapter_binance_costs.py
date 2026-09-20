"""Binance's weight table, and the three buckets it is not allowed to mix.

``04`` §2/§4 give three separate accounts and the reason they matter is in
``03`` §6: the weight bucket runs at 241 of 960 a minute while ``futures_data``
runs at 54 of 80 — so folding the ratios into the weight bucket would hide the
tightest resource in the system behind the roomiest one.

Seam ③ is the other half: nothing in ``adapters`` imports ``ratelimit``. The
ledger arrives as an :class:`~hlens_core.adapters.admission.Admission`, and
``tests/test_module_boundaries.py`` — untouched by this step — is the authority.
"""

from __future__ import annotations

import pytest

from hlens_core.adapters import Capability, LanePriority, UnsupportedCapability
from hlens_core.adapters.binance import (
    BUCKET_FAPI_WEIGHT,
    BUCKET_FUNDING_RATE,
    BUCKET_FUTURES_DATA,
    BinanceCall,
    cost_of,
    cost_of_call,
    kline_weight,
    stream_plan_for,
)
from hlens_core.adapters.binance.endpoints import WS_GROUP_MARKET
from hlens_core.contracts import LsRatioKind
from hlens_core.ratelimit import LedgerConfig


# --------------------------------------------------------------------------- #
# The bucket names are the config's, not the adapter's invention
# --------------------------------------------------------------------------- #
def test_every_bucket_the_adapter_charges_exists_in_venues_yaml(config: LedgerConfig) -> None:
    """Adapters quote ``config/venues.yaml``'s bucket names; preflight (M1-B) is
    where a name that does not exist is caught before a request is sent."""
    known = {str(key) for key in config.buckets}
    for bucket in (BUCKET_FAPI_WEIGHT, BUCKET_FUTURES_DATA, BUCKET_FUNDING_RATE):
        assert bucket in known, f"{bucket} is not a bucket in config/venues.yaml"


def test_every_call_charges_a_real_bucket_and_at_least_one_unit(
    config: LedgerConfig,
) -> None:
    known = {str(key) for key in config.buckets}
    for call in BinanceCall:
        symbols = 1 if call in _NEEDS_A_SYMBOL else None
        cost = cost_of_call(call, symbols=symbols)
        assert cost.bucket in known
        assert cost.weight >= 1
        assert cost.endpoint is not None and "://" not in cost.endpoint


_NEEDS_A_SYMBOL = {BinanceCall.OPEN_INTEREST}


# --------------------------------------------------------------------------- #
# 04 §2's numbers
# --------------------------------------------------------------------------- #
def test_premium_index_is_ten_for_the_market_and_one_for_a_symbol() -> None:
    whole = cost_of_call(BinanceCall.PREMIUM_INDEX)
    assert (whole.bucket, whole.weight) == (BUCKET_FAPI_WEIGHT, 10)
    one = cost_of_call(BinanceCall.PREMIUM_INDEX, symbols=1)
    assert (one.bucket, one.weight) == (BUCKET_FAPI_WEIGHT, 1)


def test_ticker_24h_is_forty_for_the_market_and_one_for_a_symbol() -> None:
    assert cost_of_call(BinanceCall.TICKER_24H).weight == 40
    assert cost_of_call(BinanceCall.TICKER_24H, symbols=1).weight == 1


def test_open_interest_refuses_the_market_wide_form() -> None:
    """``04`` §2: ``symbol`` 必填. There is no whole-market open interest on
    Binance, which is exactly why this endpoint grows with the coin count."""
    with pytest.raises(ValueError, match="exactly one symbol"):
        cost_of_call(BinanceCall.OPEN_INTEREST)


def test_an_endpoint_that_takes_one_symbol_refuses_a_batch() -> None:
    """Several symbols are several calls, each with its own grant. Charging one
    grant of N would leave the ledger unable to pace or partially deny them."""
    with pytest.raises(ValueError, match="at most one symbol"):
        cost_of_call(BinanceCall.PREMIUM_INDEX, symbols=7)


@pytest.mark.parametrize(
    ("limit", "weight"),
    [(1, 1), (99, 1), (100, 2), (499, 2), (500, 5), (1000, 5), (1001, 10), (1500, 10)],
)
def test_the_kline_weight_ladder(limit: int, weight: int) -> None:
    """``04`` §2: ``<100→1, <500→2, ≤1000→5, >1000→10``."""
    assert kline_weight(limit) == weight
    assert cost_of_call(BinanceCall.KLINES, rows=limit).weight == weight


def test_an_omitted_kline_limit_is_charged_at_binances_own_default() -> None:
    """An omitted parameter still spends: Binance defaults ``limit`` to 500,
    which is 5 weight, and finding that out from a 429 is the expensive way."""
    assert kline_weight(None) == 5
    assert cost_of_call(BinanceCall.KLINES).weight == 5


# --------------------------------------------------------------------------- #
# The separate request buckets
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", list(LsRatioKind))
def test_every_ratio_kind_charges_one_request_on_futures_data(kind: LsRatioKind) -> None:
    """``04`` §2: ``/futures/data/*`` charges **no weight** — no
    ``X-MBX-USED-WEIGHT`` header at all — and is metered in requests against its
    own limit (``03`` §6: 54 of 80 a minute, the tightest bucket we have)."""
    from hlens_core.adapters.binance import call_of_ls_ratio_kind

    cost = cost_of_call(call_of_ls_ratio_kind(kind))
    assert cost.bucket == BUCKET_FUTURES_DATA
    assert cost.weight == 1
    assert cost.priority is LanePriority.RESIDENT


def test_there_are_exactly_three_ratio_kinds() -> None:
    """``03`` §5 records three; ``04`` §1/§2 lists a fourth endpoint
    (``topLongShortAccountRatio``) that no confirmed feature consumes. See the
    PR's "Doc corrections"."""
    assert len(LsRatioKind) == 3


def test_funding_rate_and_funding_info_share_their_own_third_bucket() -> None:
    """``04`` §2: ``fundingRate`` / ``fundingInfo`` have their own 500 req/5min
    limit. ``04`` §11 第 10 项 leaves "is it the same counter as futures_data?"
    unverified, and ``config/venues.yaml`` answers by keeping them separate and
    pressing this bucket's opportunistic cap down to 5."""
    for call in (BinanceCall.FUNDING_INFO, BinanceCall.FUNDING_RATE_HISTORY):
        cost = cost_of_call(call)
        assert cost.bucket == BUCKET_FUNDING_RATE
        assert cost.weight == 1


def test_discovery_charges_two_different_buckets() -> None:
    """``fetch_instruments`` is two calls, and they are not the same account."""
    assert cost_of_call(BinanceCall.EXCHANGE_INFO).bucket == BUCKET_FAPI_WEIGHT
    assert cost_of_call(BinanceCall.FUNDING_INFO).bucket == BUCKET_FUNDING_RATE


# --------------------------------------------------------------------------- #
# Lanes and tiers (03 §6, §6.1)
# --------------------------------------------------------------------------- #
def test_only_the_price_lane_is_the_fast_lane() -> None:
    """``03`` §6.1's fast-lane floor of 120 weight/min is fenced off from every
    other resident lane, so claiming it for anything else would spend a
    protection somebody else is relying on."""
    fast = {
        call
        for call in BinanceCall
        if cost_of_call(
            call, symbols=1 if call in _NEEDS_A_SYMBOL else None
        ).priority
        is LanePriority.FAST_LANE
    }
    assert fast == {BinanceCall.PREMIUM_INDEX}


def test_klines_are_the_only_opportunistic_call() -> None:
    """``03`` §6.1: "Binance 侧的 opportunistic 只有 K 线回补"."""
    opportunistic = {
        call
        for call in BinanceCall
        if cost_of_call(
            call, symbols=1 if call in _NEEDS_A_SYMBOL else None
        ).priority
        is LanePriority.OPPORTUNISTIC
    }
    assert opportunistic == {BinanceCall.KLINES}


def test_the_funding_history_tier_can_be_overridden_by_the_caller() -> None:
    """One endpoint, two tiers at the same price: ``03`` §6's lane table runs
    ``fundingRate`` as an 8-hour reconciliation (resident) and ``04`` §5 names
    the same endpoint as the cold-start backfill (opportunistic). The caller
    says which; the adapter does not guess from the arguments."""
    assert (
        cost_of_call(BinanceCall.FUNDING_RATE_HISTORY).priority is LanePriority.RESIDENT
    )
    assert (
        cost_of_call(
            BinanceCall.FUNDING_RATE_HISTORY, priority=LanePriority.OPPORTUNISTIC
        ).priority
        is LanePriority.OPPORTUNISTIC
    )


# --------------------------------------------------------------------------- #
# cost_of, the protocol method
# --------------------------------------------------------------------------- #
def test_cost_of_refuses_a_capability_with_no_rest_call() -> None:
    """"It is free" and "it does not exist" are different answers and only one
    of them means do not call."""
    for capability in (Capability.MARK_PRICE_STREAM, Capability.LIQUIDATION_STREAM):
        with pytest.raises(UnsupportedCapability, match="no REST call"):
            cost_of(capability)


def test_mark_price_and_funding_rate_are_the_same_single_call() -> None:
    """``03`` §6's lane table budgets **one** 10-weight ``premiumIndex`` call per
    30 seconds, not two — the response answers both capabilities."""
    assert cost_of(Capability.MARK_PRICE) == cost_of(Capability.FUNDING_RATE)


# --------------------------------------------------------------------------- #
# Streams
# --------------------------------------------------------------------------- #
def test_both_streams_are_on_the_market_group() -> None:
    """``04`` §2's 2026-04-23 change notice: ``!markPrice@arr`` and
    ``!forceOrder@arr`` are both on ``/market``; ``@depth`` is on ``/public``
    and the two can never share a connection."""
    for capability in (Capability.MARK_PRICE_STREAM, Capability.LIQUIDATION_STREAM):
        plan = stream_plan_for(capability)
        assert plan.group == WS_GROUP_MARKET
        assert plan.streams == 1


def test_the_mark_price_stream_is_the_fast_lane_and_liquidations_are_resident() -> None:
    assert stream_plan_for(Capability.MARK_PRICE_STREAM).priority is LanePriority.FAST_LANE
    assert stream_plan_for(Capability.LIQUIDATION_STREAM).priority is LanePriority.RESIDENT


def test_per_symbol_mark_price_costs_one_stream_each() -> None:
    assert stream_plan_for(Capability.MARK_PRICE_STREAM, symbols=21).streams == 21


def test_the_liquidation_stream_has_no_per_symbol_form() -> None:
    with pytest.raises(ValueError, match="whole market in one stream"):
        stream_plan_for(Capability.LIQUIDATION_STREAM, symbols=3)


def test_stream_plan_refuses_a_capability_that_is_not_a_stream() -> None:
    with pytest.raises(UnsupportedCapability):
        stream_plan_for(Capability.OPEN_INTEREST)
