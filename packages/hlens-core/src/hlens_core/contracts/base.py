"""Shared building blocks of seam ① — the normalized data contracts.

``docs/03-ARCHITECTURE.md`` §2 seam ① fixes the rules these types encode:

* prices, sizes and rates are ``Decimal``, never ``float``;
* timestamps are **UTC millisecond integers**, never ``datetime``;
* every record carries ``venue`` · ``symbol`` · ``ts`` · ``ingest_ts`` ·
  ``source``, plus ``semantic``;
* unknown is ``None`` — §5's general rule is "未知写 NULL，绝不写 0".

What ``ts`` means here, and what it does *not* mean
---------------------------------------------------
``ts`` on a contract is **the observation instant of that record**: the venue's
own timestamp for the observation, in UTC milliseconds, exactly as observed.

``market_1m.ts`` in the database is a different thing — it is a **minute
bucket**, ``ts = date_trunc('minute', observation instant)``, and the raw
instants live only in ``obs_ts_fast`` / ``obs_ts_slow``. §5 says so in as many
words, and says what happens if the two are conflated: translating the contract
literally writes the exchange timestamp into ``market_1m.ts``, the primary key
``(venue, symbol, ts)`` degrades into "one row per observation", and the whole
overwrite semantics of the three independent write statements is void.

So: **the contracts do no bucketing.** ``ts`` is passed through untouched.
Truncation to the minute bucket happens at the persistence boundary (M1-C /
M1-E), which is also where ``obs_ts_fast`` / ``obs_ts_slow`` are filled from
the contract's own observation instants. Do not add a bucketing validator here.

The same split applies to every other column the persistence layer derives
rather than observes: the SCD-2 bounds ``valid_from`` / ``valid_to`` on
``instruments`` and the segment bounds ``in_from`` / ``in_to`` on
``coin_universe`` are assigned when a version is opened or closed, not by
whoever observed the instrument. They are not contract fields.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

__all__ = [
    "CROSS_VENUE",
    "DecimalValue",
    "HlensRecord",
    "IntervalHours",
    "NonNegativeDecimal",
    "Semantic",
    "SingleVenueRecord",
    "SourceTag",
    "Symbol",
    "TimestampMs",
    "UnitShare",
    "Venue",
    "VenueSymbol",
]


class Venue(StrEnum):
    """The venues, plus the cross-venue marker.

    ``x`` is not an exchange. It is the cross-venue value of §5's ``scope``
    syntax (``venue ∈ { binance | hyperliquid | x }``), and it is the only
    honest ``venue`` for a record that is about both venues at once — the
    ``coin_universe`` segment, which exists precisely because a coin is listed
    on both. Records that describe one venue's own observation reject it; see
    :class:`SingleVenueRecord`.
    """

    BINANCE = "binance"
    HYPERLIQUID = "hyperliquid"
    CROSS = "x"


CROSS_VENUE: Final = Venue.CROSS


class Semantic(StrEnum):
    """What the observed price actually *is*.

    Seam ① requires every record to state this, because the two answers are not
    interchangeable: a mark price is a venue-published index-derived number at
    an instant, a candle close is the last trade of a finished bar. §5 and
    ``docs/04-DATA-SOURCES.md`` §5 use ``candle_close`` for backfilled rows.
    """

    MARK_PRICE = "mark_price"
    CANDLE_CLOSE = "candle_close"


def _reject_float(value: Any) -> Any:
    """Refuse ``float`` before pydantic can quietly widen it into a Decimal.

    A price that has been through binary floating point has already lost the
    exchange's own digits, and no later conversion gets them back. Adapters
    pass the venue's raw string (or an int); that is exact.
    """
    if isinstance(value, float):
        raise ValueError(
            "float is not allowed for a Decimal field (seam ①); "
            "pass the venue's raw string or an int"
        )
    return value


def _require_int(value: Any) -> Any:
    """Refuse anything but a plain ``int`` for a timestamp.

    ``bool`` is an ``int`` in Python and a ``datetime`` is what seam ① exists to
    keep out, so both are named explicitly. Adapters convert once, at the edge.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            "timestamps are UTC millisecond integers (seam ①); "
            f"got {type(value).__name__}, convert it in the adapter"
        )
    return value


