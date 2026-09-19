"""Instruments and the tradable universe — seam ① for F1 and F3.

Two records, because §5 has two tables with two different keys:

* ``instruments`` — one row per venue per contract per version (SCD-2), keyed
  ``(venue, venue_symbol, valid_from)``. This is what makes F3's alignment
  publishable: ``/v1/instruments.json`` is generated from it, so the symbol
  mapping, the quantity multiplier and the native funding interval are all on
  the record rather than in somebody's head.
* ``coin_universe`` — one row per coin per spell on the list, keyed
  ``(symbol, in_from)``. F1: we only collect coins listed on **both** venues,
  so a universe segment is inherently about both and its ``venue`` is §5's
  cross-venue marker ``x``.

Versioning bounds are not contract fields
-----------------------------------------
``valid_from`` / ``valid_to`` and ``in_from`` / ``in_to`` are **assigned when a
version or a segment is opened or closed**, by the persistence boundary that
compares the new observation against the currently open row (M1-E). They are
not observations, and putting them on the contract would invite the same
mistake as bucketing ``ts``: an adapter would fill them from whatever it
happened to see. What the contract carries is the observation — "this is how
the instrument looked at ``ts``" — and ``ts`` is the reconciliation instant
that F1 requires at least once a day.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from .base import (
    CROSS_VENUE,
    DecimalValue,
    HlensRecord,
    IntervalHours,
    NonNegativeDecimal,
    Venue,
    VenueSymbol,
)

__all__ = ["CoinUniverseRecord", "InstrumentRecord", "InstrumentStatus"]


class InstrumentStatus(StrEnum):
    """Normalized trading status of a venue's contract.

    A small closed set on purpose. Each venue's native vocabulary is wider
    (Binance alone has ``TRADING`` / ``PENDING_TRADING`` / ``SETTLING`` /
    ``DELIVERING`` / ``CLOSE``); mapping it onto these three is the adapter's
    job (M1-A step ④), and an unmappable status is ``None``, not a guess.
    """

    TRADING = "trading"
    """Open for trading now."""

    SUSPENDED = "suspended"
    """Listed but temporarily not tradable."""

    DELISTED = "delisted"
    """Off the venue. F1: delisting stops collection; data already collected
    stays."""


#: Why a coin entered or left the universe (§5's ``coin_universe.reason``).
#: Short free text, lower-case and greppable; the vocabulary belongs to the
#: ``universe`` module (M1-E), which is the only writer of that table.
UniverseReason = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{0,63}$")]


class InstrumentRecord(HlensRecord):
    """One venue's contract, as observed at ``ts``.

    Single-venue by construction: ``venue`` is narrowed to the two exchanges
    rather than validated after the fact, so the cross-venue marker is a type
    error here instead of a runtime one.
    """

    venue: Literal[Venue.BINANCE, Venue.HYPERLIQUID]
    """Narrowed to a real exchange: an instrument belongs to exactly one."""

    semantic: None = None
    """Pinned to ``None``: an instrument definition carries no observed price,
    so the mark price / candle close distinction does not apply."""

    venue_symbol: VenueSymbol
    """The venue's own contract name, verbatim — Binance ``1000PEPEUSDT`` next
    to Hyperliquid ``kPEPE`` for the same ``symbol``. F3 keeps this permanently
    visible beside the unified name, so it must survive normalization
    untouched."""

    mult: DecimalValue | None = None
    """Quantity multiplier: how many base units one contract unit is, which is
    what makes ``1000PEPE`` and ``kPEPE`` comparable.

    Unknown is ``None`` — **not** ``1``. §5's third "必须由数据结构本身回答的事"
    calls this out: a newly listed coin that has not reached ``instruments`` yet
    has an unknown multiplier, its ``notional_usd`` must be ``NULL``, and a
    default of ``1`` would turn that gap into a confident wrong number that
    then clears a push threshold."""

    funding_interval_h: IntervalHours | None = None
    """The venue's native funding interval in hours. F3 shows it beside the
    normalized 8-hour rate; it is also the divisor
    :attr:`~hlens_core.contracts.market.MarketRecord.funding_rate_8h` needs."""

    tick: NonNegativeDecimal | None = None
    """Minimum price increment."""

    status: InstrumentStatus | None = None
    """Normalized trading status, or ``None`` if the venue's value does not map
    onto :class:`InstrumentStatus`."""


class CoinUniverseRecord(HlensRecord):
    """One coin's membership of the collected universe, as observed at ``ts``.

    F1: a coin is collected exactly while **both** venues list it. No manual
    allow-list, no manual block-list, no approval step — the daily
    reconciliation observes membership and this record reports it.
    """

    venue: Literal[Venue.CROSS] = CROSS_VENUE
    """Pinned to §5's cross-venue marker ``x``. Membership is a statement about
    both venues at once, so naming either one would be false."""

    semantic: None = None
    """Pinned to ``None``: membership carries no observed price."""

    listed: bool
    """``True`` when the coin is on the list as of ``ts``, ``False`` when it has
    left it. The persistence boundary turns a ``True`` with no open segment into
    a new ``in_from``, and a ``False`` into an ``in_to`` on the open one."""

    reason: UniverseReason | None = None
    """Why membership changed, if known."""
