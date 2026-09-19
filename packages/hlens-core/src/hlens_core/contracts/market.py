"""The normalized market observation — seam ① for the ``market_1m`` row (F2, F3).

Two lanes, one row
------------------
§5 splits ``market_1m`` into two column groups written by two independent
statements, and says the two statements must never be merged:

* **fast lane** (30 s): ``mark`` ``index_px`` ``premium`` ``funding_rate``
  ``funding_interval_h`` ``next_funding_ts`` ``obs_ts_fast``
* **slow lane** (60 s): ``oi_base`` ``oi_usd`` ``vol24h_usd`` ``chg24h_pct``
  ``obs_ts_slow``

A single :class:`MarketRecord` therefore usually carries **one** lane, and the
other lane's fields are ``None``. That is why almost every field here is
optional, and why ``None`` means "not observed in this record" — never zero.
§5's general rule is "未知写 NULL，绝不写 0"; a zero open interest and an
unobserved open interest are different facts, and F5 shows the second one grey
rather than as a number.

``ts`` is the observation instant, not the minute bucket
-------------------------------------------------------
See :mod:`hlens_core.contracts.base`. The persistence boundary (M1-C / M1-E)
computes ``market_1m.ts = date_trunc('minute', ts)`` and writes this record's
observation instant into ``obs_ts_fast`` / ``obs_ts_slow``. Nothing in this
module rounds, truncates or buckets anything.

The 8-hour funding rate is computed, not stored
-----------------------------------------------
§5's first "必须由数据结构本身回答的事" is explicit: the row stores the venue's
**native-interval raw value** ``funding_rate`` together with
``funding_interval_h``, and the normalized 8-hour figure is derived on read —
``funding_rate × 8 ÷ funding_interval_h``. That keeps F3's promise that the raw
value and the converted value are both available at all times, and it means a
correction to the conversion rule never leaves a table full of numbers computed
under the old one.

:attr:`MarketRecord.funding_rate_8h` is a plain ``@property`` and deliberately
not a pydantic ``computed_field``: ``model_dump()`` then yields exactly the
storable columns, so the derived value cannot drift into an INSERT.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Self

from pydantic import Field, model_validator

from .base import (
    DecimalValue,
    IntervalHours,
    NonNegativeDecimal,
    Semantic,
    SingleVenueRecord,
    TimestampMs,
)

__all__ = ["MarketRecord"]

#: Hours in the normalized funding period. F3: 8 hours is the one main basis.
NORMALIZED_FUNDING_INTERVAL_H = Decimal(8)

#: Observation grid in seconds: 60 for M1's own per-minute collection, 300 for
#: a 5-minute backfill grid (§5, F8). Unknown stays ``None`` — it is what makes
#: the provenance of ``n`` traceable, so it must never be guessed.
GridSeconds = Annotated[int, Field(gt=0)]

_FAST_LANE_PAYLOAD = (
    "mark",
    "index_px",
    "premium",
    "funding_rate",
    "funding_interval_h",
    "next_funding_ts",
)
_SLOW_LANE_PAYLOAD = ("oi_base", "oi_usd", "vol24h_usd", "chg24h_pct")


class MarketRecord(SingleVenueRecord):
    """One venue's observation of one coin, ready for the ``market_1m`` upsert."""

    semantic: Semantic
    """Narrowed from the base: a market observation always knows whether it is a
    mark price or a candle close."""

    # ---------------------------------------------------------------- fast lane
    mark: NonNegativeDecimal | None = None
    """Mark price, the venue's own."""

    index_px: DecimalValue | None = None
    """Index / oracle price the venue derives the mark from."""

    premium: DecimalValue | None = None
    """Premium of the perpetual over the index. Signed."""

    funding_rate: DecimalValue | None = None
    """**Raw** funding rate over the venue's native interval — Hyperliquid's
    hourly rate stays hourly here. Signed. Read
    :attr:`funding_rate_8h` for the normalized figure; never pre-convert."""

    funding_interval_h: IntervalHours | None = None
    """The venue's native funding interval in hours (HL 1, Binance 8, some
    Binance symbols 4). Without it ``funding_rate`` cannot be normalized, which
    is why :attr:`funding_rate_8h` returns ``None`` rather than assuming 8."""

    next_funding_ts: TimestampMs | None = None
    """When the venue settles funding next, UTC milliseconds."""

    obs_ts_fast: TimestampMs | None = None
    """Observation instant of the fast-lane group, UTC milliseconds. §5's
    fast-lane upsert guard compares on this column, so a fast-lane record
    without it cannot be written at all."""

    # ---------------------------------------------------------------- slow lane
    oi_base: NonNegativeDecimal | None = None
    """Open interest in base units."""

    oi_usd: NonNegativeDecimal | None = None
    """Open interest in USD."""

    vol24h_usd: NonNegativeDecimal | None = None
    """Rolling 24 h quote volume in USD."""

    chg24h_pct: DecimalValue | None = None
    """Rolling 24 h price change, in percent. Signed."""

    obs_ts_slow: TimestampMs | None = None
    """Observation instant of the slow-lane group, UTC milliseconds. §5's
    slow-lane upsert guard compares on this column."""

    # ------------------------------------------------------------- provenance
    grid_s: GridSeconds | None = None
    """Spacing of the grid this observation sits on, in seconds."""

    backfilled: bool = False
    """True only for rows produced by the M2 backfill (§5 writes them with a
    third, separate statement). Live collection never sets it, so ``False`` is a
    fact here rather than a stand-in for "unknown"."""

    @model_validator(mode="after")
    def _lane_payload_needs_its_observation_instant(self) -> Self:
        """A lane's payload without the lane's ``obs_ts_*`` is unwritable.

        §5's guards read ``excluded.obs_ts_fast > coalesce(market_1m.obs_ts_fast,
        '-infinity')``. With a ``NULL`` observation instant the comparison is
        ``NULL`` — false — and the write is silently dropped. Catching that here
        turns a vanished row into a validation error at the seam.
        """
        for lane, payload, obs_field in (
            ("fast", _FAST_LANE_PAYLOAD, "obs_ts_fast"),
            ("slow", _SLOW_LANE_PAYLOAD, "obs_ts_slow"),
        ):
            has_payload = any(getattr(self, name) is not None for name in payload)
            if has_payload and getattr(self, obs_field) is None:
                raise ValueError(
                    f"{lane}-lane fields are set but {obs_field} is None; "
                    "§5's upsert guard compares on it and would drop the row"
                )
        if self.obs_ts_fast is None and self.obs_ts_slow is None:
            raise ValueError(
                "a market record must carry at least one lane "
                "(set obs_ts_fast and/or obs_ts_slow)"
            )
        return self

    @property
    def funding_rate_8h(self) -> Decimal | None:
        """The funding rate normalized to 8 hours, or ``None`` if unknowable.

        ``funding_rate × 8 ÷ funding_interval_h``, entirely in ``Decimal``.
        Returns ``None`` whenever either input is missing: an unknown rate and a
        rate of zero are different facts, and inventing 8 as a default interval
        would silently turn Hyperliquid's hourly number into an 8-hourly one.
        """
        if self.funding_rate is None or self.funding_interval_h is None:
            return None
        return (
            self.funding_rate
            * NORMALIZED_FUNDING_INTERVAL_H
            / Decimal(self.funding_interval_h)
        )
