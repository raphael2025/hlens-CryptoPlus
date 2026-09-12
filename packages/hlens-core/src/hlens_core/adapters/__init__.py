"""Venue adapters. `binance` is the reference implementation."""

from .base import (
    AdapterHealth,
    BreakerRegistry,
    Capability,
    CapabilityInfo,
    CapabilitySet,
    Mode,
    SymbolMapper,
    VenueAdapter,
    VenueHttp,
    derive_canonical,
    split_multiplier,
)
from .binance import BinanceFutures
from .errors import (
    AdapterError,
    Banned,
    CircuitOpen,
    GeoBlocked,
    InvalidSymbol,
    RateLimited,
    SchemaError,
    ServerError,
    TransportError,
)

__all__ = [
    "AdapterError",
    "AdapterHealth",
    "Banned",
    "BinanceFutures",
    "BreakerRegistry",
    "Capability",
    "CapabilityInfo",
    "CapabilitySet",
    "CircuitOpen",
    "GeoBlocked",
    "InvalidSymbol",
    "Mode",
    "RateLimited",
    "SchemaError",
    "ServerError",
    "SymbolMapper",
    "TransportError",
    "VenueAdapter",
    "VenueHttp",
    "derive_canonical",
    "split_multiplier",
]
