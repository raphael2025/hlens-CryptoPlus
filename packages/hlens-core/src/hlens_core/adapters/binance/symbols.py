"""Binance ``1000PEPEUSDT`` ↔ unified ``PEPE``, both ways, with the units.

AGENTS §3.5 asks for three things at once and they are three different facts:

1. **the two names**, both kept — F3 shows the venue's own contract name beside
   the unified one permanently, so ``1000PEPEUSDT`` is never rewritten, only
   accompanied;
2. **the unit difference**, as a number — Binance lists PEPE in thousands and
   Hyperliquid lists it as ``kPEPE``; a size read off either venue means
   nothing until it is multiplied, and ``03`` §5 spells out what a *default* of
   1 costs (a confident wrong notional that then clears a push threshold), so
   an unrecognised prefix convention yields ``None``, never 1;
3. **the venue's native funding interval** — 8 hours for Binance by default,
   4 hours for the symbols ``fundingInfo`` lists (``04`` §2: "后者只列非默认周
   期币"). Seam ① stores the raw rate with its interval and derives the 8-hour
   figure on read, so the interval has to travel with the symbol.

The unified alphabet is ``hlens_core.contracts.Symbol`` — ``^[A-Z0-9]{1,15}$``,
the same pattern as §5's ``scope`` CHECK. It is **not** widened here. Note that
``1000PEPE`` would itself pass that pattern: stripping the prefix is a decision
about what the coin *is* (F3's "币名映射如 Binance `1000PEPE` = Hyperliquid
`kPEPE`"), not something the regex forces.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Final

from hlens_core.adapters.symbols import SymbolMapping, SymbolTable
from hlens_core.contracts import InstrumentRecord

__all__ = [
    "DEFAULT_FUNDING_INTERVAL_H",
    "DEFAULT_QUOTE_ASSET",
    "split_multiplier",
    "symbol_table_of",
    "unified_symbol",
]

#: ``04`` §2: ``fundingInfo`` "只列非默认周期币" — every symbol it does not
#: mention settles on Binance's default 8-hour grid (``04`` §5: "按 8 小时结算
#: 网格").
DEFAULT_FUNDING_INTERVAL_H: Final = 8

#: The quote asset the whole budget is written against. ``04`` §2's examples and
#: ``03`` §6's per-coin arithmetic are all USDT perpetuals. A venue that lists
#: the same coin twice (a USDT and a USDC perpetual) is a universe question
#: (F1), not a mapping to resolve quietly, so :func:`symbol_table_of` keeps one
#: quote asset and says which.
DEFAULT_QUOTE_ASSET: Final = "USDT"

#: A quantity multiplier written into the base asset: ``1`` followed by at least
#: two zeros and then a letter. ``1000PEPE`` → 1000, ``1000000MOG`` → 1000000.
#: Anchored and followed by a letter so ``1INCH`` — a coin whose name begins
#: with a digit — cannot be read as a multiplier.
_MULTIPLIER = re.compile(r"^(10{2,})(?=[A-Z])")

#: A base asset that starts with a digit followed by a metric letter (``1M…``,
#: ``1K…``). Binance's ``1000`` convention is the only one ``04`` documents;
#: this one is a unit prefix we can *see* but whose value no document states, so
#: the multiplier is **unknown** rather than 1. Getting this wrong is the exact
#: mistake ``03`` §5 names, and one factor of a million is worse than a gap.
_UNDOCUMENTED_PREFIX = re.compile(r"^[0-9]+[KM](?=[A-Z])")


def split_multiplier(base_asset: str) -> tuple[str, Decimal | None]:
    """``"1000PEPE"`` → ``("PEPE", Decimal(1000))``.

    Returns the unified coin name and how many base units one contract unit is.
    Three answers, in order of how much we know:

    * a documented ``1000``-family prefix → the name without it, and the
      multiplier it states;
    * an *undocumented* unit prefix (``1M…``, ``1K…``) → the base asset
      verbatim and ``None``: we can see that a prefix is there and cannot see
      what it means, and a wrong multiplier is worse than a missing one
      (``03`` §5). Such a coin simply will not pair with Hyperliquid's name
      until a document or ``M1-G``'s recording settles it;
    * anything else, ``1INCH`` included → the base asset verbatim and 1. That 1
      is an observation (Binance quotes these contracts in whole base units),
      not the forbidden default.
    """
    match = _MULTIPLIER.match(base_asset)
    if match is not None:
        prefix = match.group(1)
        return base_asset[len(prefix) :], Decimal(prefix)
    if _UNDOCUMENTED_PREFIX.match(base_asset) is not None:
        return base_asset, None
    return base_asset, Decimal(1)


def unified_symbol(base_asset: str) -> str:
    """Just the unified coin name — the half of :func:`split_multiplier` that
    F3 prints next to the venue's own contract name."""
    return split_multiplier(base_asset)[0]


def symbol_table_of(
    instruments: Iterable[InstrumentRecord],
    *,
    quote_asset: str = DEFAULT_QUOTE_ASSET,
) -> SymbolTable:
    """Build the adapter's :class:`SymbolMap` from what discovery returned.

    ``fetch_instruments`` returns **every** perpetual the venue lists, because
    ``instruments`` is the venue's contract list and F1's universe is somebody
    else's answer (``universe``, M1-D). This function is the narrowing the
    adapter itself needs in order to normalize: one contract per coin, so that a
    stream frame for ``1000PEPEUSDT`` has exactly one unified name.

    ``quote_asset`` is how that narrowing is stated out loud. ``04`` never names
    the quote asset — its examples and ``03`` §6's per-coin arithmetic are all
    USDT — so a coin listed in both USDT and USDC would otherwise collide, and
    :class:`SymbolTable` would (correctly) refuse to build. See "Doc
    corrections".
    """
    mappings = [
        SymbolMapping(
            symbol=record.symbol,
            venue_symbol=record.venue_symbol,
            # An absent interval on a Binance record is not an unknown: ``04``
            # §2 says ``fundingInfo`` lists only the symbols that are *not* on
            # the 8-hour grid, so "not listed" is a statement about the venue,
            # which is why :data:`DEFAULT_FUNDING_INTERVAL_H` is a constant with
            # a citation rather than a fallback.
            funding_interval_h=(
                record.funding_interval_h
                if record.funding_interval_h is not None
                else DEFAULT_FUNDING_INTERVAL_H
            ),
            mult=record.mult,
        )
        for record in instruments
        if record.venue_symbol.endswith(quote_asset)
    ]
    return SymbolTable.of(mappings)
