"""Seam ① — the rules the contracts exist to enforce, one test each.

Nothing here touches the network; M1-A1 is entirely offline, so there are no
fixtures and no ``@pytest.mark.live`` tests in this file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hlens_core.contracts import (
    CoinUniverseRecord,
    InstrumentRecord,
    InstrumentStatus,
    LsRatioKind,
    LsRatioPoint,
    MarketRecord,
    Semantic,
    Venue,
)

# A minute-aligned instant and a deliberately un-aligned one: 2026-09-19
# 12:34:00.000 UTC and the same minute plus 37.219 s.
MINUTE_TS = 1_789_734_840_000
OBSERVED_TS = MINUTE_TS + 37_219
INGEST_TS = OBSERVED_TS + 12


def _fast_lane(**overrides: object) -> MarketRecord:
    kwargs: dict[str, object] = {
        "venue": Venue.HYPERLIQUID,
        "symbol": "BTC",
        "ts": OBSERVED_TS,
        "ingest_ts": INGEST_TS,
        "source": "hyperliquid_rest_meta_and_asset_ctxs",
        "semantic": Semantic.MARK_PRICE,
        "mark": "64123.5",
        "funding_rate": "0.0000125",
        "funding_interval_h": 1,
        "obs_ts_fast": OBSERVED_TS,
    }
    kwargs.update(overrides)
    return MarketRecord(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The 8-hour funding rate is computed, and None-safe
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "interval_h", "expected"),
    [
        # Hyperliquid settles hourly: ×8.
        ("0.0000125", 1, "0.0001"),
        # Binance's default interval is already the normalized one.
        ("0.0001", 8, "0.0001"),
        # Binance's 4-hour symbols: ×2.
        ("0.00005", 4, "0.0001"),
        # Negative funding stays negative — the sign is the information.
        ("-0.0000125", 1, "-0.0001"),
        # Zero is a real observation, not a missing one.
        ("0", 1, "0"),
    ],
)
def test_funding_rate_8h_converts_from_the_native_interval(
    raw: str, interval_h: int, expected: str
) -> None:
    record = _fast_lane(funding_rate=raw, funding_interval_h=interval_h)
    assert record.funding_rate_8h == Decimal(expected)
    # The stored value is the venue's own raw number, untouched.
    assert record.funding_rate == Decimal(raw)
    assert record.funding_interval_h == interval_h


@pytest.mark.parametrize(
    ("raw", "interval_h"),
    [(None, 1), ("0.0000125", None), (None, None)],
)
def test_funding_rate_8h_is_none_when_either_input_is_unknown(
    raw: str | None, interval_h: int | None
) -> None:
    record = _fast_lane(
        funding_rate=raw,
        funding_interval_h=interval_h,
        # keep the lane non-empty even when both funding fields are None
        mark="64123.5",
    )
    assert record.funding_rate_8h is None


def test_funding_rate_8h_is_not_a_stored_field() -> None:
    """It must not appear in ``model_dump()``: §5 says it 不入库."""
    dumped = _fast_lane().model_dump()
    assert "funding_rate_8h" not in dumped
    assert "funding_rate" in dumped


def test_a_zero_funding_interval_cannot_be_constructed() -> None:
    with pytest.raises(ValidationError):
        _fast_lane(funding_interval_h=0)


# --------------------------------------------------------------------------- #
# Decimal, never float
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["mark", "index_px", "premium", "funding_rate"])
def test_float_is_refused_for_a_decimal_field(field: str) -> None:
    with pytest.raises(ValidationError, match="float is not allowed"):
        _fast_lane(**{field: 64123.5})


def test_the_venues_raw_string_is_accepted_exactly() -> None:
    """A string keeps every digit the venue sent; a float would not."""
    record = _fast_lane(mark="0.000000012345678901234567")
    assert record.mark == Decimal("0.000000012345678901234567")


# --------------------------------------------------------------------------- #
# UTC millisecond integers, never datetime
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 9, 19, 12, 34, tzinfo=UTC),
        1_789_734_840.0,
        "1789734840000",
        True,
    ],
)
def test_timestamps_must_be_plain_integers(value: object) -> None:
    with pytest.raises(ValidationError, match="UTC millisecond integers"):
        _fast_lane(ts=value)


# --------------------------------------------------------------------------- #
# Trap 1: the contract does no minute bucketing
# --------------------------------------------------------------------------- #
def test_the_contract_does_no_bucketing() -> None:
    """``ts`` survives verbatim; truncation belongs to the persistence boundary.

    If this ever starts returning ``MINUTE_TS``, the contracts have taken over a
    job that §5 assigns to M1-C/M1-E, and the raw observation instant is gone.
    """
    record = _fast_lane(ts=OBSERVED_TS, obs_ts_fast=OBSERVED_TS)
    assert record.ts == OBSERVED_TS
    assert record.ts % 60_000 != 0
    assert record.obs_ts_fast == OBSERVED_TS


# --------------------------------------------------------------------------- #
# Trap 2: unknown is None, never 0 and never 1
# --------------------------------------------------------------------------- #
def test_unobserved_market_fields_default_to_none_not_zero() -> None:
    record = _fast_lane()
    for field in ("index_px", "premium", "oi_base", "oi_usd", "vol24h_usd", "chg24h_pct"):
        assert getattr(record, field) is None, field


def test_an_unknown_multiplier_stays_none_and_never_defaults_to_one() -> None:
    instrument = InstrumentRecord(
        venue=Venue.BINANCE,
        symbol="PEPE",
        ts=OBSERVED_TS,
        ingest_ts=INGEST_TS,
        source="binance_rest_exchange_info",
        venue_symbol="1000PEPEUSDT",
    )
    assert instrument.mult is None
    assert instrument.tick is None
    assert instrument.funding_interval_h is None
    assert instrument.status is None


# --------------------------------------------------------------------------- #
# Every record carries the five mandatory fields, plus semantic
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["venue", "symbol", "ts", "ingest_ts", "source"])
def test_the_mandatory_fields_are_mandatory(field: str) -> None:
    kwargs = {
        "venue": Venue.BINANCE,
        "symbol": "BTC",
        "ts": OBSERVED_TS,
        "ingest_ts": INGEST_TS,
        "source": "binance_rest_premium_index",
        "semantic": Semantic.MARK_PRICE,
        "obs_ts_fast": OBSERVED_TS,
    }
    del kwargs[field]
    with pytest.raises(ValidationError):
        MarketRecord(**kwargs)  # type: ignore[arg-type]


def test_a_market_record_must_state_its_semantic() -> None:
    with pytest.raises(ValidationError):
        MarketRecord(
            venue=Venue.BINANCE,
            symbol="BTC",
            ts=OBSERVED_TS,
            ingest_ts=INGEST_TS,
            source="binance_rest_premium_index",
            obs_ts_fast=OBSERVED_TS,
        )  # type: ignore[call-arg]


def test_records_without_a_price_pin_semantic_to_none() -> None:
    point = LsRatioPoint(
        venue=Venue.BINANCE,
        symbol="BTC",
        ts=OBSERVED_TS,
        ingest_ts=INGEST_TS,
        source="binance_futures_data_global_long_short_account_ratio",
        kind=LsRatioKind.GLOBAL_LONG_SHORT_ACCOUNT,
        long_share="0.7871",
        period=300,
    )
    assert point.semantic is None
    # Pinned, not defaulted: a caller cannot attach a misleading value.
    with pytest.raises(ValidationError):
        LsRatioPoint(
            venue=Venue.BINANCE,
            symbol="BTC",
            ts=OBSERVED_TS,
            ingest_ts=INGEST_TS,
            source="binance_futures_data_global_long_short_account_ratio",
            semantic=Semantic.MARK_PRICE,
            kind=LsRatioKind.GLOBAL_LONG_SHORT_ACCOUNT,
            long_share="0.7871",
            period=300,
        )  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Records are frozen and closed
# --------------------------------------------------------------------------- #
def test_records_are_frozen_and_reject_unknown_fields() -> None:
    record = _fast_lane()
    with pytest.raises(ValidationError):
        record.mark = Decimal("1")  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _fast_lane(funding_rate_8h="0.0001")


# --------------------------------------------------------------------------- #
# Lane discipline (§5's three independent write statements)
# --------------------------------------------------------------------------- #
def test_a_lane_payload_without_its_observation_instant_is_refused() -> None:
    with pytest.raises(ValidationError, match="obs_ts_slow"):
        _fast_lane(oi_usd="123456789")


def test_a_record_with_no_lane_at_all_is_refused() -> None:
    with pytest.raises(ValidationError, match="at least one lane"):
        MarketRecord(
            venue=Venue.BINANCE,
            symbol="BTC",
            ts=OBSERVED_TS,
            ingest_ts=INGEST_TS,
            source="binance_rest_premium_index",
            semantic=Semantic.MARK_PRICE,
        )


def test_a_slow_lane_record_carries_only_the_slow_group() -> None:
    record = MarketRecord(
        venue=Venue.BINANCE,
        symbol="ETH",
        ts=OBSERVED_TS,
        ingest_ts=INGEST_TS,
        source="binance_rest_open_interest",
        semantic=Semantic.MARK_PRICE,
        oi_base="1234.5",
        oi_usd="4000000",
        obs_ts_slow=OBSERVED_TS,
    )
    assert record.obs_ts_fast is None
    assert record.mark is None
    assert record.backfilled is False


# --------------------------------------------------------------------------- #
# Symbols and the cross-venue marker
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("symbol", ["btc", "BTC-PERP", "", "TOOLONGSYMBOL1234", "BTC USDT"])
def test_the_unified_symbol_alphabet_is_enforced(symbol: str) -> None:
    with pytest.raises(ValidationError):
        _fast_lane(symbol=symbol)


def test_the_unified_symbol_accepts_the_thousand_prefixed_names() -> None:
    assert _fast_lane(symbol="1000PEPE").symbol == "1000PEPE"


def test_a_single_venue_observation_cannot_claim_the_cross_venue_marker() -> None:
    with pytest.raises(ValidationError, match="cross-venue marker"):
        _fast_lane(venue=Venue.CROSS)


def test_universe_membership_is_a_cross_venue_record() -> None:
    segment = CoinUniverseRecord(
        symbol="BTC",
        ts=OBSERVED_TS,
        ingest_ts=INGEST_TS,
        source="universe_daily_reconcile",
        listed=True,
    )
    assert segment.venue is Venue.CROSS
    assert segment.reason is None
    with pytest.raises(ValidationError):
        CoinUniverseRecord(
            venue=Venue.BINANCE,
            symbol="BTC",
            ts=OBSERVED_TS,
            ingest_ts=INGEST_TS,
            source="universe_daily_reconcile",
            listed=True,
        )  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# ls_ratio: three kinds, a share in [0, 1]
# --------------------------------------------------------------------------- #
def test_there_are_exactly_three_ls_ratio_kinds() -> None:
    assert {kind.value for kind in LsRatioKind} == {
        "global_long_short_account",
        "top_long_short_position",
        "taker_long_short",
    }


@pytest.mark.parametrize("share", ["-0.01", "1.01", "78.71"])
def test_long_share_is_a_fraction_not_a_percentage(share: str) -> None:
    with pytest.raises(ValidationError):
        LsRatioPoint(
            venue=Venue.BINANCE,
            symbol="BTC",
            ts=OBSERVED_TS,
            ingest_ts=INGEST_TS,
            source="binance_futures_data_global_long_short_account_ratio",
            kind=LsRatioKind.TAKER_LONG_SHORT,
            long_share=share,
            period=300,
        )


def test_an_unobserved_long_share_must_be_stated_as_none() -> None:
    point = LsRatioPoint(
        venue=Venue.BINANCE,
        symbol="BTC",
        ts=OBSERVED_TS,
        ingest_ts=INGEST_TS,
        source="binance_futures_data_taker_long_short_ratio",
        kind=LsRatioKind.TAKER_LONG_SHORT,
        long_share=None,
        period=300,
    )
    assert point.long_share is None
    # ... and it cannot simply be left out, so nobody can forget to say so.
    with pytest.raises(ValidationError):
        LsRatioPoint(
            venue=Venue.BINANCE,
            symbol="BTC",
            ts=OBSERVED_TS,
            ingest_ts=INGEST_TS,
            source="binance_futures_data_taker_long_short_ratio",
            kind=LsRatioKind.TAKER_LONG_SHORT,
            period=300,
        )  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Instruments keep the venue's own name verbatim (F3)
# --------------------------------------------------------------------------- #
def test_the_venue_contract_name_survives_normalization() -> None:
    binance = InstrumentRecord(
        venue=Venue.BINANCE,
        symbol="1000PEPE",
        ts=OBSERVED_TS,
        ingest_ts=INGEST_TS,
        source="binance_rest_exchange_info",
        venue_symbol="1000PEPEUSDT",
        mult="1000",
        funding_interval_h=8,
        tick="0.0000001",
        status=InstrumentStatus.TRADING,
    )
    hyperliquid = InstrumentRecord(
        venue=Venue.HYPERLIQUID,
        symbol="1000PEPE",
        ts=OBSERVED_TS,
        ingest_ts=INGEST_TS,
        source="hyperliquid_rest_meta",
        venue_symbol="kPEPE",
        mult="1000",
        funding_interval_h=1,
        status=InstrumentStatus.TRADING,
    )
    assert binance.symbol == hyperliquid.symbol == "1000PEPE"
    assert (binance.venue_symbol, hyperliquid.venue_symbol) == ("1000PEPEUSDT", "kPEPE")
    assert (binance.funding_interval_h, hyperliquid.funding_interval_h) == (8, 1)
