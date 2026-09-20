"""Symbol mapping: both directions, with the multiplier and the interval.

AGENTS §3.5: "Symbol mapping both ways, including the unit differences
(``1000PEPE`` ↔ ``kPEPE``) and the venue's native funding interval alongside
the normalized 8-hour value."
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from hlens_core.adapters import SymbolMap, SymbolMapping, SymbolTable
from hlens_core.contracts import NORMALIZED_FUNDING_INTERVAL_H

BINANCE_PEPE = SymbolMapping(
    symbol="PEPE", venue_symbol="1000PEPEUSDT", funding_interval_h=8, mult=Decimal(1000)
)
HYPERLIQUID_PEPE = SymbolMapping(
    symbol="PEPE", venue_symbol="kPEPE", funding_interval_h=1, mult=Decimal(1000)
)


def test_the_same_coin_is_two_names_and_one_multiplier() -> None:
    """The reason the mapping carries units at all: the two venues write the
    same 1000-unit contract differently, and a size means nothing until it is
    multiplied."""
    assert BINANCE_PEPE.symbol == HYPERLIQUID_PEPE.symbol == "PEPE"
    assert BINANCE_PEPE.venue_symbol != HYPERLIQUID_PEPE.venue_symbol
    assert BINANCE_PEPE.to_base_units(Decimal(3)) == Decimal(3000)
    assert HYPERLIQUID_PEPE.to_base_units(Decimal(3)) == Decimal(3000)


def test_the_native_funding_interval_travels_with_the_symbol() -> None:
    """Hyperliquid settles hourly and Binance eight-hourly (``04`` §3/§2), and
    the normalized 8-hour figure is derived from that divisor by the contract —
    never stored, never pre-converted here (seam ①, ``03`` §5)."""
    assert HYPERLIQUID_PEPE.funding_interval_h == 1
    assert BINANCE_PEPE.funding_interval_h == 8
    assert NORMALIZED_FUNDING_INTERVAL_H == Decimal(8)


def test_an_unknown_multiplier_is_none_and_never_one() -> None:
    """``03`` §5: a default of 1 turns an unknown into a confident wrong
    notional that then clears a push threshold."""
    unknown = SymbolMapping(symbol="NEWCOIN", venue_symbol="NEWCOINUSDT", funding_interval_h=8)
    assert unknown.mult is None
    assert unknown.to_base_units(Decimal(3)) is None


def test_a_multiplier_is_a_decimal_and_positive() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        SymbolMapping(
            symbol="PEPE",
            venue_symbol="1000PEPEUSDT",
            funding_interval_h=8,
            mult=1000.0,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="positive"):
        SymbolMapping(
            symbol="PEPE", venue_symbol="1000PEPEUSDT", funding_interval_h=8, mult=Decimal(0)
        )


@pytest.mark.parametrize("bad", ["pepe", "1000PEPE-USDT", "", "TOOLONGSYMBOLNAME", "PEPE/USDC"])
def test_the_unified_alphabet_is_the_contracts_one_and_is_not_widened(bad: str) -> None:
    """The pattern is not copied here: :class:`SymbolMapping` validates through
    the contracts' own ``Symbol`` type, which is the same alphabet as §5's
    ``scope`` CHECK. Widening one and not the other is how a symbol stops
    fitting into a scope string."""
    with pytest.raises(ValueError):
        SymbolMapping(symbol=bad, venue_symbol="X", funding_interval_h=8)


def test_the_venue_name_is_kept_exactly_as_the_venue_writes_it() -> None:
    """F3 displays it permanently, and the next request has to send it back."""
    for venue_symbol in ("1000PEPEUSDT", "kPEPE", "@1", "PURR/USDC"):
        mapping = SymbolMapping(symbol="PEPE", venue_symbol=venue_symbol, funding_interval_h=8)
        assert mapping.venue_symbol == venue_symbol


def test_a_funding_interval_of_zero_is_refused() -> None:
    """It is the divisor of the 8-hour normalization."""
    with pytest.raises(ValueError):
        SymbolMapping(symbol="PEPE", venue_symbol="1000PEPEUSDT", funding_interval_h=0)


def test_the_table_maps_both_ways() -> None:
    table = SymbolTable.of([BINANCE_PEPE])
    assert isinstance(table, SymbolMap)
    assert table.to_venue("PEPE") is BINANCE_PEPE
    assert table.to_unified("1000PEPEUSDT") is BINANCE_PEPE
    assert len(table) == 1
    assert list(table) == [BINANCE_PEPE]


def test_an_unknown_name_is_none_in_both_directions() -> None:
    """A market-wide stream pushes coins we did not ask for; that is an
    ordinary event to skip and count, not an exception mid-stream."""
    table = SymbolTable.of([BINANCE_PEPE])
    assert table.to_venue("BTC") is None
    assert table.to_unified("BTCUSDT") is None


def test_a_coin_cannot_map_to_two_contracts_or_the_other_way_round() -> None:
    other = SymbolMapping(symbol="PEPE", venue_symbol="PEPEUSDT", funding_interval_h=8)
    with pytest.raises(ValueError, match="mapped twice"):
        SymbolTable.of([BINANCE_PEPE, other])

    renamed = SymbolMapping(symbol="PEPE2", venue_symbol="1000PEPEUSDT", funding_interval_h=8)
    with pytest.raises(ValueError, match="mapped twice"):
        SymbolTable.of([BINANCE_PEPE, renamed])
