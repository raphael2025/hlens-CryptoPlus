"""Hyperliquid ``kPEPE`` ↔ unified ``PEPE``, both ways, with the units.

AGENTS §3.5 names this venue's half of the example in as many words
(``1000PEPE`` ↔ ``kPEPE``), and asks for three facts at once:

1. **both names, both kept.** F3 shows the venue's own contract name beside the
   unified one permanently, so ``kPEPE`` is never rewritten, only accompanied;
2. **the unit difference, as a number.** ``k`` is a thousand, so one ``kPEPE``
   contract unit is 1000 PEPE — the same 1000 that Binance writes into the
   contract *name* as ``1000PEPE``. That both venues arrive at the same
   multiplier by different spellings is exactly why the number lives on the
   mapping and not in a reader's head: without it, an ``oi_share`` across the
   two venues compares two different units;
3. **the venue's native funding interval**, which on Hyperliquid is
   :data:`FUNDING_INTERVAL_H` — **one hour, for every symbol, with no default
   and no fallback**. See below; this is the single most expensive thing on
   this venue to get wrong.

Unknown prefixes stay unknown
-----------------------------
``04`` documents exactly one Hyperliquid prefix convention: ``k`` = 1000 (the
``kPEPE`` example). A coin whose name carries some *other* unit prefix is a
prefix we can see and whose value no document states, so
:func:`split_multiplier` answers ``None`` — never ``1``. ``03`` §5 spells out
what a default of 1 costs: a confident wrong notional that then clears a push
threshold. One factor of a thousand is worse than a gap, and a gap is
recoverable.

The unified alphabet is ``hlens_core.contracts.Symbol`` — ``^[A-Z0-9]{1,15}$``,
the same pattern as §5's ``scope`` CHECK, not widened here. Note that the
Hyperliquid contract name ``kPEPE`` would **not** pass that pattern (the ``k``
is lower case), which is a useful accident: the unified name has to be derived,
it cannot be the venue's string by default.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Final

from hlens_core.adapters.symbols import SymbolMapping, SymbolTable
from hlens_core.contracts import InstrumentRecord

__all__ = [
    "FUNDING_INTERVAL_H",
    "split_multiplier",
    "symbol_table_of",
    "unified_symbol",
]

#: **Hyperliquid settles funding every hour, for every symbol** (``04`` §3:
#: ``metaAndAssetCtxs``'s ``funding（每小时）``; ``04`` §5: "按 1 小时结算网格").
#:
#: This is a constant and not a default, and the difference is the point.
#: Binance's :data:`~hlens_core.adapters.binance.symbols.
#: DEFAULT_FUNDING_INTERVAL_H` is 8 because ``fundingInfo`` lists only the
#: symbols that deviate; Hyperliquid publishes **no** per-symbol interval at
#: all, so there is nothing to deviate from and nothing to look up. Writing 8
#: here — as a copied default, or as a "safe" fallback when a lookup misses —
#: would multiply every Hyperliquid funding rate by eight the moment seam ①'s
#: ``funding_rate_8h`` divides by the interval, silently and in the direction
#: that makes the venue look extreme. Seam ① stores the raw hourly value with
#: this interval beside it and derives the 8-hour figure on read; it is never
#: pre-converted and never stored (``03`` §5, ``04`` §1).
FUNDING_INTERVAL_H: Final = 1

#: ``k`` followed by an upper-case letter or digit: Hyperliquid's documented
#: thousand prefix (``04``/AGENTS §3.5's ``kPEPE``). Anchored, and the case
#: matters — ``KAITO`` is a coin called KAITO, not a thousand AITO.
_THOUSAND_PREFIX = re.compile(r"^k(?=[A-Z0-9])")

#: Some *other* lower-case unit prefix (``m…``, ``M`` is upper case so it is a
#: name). We can see that a prefix is there and cannot see what it means, so
#: the multiplier is unknown rather than 1 — the same rule the Binance adapter
#: applies to ``1M…`` / ``1K…``.
_UNDOCUMENTED_PREFIX = re.compile(r"^[a-z](?=[A-Z0-9])")


def split_multiplier(coin: str) -> tuple[str, Decimal | None]:
    """``"kPEPE"`` → ``("PEPE", Decimal(1000))``.

    Three answers, in order of how much we know:

    * the documented ``k`` prefix → the name without it, and 1000;
    * any *other* lower-case unit prefix → the coin name verbatim and ``None``:
      visible prefix, undocumented value, and ``03`` §5 would rather have the
      gap;
    * anything else → the coin name verbatim and 1. That 1 is an observation
      (Hyperliquid quotes these contracts in whole base units), not the
      forbidden default.
    """
    if _THOUSAND_PREFIX.match(coin) is not None:
        return coin[1:], Decimal(1000)
    if _UNDOCUMENTED_PREFIX.match(coin) is not None:
        return coin, None
    return coin, Decimal(1)


def unified_symbol(coin: str) -> str:
    """Just the unified coin name — the half of :func:`split_multiplier` that
    F3 prints next to the venue's own contract name."""
    return split_multiplier(coin)[0]


def symbol_table_of(instruments: Iterable[InstrumentRecord]) -> SymbolTable:
    """Build the adapter's :class:`~hlens_core.adapters.symbols.SymbolMap` from
    what discovery returned.

    Unlike Binance there is no quote-asset narrowing to do: ``meta.universe``
    lists each perpetual once, under one name, so a coin cannot appear twice.
    (Hyperliquid *spot* has an entirely separate symbol system — ``TOKEN/USDC``
    and ``@<index>`` — and ``04`` §9.3 says outright that no document states how
    a spot token pairs with the same-named perpetual. That is M4's problem and
    a hand-maintained table; nothing here guesses at it.)
    """
    return SymbolTable.of(
        SymbolMapping(
            symbol=record.symbol,
            venue_symbol=record.venue_symbol,
            # Not `record.funding_interval_h or FUNDING_INTERVAL_H`: there is no
            # case where it can be missing, and an `or` would hide it if there
            # were. The record was built by this adapter's own normalizer, which
            # writes the constant.
            funding_interval_h=FUNDING_INTERVAL_H,
            mult=record.mult,
        )
        for record in instruments
    )
