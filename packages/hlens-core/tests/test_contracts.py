"""The normalization rules the whole pipeline depends on."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from hlens_core.contracts import (
    Candle,
    Completeness,
    FundingRate,
    Instrument,
    Liquidation,
    LongShortRatio,
    LSKind,
    MarkPrice,
    OpenInterest,
    Side,
    Ticker24h,
)

TS = 1789196919003


def test_funding_rate_normalizes_to_8h():
    f = FundingRate(
        venue="binance", symbol="BTC", ts=TS, source="/fapi/v1/premiumIndex",
        rate=Decimal("0.0001"), interval_h=Decimal(4),
    )
    assert f.rate_8h == Decimal("0.0002")
    assert f.rate == Decimal("0.0001")  # raw value survives normalization


def test_funding_rate_8h_interval_is_identity():
    f = FundingRate(venue="hl", symbol="BTC", ts=TS, source="x", rate=Decimal("0.00005"),
                    interval_h=Decimal(1))
    assert f.rate_8h == Decimal("0.0004")
    assert f.apr_pct == Decimal("0.0004") * 1095 * 100


def test_timestamps_must_be_milliseconds():
    with pytest.raises(ValidationError, match="milliseconds"):
        MarkPrice(venue="binance", symbol="BTC", ts=1789196919, source="x", mark=Decimal(1))


def test_long_share_bounds():
    with pytest.raises(ValidationError, match=r"\[0, 1\]"):
        LongShortRatio(venue="binance", symbol="BTC", ts=TS, source="x",
                       kind=LSKind.account, long_share=Decimal("1.4"))


def test_long_short_ratio_derives_ratio():
    r = LongShortRatio(venue="binance", symbol="BTC", ts=TS, source="x",
                       kind=LSKind.top_position, long_share=Decimal("0.5"))
    assert r.ratio == 1


def test_open_interest_rejects_all_null():
    # "both fields null" means the fetch failed; storing it would fake a zero.
    with pytest.raises(ValidationError, match="failed fetch"):
        OpenInterest(venue="binance", symbol="BTC", ts=TS, source="x")


def test_open_interest_rejects_negative():
    with pytest.raises(ValidationError):
        OpenInterest(venue="binance", symbol="BTC", ts=TS, source="x", oi_base=Decimal(-1))


def test_candle_rejects_taker_buy_above_volume():
    with pytest.raises(ValidationError, match="taker_buy_v"):
        Candle(venue="binance", symbol="BTC", ts=TS, source="x", tf="1m", close_ts=TS + 60000,
               o=Decimal(1), h=Decimal(2), l=Decimal(1), c=Decimal(2), v=Decimal(10),
               taker_buy_v=Decimal(11))


def test_candle_taker_buy_share():
    c = Candle(venue="binance", symbol="BTC", ts=TS, source="x", tf="1m", close_ts=TS + 60000,
               o=Decimal(1), h=Decimal(2), l=Decimal(1), c=Decimal(2), v=Decimal(10),
               taker_buy_v=Decimal(4))
    assert c.taker_buy_share == Decimal("0.4")


def test_throttled_liquidation_cannot_claim_full_completeness():
    with pytest.raises(ValidationError, match="throttled"):
        Liquidation(venue="binance", symbol="BTC", ts=TS, source="ws", side=Side.long,
                    price=Decimal(1), size=Decimal(1), throttled_source=True,
                    completeness=Completeness.full)


def test_liquidation_defaults_to_lower_bound():
    liq = Liquidation(venue="binance", symbol="BTC", ts=TS, source="ws", side=Side.short,
                      price=Decimal(100), size=Decimal(2))
    assert liq.completeness is Completeness.lower_bound


def test_instrument_rejects_zero_multiplier():
    with pytest.raises(ValidationError, match="multiplier"):
        Instrument(venue="binance", symbol="PEPE", venue_symbol="1000PEPEUSDT", ts=TS,
                   source="x", base="1000PEPE", quote="USDT", mult=Decimal(0))


def test_models_are_frozen_and_reject_unknown_fields():
    t = Ticker24h(venue="binance", symbol="BTC", ts=TS, source="x")
    with pytest.raises(ValidationError):
        t.last = Decimal(1)
    with pytest.raises(ValidationError):
        Ticker24h(venue="binance", symbol="BTC", ts=TS, source="x", nonsense=1)


def test_decimal_survives_json_round_trip():
    m = MarkPrice(venue="binance", symbol="BTC", ts=TS, source="x",
                  mark=Decimal("77293.97295352"))
    assert '"77293.97295352"' in m.model_dump_json()
