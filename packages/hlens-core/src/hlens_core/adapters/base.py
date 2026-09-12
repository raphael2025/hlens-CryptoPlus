"""The contract every venue adapter implements.

Read this file before writing a new adapter; `binance.py` is the reference
implementation of every method declared here.

An adapter answers exactly one question: *how do I reach and interpret one venue*.
Scheduling, persistence, cross-venue aggregation and business events live above it and
must never be duplicated into six venue classes (ADAPTER-RESEARCH §3.1).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import (
    Candle,
    Completeness,
    FundingRate,
    Instrument,
    Liquidation,
    LongShortRatio,
    LSKind,
    MarkPrice,
    OpenInterest,
    Ticker24h,
    now_ms,
)
from .errors import AdapterError
from .http import BreakerRegistry, HttpResult, VenueHttp
from .symbols import SymbolMapper, derive_canonical, split_multiplier

__all__ = [
    "AdapterError",
    "AdapterHealth",
    "BreakerRegistry",
    "Capability",
    "CapabilityInfo",
    "CapabilitySet",
    "HttpResult",
    "Mode",
    "SymbolMapper",
    "VenueAdapter",
    "VenueHttp",
    "derive_canonical",
    "split_multiplier",
]


class Capability(StrEnum):
    """The capability names hlens actually schedules against (ADAPTER-RESEARCH §3.2)."""

    discover_instruments = "discover_instruments"
    batch_ticker = "batch_ticker"
    mark_price = "mark_price"
    funding_current = "funding_current"
    funding_history = "funding_history"
    open_interest = "open_interest"
    open_interest_history = "open_interest_history"
    long_short_ratio = "long_short_ratio"
    taker_flow = "taker_flow"
    klines = "klines"
    liquidation_stream = "liquidation_stream"
    liquidation_history = "liquidation_history"
    trade_stream = "trade_stream"
    wallet_fills = "wallet_fills"


class Mode(StrEnum):
    rest = "rest"
    ws = "ws"
    ws_and_rest = "ws_and_rest"
    none = "none"


class CapabilityInfo(BaseModel):
    """Three independent answers that must not collapse into one boolean.

    ``supported`` -- can we call it at all;
    ``mode``      -- over which transport;
    ``completeness`` -- may the result be treated as a complete history.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    supported: bool = False
    mode: Mode = Mode.none
    completeness: Completeness = Completeness.unknown
    per_symbol: bool = Field(
        default=False, description="True when the venue has no all-market form"
    )
    note: str | None = None


class CapabilitySet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    venue: str
    product: str = "usdt_perpetual"
    capabilities: dict[Capability, CapabilityInfo] = Field(default_factory=dict)

    def get(self, cap: Capability) -> CapabilityInfo:
        return self.capabilities.get(cap, CapabilityInfo())

    def supports(self, cap: Capability) -> bool:
        return self.get(cap).supported


class AdapterHealth(BaseModel):
    """Per ``(venue, capability, transport)`` health, as `source_health` expects.

    Deliberately not one status per venue: a dead long/short-ratio endpoint must not
    grey out price and funding (ADAPTER-RESEARCH §6.3, §8.3).
    """

    model_config = ConfigDict(extra="forbid")

    venue: str
    capability: str
    transport: str = "rest"
    ok: bool = True
    last_ok_ts: int | None = None
    last_event_ts: int | None = None
    latency_ms: float | None = None
    consecutive_fail: int = 0
    last_error_class: str | None = None
    last_http_status: int | None = None
    gap_count_24h: int = 0
    freshness_state: str = "fresh"
    checked_ts: int = Field(default_factory=now_ms)


@runtime_checkable
class VenueAdapter(Protocol):
    """Structural protocol -- implementations do not subclass, they just match.

    A new adapter only has to implement what the venue actually offers; declare the
    rest as unsupported in :meth:`capabilities` and raise
    :class:`~hlens_core.adapters.errors.AdapterError` if called anyway. The collector
    asks ``capabilities()`` first and never calls an unsupported method.
    """

    venue: str
    product: str

    def capabilities(self) -> CapabilitySet: ...

    async def discover_instruments(self) -> list[Instrument]: ...

    async def fetch_mark_prices(
        self, symbols: Sequence[str] | None = None
    ) -> list[MarkPrice]: ...

    async def fetch_funding_rates(
        self, symbols: Sequence[str] | None = None
    ) -> list[FundingRate]: ...

    async def fetch_open_interest(self, symbols: Sequence[str]) -> list[OpenInterest]: ...

    async def fetch_long_short_ratios(
        self,
        symbol: str,
        kinds: Sequence[LSKind] | None = None,
        *,
        period: str = "5m",
        limit: int = 1,
    ) -> list[LongShortRatio]: ...

    async def fetch_klines(
        self,
        symbol: str,
        tf: str = "1m",
        *,
        limit: int = 500,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[Candle]: ...

    async def fetch_ticker_24h(
        self, symbols: Sequence[str] | None = None
    ) -> list[Ticker24h]: ...

    def stream_mark_price(
        self, symbols: Sequence[str] | None = None
    ) -> AsyncIterator[MarkPrice]: ...

    def stream_liquidations(
        self, symbols: Sequence[str] | None = None
    ) -> AsyncIterator[Liquidation]: ...

    async def healthcheck(self) -> list[AdapterHealth]: ...

    async def aclose(self) -> None: ...
