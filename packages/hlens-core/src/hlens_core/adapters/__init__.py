"""Seam ② — the market-data adapter protocol and its capability declarations.

``03`` §4: "一个所怎么访问、字段怎么归一、声明了什么能力 … 返回契约对象（不碰
库）". Four pieces, and each exists because of a named cost:

``base``
    The :class:`~hlens_core.adapters.base.MarketDataAdapter` protocol — AGENTS
    §3.4's method set, every method returning contract objects (seam ①).
``capabilities``
    ``supported`` / ``mode`` / ``completeness`` as three separate answers
    (``01`` §4.7), with ``trade_stream`` ``book_l2`` ``spot`` already in the
    enum and refused as supported until M4, with Hyperliquid's missing ratios
    and missing liquidation stream refused as supported at all, and with a
    liquidation completeness type that has no ``full`` in it.
``admission``
    How an adapter obeys the rate-limit ledger without importing it: the weight
    table stays on the adapter, the budget stays in ``ratelimit``, and the two
    meet through a structural protocol the ledger already satisfies. Seam ③
    holds with no new edge.
``symbols``
    The mapping both ways, carrying the quantity multiplier (``1000PEPE`` ↔
    ``kPEPE``) and the venue's native funding interval (AGENTS §3.5).

**No venue is implemented here.** ``binance/`` and ``hyperliquid/`` are M1-A
steps ⑤ and ⑥; this step is the protocol only, and nothing in this package
sends a request, opens a socket or names a host.

Wallet data is **not** here and never will be: it has its own protocol in
:mod:`hlens_core.wallet` from M5 (seam ⑤).
"""

from __future__ import annotations

from .admission import Admission, AnyAdmission, CallCost, LanePriority, SpendAuthority
from .base import KlineInterval, MarketDataAdapter, NormalizedRecord, StreamPlan
from .capabilities import (
    LIQUIDATION_CAPABILITIES,
    M4_CAPABILITIES,
    VENUE_MUST_DECLARE_UNSUPPORTED,
    AdapterVenue,
    Capability,
    CapabilityDeclaration,
    CapabilityError,
    CapabilitySet,
    Completeness,
    LiquidationCapabilityDeclaration,
    LiquidationCompleteness,
    MarketCapabilityDeclaration,
    Mode,
    Support,
    UnsupportedCapability,
    capability_of_ls_ratio_kind,
    unsupported,
)
from .symbols import SymbolMap, SymbolMapping, SymbolTable

__all__ = [
    "LIQUIDATION_CAPABILITIES",
    "M4_CAPABILITIES",
    "VENUE_MUST_DECLARE_UNSUPPORTED",
    "AdapterVenue",
    "Admission",
    "AnyAdmission",
    "CallCost",
    "Capability",
    "CapabilityDeclaration",
    "CapabilityError",
    "CapabilitySet",
    "Completeness",
    "KlineInterval",
    "LanePriority",
    "LiquidationCapabilityDeclaration",
    "LiquidationCompleteness",
    "MarketCapabilityDeclaration",
    "MarketDataAdapter",
    "Mode",
    "NormalizedRecord",
    "SpendAuthority",
    "StreamPlan",
    "Support",
    "SymbolMap",
    "SymbolMapping",
    "SymbolTable",
    "UnsupportedCapability",
    "capability_of_ls_ratio_kind",
    "unsupported",
]
