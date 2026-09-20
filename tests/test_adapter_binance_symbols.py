"""``1000PEPEUSDT`` ↔ ``PEPE``, both ways, with the units (AGENTS §3.5).

Three things have to be true at once, and each of them has its own way of going
wrong: the unified name (F3's "币名映射如 Binance ``1000PEPE`` = Hyperliquid
``kPEPE``"), the quantity multiplier (``03`` §5: unknown is ``NULL``, never a
default of 1), and the venue's native funding interval (``04`` §2: ``fundingInfo``
lists only the symbols that are *not* on the 8-hour grid).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import TypeAdapter

from conftest import binance_payload
from hlens_core.adapters import SymbolTable
from hlens_core.adapters.binance import (
    DEFAULT_FUNDING_INTERVAL_H,
    normalize_exchange_info,
    normalize_funding_info,
    split_multiplier,
    symbol_table_of,
    unified_symbol,
)
from hlens_core.contracts import Symbol

_SYMBOL = TypeAdapter(Symbol)


def _table() -> SymbolTable:
    intervals = normalize_funding_info(binance_payload("funding_info"))
    instruments = normalize_exchange_info(
        binance_payload("exchange_info"), ingest_ts=1, funding_interval_h=intervals
    )
    return symbol_table_of(instruments)


# --------------------------------------------------------------------------- #
# The multiplier
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("base_asset", "expected_symbol", "expected_mult"),
    [
        ("BTC", "BTC", Decimal(1)),
        ("ETH", "ETH", Decimal(1)),
        ("1000PEPE", "PEPE", Decimal(1000)),
        ("1000000MOG", "MOG", Decimal(1_000_000)),
        # A coin whose name genuinely starts with a digit. Reading the 1 as a
        # multiplier would rename the coin AND get its size right by accident.
        ("1INCH", "1INCH", Decimal(1)),
        # A unit prefix we can see and cannot read: unknown, never 1.
        ("1MBABYDOGE", "1MBABYDOGE", None),
    ],
)
def test_split_multiplier(
    base_asset: str, expected_symbol: str, expected_mult: Decimal | None
) -> None:
    symbol, mult = split_multiplier(base_asset)
    assert symbol == expected_symbol
    assert mult == expected_mult
    assert unified_symbol(base_asset) == expected_symbol


def test_an_unreadable_unit_prefix_is_unknown_and_never_one() -> None:
    """``03`` §5: a default of 1 turns a gap into a confident wrong number.

    A factor of a million is the version of that mistake that clears a push
    threshold, so the answer is ``None`` and the coin simply does not pair.
    """
    _, mult = split_multiplier("1MBABYDOGE")
    assert mult is None


def test_every_unified_name_fits_the_scope_alphabet() -> None:
    """``^[A-Z0-9]{1,15}$`` — the same pattern as ``03`` §5's ``scope`` CHECK.

    Not widened here, and not widened there: a symbol that does not fit cannot
    be dropped into a scope string.
    """
    for base_asset in ("BTC", "1000PEPE", "1000000MOG", "1INCH"):
        _SYMBOL.validate_python(unified_symbol(base_asset))


# --------------------------------------------------------------------------- #
# Both directions
# --------------------------------------------------------------------------- #
def test_the_mapping_round_trips_both_ways() -> None:
    table = _table()
    for symbol, venue_symbol in (("PEPE", "1000PEPEUSDT"), ("BTC", "BTCUSDT")):
        forward = table.to_venue(symbol)
        assert forward is not None and forward.venue_symbol == venue_symbol
        back = table.to_unified(venue_symbol)
        assert back is not None and back.symbol == symbol
        assert back is forward


def test_the_mapping_carries_the_multiplier_and_the_native_interval() -> None:
    table = _table()
    pepe = table.to_venue("PEPE")
    assert pepe is not None
    assert pepe.mult == Decimal(1000)
    # 04 §2: fundingInfo lists 1000PEPEUSDT at 4 hours.
    assert pepe.funding_interval_h == 4
    assert pepe.to_base_units(Decimal("2.5")) == Decimal(2500)

    btc = table.to_venue("BTC")
    assert btc is not None
    assert btc.mult == Decimal(1)
    # Absent from fundingInfo, so the venue's default — a fact, not a fallback.
    assert btc.funding_interval_h == DEFAULT_FUNDING_INTERVAL_H == 8


def test_an_unknown_venue_symbol_is_none_rather_than_an_exception() -> None:
    """A coin listed since the last reconciliation arrives on a market-wide
    stream we did not choose. Skipped and counted, never raised mid-stream."""
    table = _table()
    assert table.to_unified("NEWCOINUSDT") is None
    assert table.to_venue("NEWCOIN") is None


def test_only_perpetuals_reach_the_table() -> None:
    """``04`` §2 reads ``exchangeInfo`` with ``contractType`` = PERPETUAL only.

    The fixture carries a quarterly contract on the same base asset; without the
    filter it would collide with the perpetual on the unified name ``BTC`` and
    :class:`SymbolTable` would refuse to build — which is the loud version of a
    bug that is otherwise silent.
    """
    table = _table()
    assert table.to_unified("BTCUSDT_260925") is None
    assert len(table) == 2
