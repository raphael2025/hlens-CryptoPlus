"""Seam ① — the normalized data contracts (``docs/03-ARCHITECTURE.md`` §2).

The only thing every module is allowed to import (seam ③). Adapters return
these models, never raw exchange JSON; the persistence layer writes them into
tables; export reads them. Prices, sizes and rates are ``Decimal``; timestamps
are UTC millisecond integers; every record carries ``venue`` ``symbol`` ``ts``
``ingest_ts`` ``source`` and ``semantic``; unknown is ``None`` and never ``0``.

M1 scope
--------
:class:`MarketRecord` (the ``market_1m`` row, both lane groups),
:class:`LsRatioPoint` (the ``ls_ratio`` point) and
:class:`InstrumentRecord` / :class:`CoinUniverseRecord` (F1's universe).

Liquidation, trade, order-book, spot and wallet contracts belong to M2, M4 and
M5 and are deliberately absent — declaring them early would mean declaring
their completeness semantics before the data that constrains them exists.

Read :mod:`hlens_core.contracts.base` before adding a field: it explains why
``ts`` here is an observation instant while ``market_1m.ts`` is a minute
bucket, and why no bucketing happens in this package.
"""

from __future__ import annotations

from .base import (
    CROSS_VENUE,
    DecimalValue,
    HlensRecord,
    IntervalHours,
    NonNegativeDecimal,
    Semantic,
    SingleVenueRecord,
    SourceTag,
    Symbol,
    TimestampMs,
    UnitShare,
    Venue,
    VenueSymbol,
)
from .ls_ratio import LsRatioKind, LsRatioPoint
from .market import NORMALIZED_FUNDING_INTERVAL_H, GridSeconds, MarketRecord
from .universe import CoinUniverseRecord, InstrumentRecord, InstrumentStatus

__all__ = [
    "CROSS_VENUE",
    "NORMALIZED_FUNDING_INTERVAL_H",
    "CoinUniverseRecord",
    "DecimalValue",
    "GridSeconds",
    "HlensRecord",
    "InstrumentRecord",
    "InstrumentStatus",
    "IntervalHours",
    "LsRatioKind",
    "LsRatioPoint",
    "MarketRecord",
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
