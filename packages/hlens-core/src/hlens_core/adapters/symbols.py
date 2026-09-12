"""Venue symbol <-> canonical hlens symbol.

``BTCUSDT``, ``BTC-USDT-SWAP``, ``BTC_USDT`` and ``BTC`` are four strings for one
asset-level perpetual, and ``1000PEPE`` / ``kPEPE`` / ``PEPE`` are the same asset at
three different contract multipliers. The canonical symbol is the *asset* (``PEPE``);
the multiplier that reconciles the venue quote with it travels on the Instrument.
"""

from __future__ import annotations

import re
from decimal import Decimal

#: Quote/settle suffixes stripped when deriving the canonical symbol, longest first.
QUOTE_SUFFIXES: tuple[str, ...] = ("USDT", "USDC", "USD", "BUSD")

#: Venue prefixes that mean "this contract is N base units". ``1INCH`` deliberately does
#: not match: the pattern requires the full multiplier token followed by a letter.
_PREFIX_MULTIPLIERS: tuple[tuple[str, Decimal], ...] = (
    ("1000000", Decimal(1_000_000)),
    ("100000", Decimal(100_000)),
    ("10000", Decimal(10_000)),
    ("1000", Decimal(1_000)),
    ("1M", Decimal(1_000_000)),
    ("1B", Decimal(1_000_000_000)),
    ("k", Decimal(1_000)),  # Hyperliquid style: kPEPE, kSHIB. Lowercase only, so KAVA is safe.
)

_TRAILING_NUM = re.compile(r"^[0-9]+$")


def split_multiplier(token: str) -> tuple[str, Decimal]:
    """``1000PEPE -> ("PEPE", 1000)``, ``kSHIB -> ("SHIB", 1000)``, ``BTC -> ("BTC", 1)``."""
    for prefix, mult in _PREFIX_MULTIPLIERS:
        if token.startswith(prefix):
            rest = token[len(prefix) :]
            if rest and rest[0].isupper() and not _TRAILING_NUM.match(rest):
                return rest, mult
    return token, Decimal(1)


def strip_quote(token: str) -> tuple[str, str | None]:
    """``BTCUSDT -> ("BTC", "USDT")``; unknown quote returns ``(token, None)``."""
    for q in QUOTE_SUFFIXES:
        if token.endswith(q) and len(token) > len(q):
            return token[: -len(q)], q
    return token, None


class SymbolMapper:
    """Bidirectional map between one venue's symbols and canonical hlens symbols.

    Adapters register the pairs they discover from the venue's instrument endpoint;
    ``overrides`` handles the cases the rules cannot infer (a venue that lists two
    contracts for one asset, or a rename).
    """

    def __init__(self, venue: str, overrides: dict[str, str] | None = None) -> None:
        self.venue = venue
        self._to_canonical: dict[str, str] = {}
        self._to_venue: dict[str, str] = {}
        self._overrides = dict(overrides or {})

    def register(self, venue_symbol: str, canonical: str) -> None:
        self._to_canonical[venue_symbol] = canonical
        # First registration wins: if a venue lists both BTCUSDT and BTCUSDC we keep the
        # one the adapter offered first (its own preference order), never silently flip.
        self._to_venue.setdefault(canonical, venue_symbol)

    def canonical(self, venue_symbol: str) -> str:
        """Canonical symbol for a venue symbol, deriving it when not registered."""
        if venue_symbol in self._overrides:
            return self._overrides[venue_symbol]
        known = self._to_canonical.get(venue_symbol)
        if known:
            return known
        return derive_canonical(venue_symbol)

    def venue_symbol(self, canonical: str) -> str | None:
        return self._to_venue.get(canonical)

    def __len__(self) -> int:
        return len(self._to_canonical)

    @property
    def canonicals(self) -> list[str]:
        return sorted(self._to_venue)


def derive_canonical(venue_symbol: str) -> str:
    """Best-effort canonical symbol from a raw venue symbol, no registry needed.

    ``BTCUSDT`` / ``BTC-USDT-SWAP`` / ``BTC_USDT`` / ``1000PEPEUSDT`` / ``kPEPE`` all
    reduce to the asset. Separator-delimited forms take the first segment; glued forms
    have the quote suffix stripped.
    """
    token = venue_symbol.strip()
    for sep in ("-", "_", "/"):
        if sep in token:
            token = token.split(sep)[0]
            break
    else:
        token, _ = strip_quote(token)
    base, _mult = split_multiplier(token)
    return base.upper()
