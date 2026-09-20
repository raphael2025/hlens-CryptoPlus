"""Symbol mapping, both ways, with the units attached (AGENTS §3.5).

F3's promise is that the unified coin name and the venue's own contract name
are both visible at all times, and that the numbers beside them are comparable.
Two facts make that true, and both are carried on the mapping rather than
remembered by a caller:

* the **quantity multiplier** — Binance lists ``1000PEPEUSDT`` and Hyperliquid
  lists ``kPEPE``; one contract unit is 1000 PEPE on both, and a size read off
  either venue means nothing until it is multiplied. Unknown is ``None`` and
  never ``1``: ``03`` §5 spells out what a default of 1 does — a newly listed
  coin gets a confident wrong notional that then clears a push threshold;
* the venue's **native funding interval** — 1 h on Hyperliquid, 8 h on Binance
  and 4 h on some Binance symbols. Seam ① stores the raw rate with its interval
  and derives the 8-hour figure on read, so the interval has to travel with the
  symbol from the moment the adapter discovers it.

The unified name is validated against the contracts' own ``Symbol`` type rather
than against a copy of its pattern, so the alphabet here is the same one §5's
``scope`` CHECK uses, by construction. Widening it in one place and not the
other is how a symbol becomes undroppable into a scope string.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final, Protocol, runtime_checkable

from pydantic import TypeAdapter

from hlens_core.contracts import IntervalHours, Symbol, VenueSymbol

__all__ = ["SymbolMap", "SymbolMapping", "SymbolTable"]

_SYMBOL: Final[TypeAdapter[str]] = TypeAdapter(Symbol)
_VENUE_SYMBOL: Final[TypeAdapter[str]] = TypeAdapter(VenueSymbol)
_INTERVAL_H: Final[TypeAdapter[int]] = TypeAdapter(IntervalHours)


@dataclass(frozen=True, slots=True)
class SymbolMapping:
    """One coin on one venue: both names, the multiplier and the interval."""

    symbol: str
    """The unified coin name (``PEPE``). Validated with the contracts'
    ``Symbol`` type — same alphabet as §5's ``scope`` CHECK."""

    venue_symbol: str
    """The venue's own contract name, verbatim: ``1000PEPEUSDT`` / ``kPEPE``.
    Kept exactly as the venue writes it, because it is what F3 displays and
    what the next request has to send back."""

    funding_interval_h: int
    """The venue's native funding interval in hours. The divisor seam ①'s
    ``funding_rate_8h`` needs; without it a Hyperliquid hourly rate silently
    reads as an 8-hourly one."""

    mult: Decimal | None = None
    """Base units per contract unit — 1000 for both ``1000PEPE`` and ``kPEPE``.
    ``None`` means unknown, never 1 (``03`` §5)."""

    def __post_init__(self) -> None:
        _SYMBOL.validate_python(self.symbol)
        _VENUE_SYMBOL.validate_python(self.venue_symbol)
        _INTERVAL_H.validate_python(self.funding_interval_h)
        if isinstance(self.mult, float):
            raise TypeError("mult is a Decimal (seam ①); a float has already lost digits")
        if self.mult is not None and self.mult <= 0:
            raise ValueError(f"a quantity multiplier is positive; got {self.mult}")

    def to_base_units(self, quantity: Decimal) -> Decimal | None:
        """``quantity`` expressed in the coin's own units, or ``None``.

        ``None`` when the multiplier is unknown — the caller then writes
        ``NULL``, which is what ``03`` §5 requires of an unknown notional.
        """
        if self.mult is None:
            return None
        return quantity * self.mult


@runtime_checkable
class SymbolMap(Protocol):
    """Both directions of the mapping, for one venue.

    Both directions are required (AGENTS §3.5): the collector goes unified →
    venue to build a request, and venue → unified to normalize whatever a
    market-wide endpoint or a stream pushes back, which is a set we did not
    choose and which contains names we may not have asked for.

    Lookups return ``None`` rather than raising: an unknown venue symbol on a
    market-wide stream is an ordinary event (a coin listed since our last
    universe reconciliation), and it must be skipped and counted, not turned
    into an exception in the middle of a stream.
    """

    def to_venue(self, symbol: str) -> SymbolMapping | None:
        """Unified name → this venue's mapping."""
        ...

    def to_unified(self, venue_symbol: str) -> SymbolMapping | None:
        """This venue's contract name → the mapping carrying the unified name."""
        ...

    def __iter__(self) -> Iterator[SymbolMapping]:
        """Every mapping, for the daily reconciliation and for ``/v1/instruments.json``."""
        ...

    def __len__(self) -> int: ...


@dataclass(frozen=True, slots=True)
class SymbolTable:
    """The obvious :class:`SymbolMap`: a fixed set of mappings, both ways.

    Lives here rather than in each venue adapter so that the duplicate rules
    are the same on both: one unified name per venue symbol **and** one venue
    symbol per unified name. A venue that lists two contracts for one coin is
    not a mapping problem to be resolved quietly — it is a universe question
    (F1), and it has to surface as an error here.
    """

    mappings: tuple[SymbolMapping, ...]
    _by_symbol: dict[str, SymbolMapping] = field(
        init=False, default_factory=dict, compare=False, repr=False
    )
    _by_venue_symbol: dict[str, SymbolMapping] = field(
        init=False, default_factory=dict, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        for mapping in self.mappings:
            if mapping.symbol in self._by_symbol:
                raise ValueError(
                    f"{mapping.symbol} is mapped twice "
                    f"({self._by_symbol[mapping.symbol].venue_symbol} and {mapping.venue_symbol})"
                )
            if mapping.venue_symbol in self._by_venue_symbol:
                raise ValueError(
                    f"{mapping.venue_symbol} is mapped twice "
                    f"({self._by_venue_symbol[mapping.venue_symbol].symbol} and {mapping.symbol})"
                )
            self._by_symbol[mapping.symbol] = mapping
            self._by_venue_symbol[mapping.venue_symbol] = mapping

    @classmethod
    def of(cls, mappings: Iterable[SymbolMapping]) -> SymbolTable:
        return cls(tuple(mappings))

    def to_venue(self, symbol: str) -> SymbolMapping | None:
        return self._by_symbol.get(symbol)

    def to_unified(self, venue_symbol: str) -> SymbolMapping | None:
        return self._by_venue_symbol.get(venue_symbol)

    def __iter__(self) -> Iterator[SymbolMapping]:
        return iter(self.mappings)

    def __len__(self) -> int:
        return len(self.mappings)
