"""Normalized market observations (ADAPTER-RESEARCH §4.4)."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .base import Completeness, Observation, check_finite, check_non_negative


class Side(StrEnum):
    long = "long"
    short = "short"


class MarkPrice(Observation):
    mark: Decimal
    index: Decimal | None = None
    premium: Decimal | None = None

    @field_validator("mark", "index")
    @classmethod
    def _positive(cls, v: Decimal | None) -> Decimal | None:
        check_finite(v)
        if v is not None and v <= 0:
            raise ValueError("price must be > 0")
        return v

    @field_validator("premium")
    @classmethod
    def _finite(cls, v: Decimal | None) -> Decimal | None:
        return check_finite(v)


class FundingRate(Observation):
    """A funding observation, always carrying both the raw rate and the 8h-normalized one.

    ``rate`` is a decimal fraction over ``interval_h`` (0.0001 = 0.01% per period).
    ``rate_8h`` is ``rate * 8 / interval_h`` and is filled in automatically; it is
    ``semantic=normalized`` data, which is why the raw pair is kept beside it.
    """

    rate: Decimal
    interval_h: Decimal = Field(default=Decimal(8))
    rate_8h: Decimal | None = None
    next_ts: int | None = Field(
        default=None, description="next settlement, UTC ms; None when the venue omits it"
    )
    predicted: bool = False

    @field_validator("rate")
    @classmethod
    def _rate_finite(cls, v: Decimal) -> Decimal:
        check_finite(v)
        return v

    @field_validator("interval_h")
    @classmethod
    def _interval_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("interval_h must be > 0")
        return v

    @model_validator(mode="after")
    def _normalize_8h(self) -> FundingRate:
        if self.rate_8h is None:
            object.__setattr__(self, "rate_8h", self.rate * Decimal(8) / self.interval_h)
        return self

    @property
    def apr_pct(self) -> Decimal:
        """Annualized, assuming the current 8h-normalized rate persists (3 settlements/day)."""
        assert self.rate_8h is not None
        return self.rate_8h * Decimal(3 * 365) * Decimal(100)


class OpenInterest(Observation):
    """Open interest in base units and in USD.

    ``oi_usd`` from the venue is ``exchange_reported``; when the adapter computes it
    as ``oi_base * mark`` it must set ``semantic=derived`` (06 §2.3, §8.1).
    """

    oi_base: Decimal | None = None
    oi_usd: Decimal | None = None

    @field_validator("oi_base", "oi_usd")
    @classmethod
    def _non_negative(cls, v: Decimal | None) -> Decimal | None:
        return check_non_negative(v)

    @model_validator(mode="after")
    def _at_least_one(self) -> OpenInterest:
        if self.oi_base is None and self.oi_usd is None:
            raise ValueError("open interest needs oi_base or oi_usd; both null is a failed fetch")
        return self


class LSKind(StrEnum):
    """Long/short ratio flavours. Never averaged together across kinds (06 §2.4)."""

    account = "account"
    top_account = "top_account"
    top_position = "top_position"
    taker = "taker"


class LongShortRatio(Observation):
    kind: LSKind
    long_share: Decimal = Field(description="long side as a fraction of the total, 0..1")
    period: str | None = Field(default=None, description="venue sampling window, e.g. '5m'")
    sample_n: int | None = None

    @field_validator("long_share")
    @classmethod
    def _share_range(cls, v: Decimal) -> Decimal:
        check_finite(v)
        if not (0 <= v <= 1):
            raise ValueError(f"long_share must be in [0, 1], got {v}")
        return v

    @property
    def ratio(self) -> Decimal | None:
        """long/short ratio, the form most venues publish. None when all-short."""
        if self.long_share >= 1:
            return None
        return self.long_share / (Decimal(1) - self.long_share)


class Candle(Observation):
    """OHLCV bar. ``ts`` is the OPEN time; ``close_ts`` is the bar end."""

    tf: str = Field(description="timeframe token: 1m, 1h, 1d")
    close_ts: int
    o: Decimal
    h: Decimal
    low: Decimal = Field(alias="l")
    c: Decimal
    v: Decimal
    quote_v: Decimal | None = None
    taker_buy_v: Decimal | None = Field(
        default=None, description="taker buy volume in base units; None = venue does not report it"
    )
    trades: int | None = None
    closed: bool = True

    model_config = Observation.model_config | {"populate_by_name": True}

    @field_validator("o", "h", "low", "c")
    @classmethod
    def _prices(cls, v: Decimal) -> Decimal:
        check_finite(v)
        if v <= 0:
            raise ValueError("OHLC prices must be > 0")
        return v

    @field_validator("v", "quote_v", "taker_buy_v")
    @classmethod
    def _volumes(cls, v: Decimal | None) -> Decimal | None:
        return check_non_negative(v)

    @model_validator(mode="after")
    def _hl_order(self) -> Candle:
        if self.h < self.low:
            raise ValueError("high < low")
        if self.taker_buy_v is not None and self.taker_buy_v > self.v:
            raise ValueError("taker_buy_v > total volume")
        if self.close_ts <= self.ts:
            raise ValueError("close_ts must be after open ts")
        return self

    @property
    def taker_buy_share(self) -> Decimal | None:
        if self.taker_buy_v is None or self.v == 0:
            return None
        return self.taker_buy_v / self.v


class Liquidation(Observation):
    """One forced-liquidation print.

    ``side`` is the side of the POSITION that was liquidated (a sell forceOrder
    closes a long). Venues that throttle their stream -- Binance pushes at most one
    order per symbol per second -- must set ``throttled_source=True``; those rows
    are a lower bound and the aggregation layer is not allowed to forget that.
    """

    side: Side
    price: Decimal
    size: Decimal
    notional_usd: Decimal | None = None
    throttled_source: bool = False
    event_id: str | None = None
    completeness: Completeness = Completeness.lower_bound

    @field_validator("price")
    @classmethod
    def _price(cls, v: Decimal) -> Decimal:
        check_finite(v)
        if v <= 0:
            raise ValueError("price must be > 0")
        return v

    @field_validator("size", "notional_usd")
    @classmethod
    def _size(cls, v: Decimal | None) -> Decimal | None:
        return check_non_negative(v)

    @model_validator(mode="after")
    def _throttle_implies_lower_bound(self) -> Liquidation:
        if self.throttled_source and self.completeness is Completeness.full:
            raise ValueError("a throttled source can never be completeness=full")
        return self


class Ticker24h(Observation):
    last: Decimal | None = None
    chg24h_pct: Decimal | None = Field(default=None, description="percent, not fraction")
    vol24h_usd: Decimal | None = None
    high24h: Decimal | None = None
    low24h: Decimal | None = None

    @field_validator("last", "high24h", "low24h")
    @classmethod
    def _prices(cls, v: Decimal | None) -> Decimal | None:
        check_finite(v)
        if v is not None and v <= 0:
            raise ValueError("price must be > 0")
        return v

    @field_validator("vol24h_usd")
    @classmethod
    def _vol(cls, v: Decimal | None) -> Decimal | None:
        return check_non_negative(v)

    @field_validator("chg24h_pct")
    @classmethod
    def _chg(cls, v: Decimal | None) -> Decimal | None:
        return check_finite(v)