#: A ``Decimal`` that refuses ``float`` input and refuses inf/NaN.
DecimalValue = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(allow_inf_nan=False),
]

#: A ``DecimalValue`` that additionally cannot be negative (prices, sizes,
#: notionals, volumes). Rates and spreads are signed and use ``DecimalValue``.
NonNegativeDecimal = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(allow_inf_nan=False, ge=0),
]

#: A share of a whole, as a fraction in [0, 1] — never a percentage.
UnitShare = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(allow_inf_nan=False, ge=0, le=1),
]

#: UTC milliseconds since the epoch.
TimestampMs = Annotated[int, BeforeValidator(_require_int)]

#: A funding (or settlement) interval in whole hours: Hyperliquid 1, Binance 8,
#: some Binance symbols 4. ``gt=0`` is load-bearing — it is what keeps the
#: 8-hour normalization from dividing by zero.
IntervalHours = Annotated[int, Field(gt=0, le=24 * 7)]

#: The unified coin name (F3). Same alphabet as §5's ``scope`` CHECK, so a
#: symbol can be dropped straight into a scope string.
Symbol = Annotated[str, Field(pattern=r"^[A-Z0-9]{1,15}$")]

#: The venue's own contract name, kept verbatim for F3's "原始合约名常驻显示"
#: (Binance ``1000PEPEUSDT``, Hyperliquid ``kPEPE``).
VenueSymbol = Annotated[str, Field(pattern=r"^\S{1,40}$")]

#: Provenance of the record: which venue transport and endpoint produced it.
#: Free-form by design — the vocabulary belongs to the adapters (M1-A step ④),
#: not to the contracts. Convention: ``<venue>_<transport>_<endpoint>``, e.g.
#: ``binance_rest_premium_index``. Lower-case so it is greppable in logs.
SourceTag = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{0,63}$")]


class HlensRecord(BaseModel):
    """Base of every normalized record that crosses a module boundary.

    Frozen and ``extra="forbid"``: a record is an observation that already
    happened, and an unrecognised key is a normalization bug rather than a
    field to carry along.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    venue: Venue
    """Which venue this record is about; ``Venue.CROSS`` only where the record
    genuinely spans both (see :class:`Venue`)."""

    symbol: Symbol
    """Unified coin name (F3), not the venue's contract name."""

    ts: TimestampMs
    """Observation instant, UTC milliseconds. **Not** a minute bucket — see the
    module docstring."""

    ingest_ts: TimestampMs
    """When we received it, UTC milliseconds. Together with ``ts`` this is what
    makes staleness measurable instead of guessed."""

    source: SourceTag
    """Which transport and endpoint produced the record."""

    semantic: Semantic | None
    """What the observed price is (seam ①). Required — never defaulted, because
    guessing it is exactly the ambiguity the seam removes. Records with no price
    of their own pin it to ``None``; see :class:`~hlens_core.contracts.ls_ratio`
    and :class:`~hlens_core.contracts.universe`."""


class SingleVenueRecord(HlensRecord):
    """A record that is one venue's own observation.

    Rejects ``Venue.CROSS``: there is no such thing as a cross-venue mark price
    or a cross-venue instrument, and §4 of ``01-PRODUCT.md`` forbids the
    cross-venue aggregate that such a record would invite.
    """

    @field_validator("venue")
    @classmethod
    def _reject_cross_venue(cls, value: Venue) -> Venue:
        if value is Venue.CROSS:
            raise ValueError(
                f"{Venue.CROSS.value!r} is the cross-venue marker and cannot be "
                "the venue of a single-venue observation"
            )
        return value
