"""``kPEPE`` ↔ ``PEPE``, both ways, with the units and the interval (AGENTS §3.5).

The unit half of this is the whole reason the mapping carries a number:
Binance lists the same coin as ``1000PEPEUSDT`` and Hyperliquid as ``kPEPE``,
one contract unit is 1000 PEPE on both, and a size read off either venue means
nothing until it has been multiplied.

The interval half is this venue's specific trap: **every** Hyperliquid
perpetual funds hourly, so the interval is a constant, not a default with a
fallback — and an 8 that slipped in here would multiply every funding rate by
eight downstream, silently.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import hyperliquid_payload
from hlens_core.adapters import SymbolTable
from hlens_core.adapters.hyperliquid import (
    FUNDING_INTERVAL_H,
    normalize_meta,
    split_multiplier,
    symbol_table_of,
    unified_symbol,
)

T0 = 1_789_819_200_000


def _table() -> SymbolTable:
    return symbol_table_of(
        normalize_meta(hyperliquid_payload("meta"), ingest_ts=T0, observed_ts=T0)
    )


def test_the_thousand_prefix_is_read_as_a_thousand() -> None:
    assert split_multiplier("kPEPE") == ("PEPE", Decimal(1000))
    assert unified_symbol("kPEPE") == "PEPE"


def test_an_ordinary_coin_is_one_and_that_one_is_an_observation() -> None:
    """Hyperliquid quotes these contracts in whole base units. That 1 is
    something the venue does, not the forbidden default."""
    assert split_multiplier("BTC") == ("BTC", Decimal(1))


def test_an_upper_case_k_is_a_coin_name_not_a_prefix() -> None:
    """``KAITO`` is a coin called KAITO, not a thousand AITO. The case is the
    only thing that separates them, so the pattern is case-sensitive."""
    assert split_multiplier("KAITO") == ("KAITO", Decimal(1))


def test_an_undocumented_unit_prefix_is_unknown_and_never_one() -> None:
    """``04`` documents exactly one prefix convention on this venue. A prefix we
    can see and cannot read means the multiplier is unknown — ``03`` §5 spells
    out what a default of 1 costs, and a wrong multiplier is worse than a gap."""
    name, mult = split_multiplier("mBABYDOGE")
    assert name == "mBABYDOGE"
    assert mult is None


def test_both_directions_round_trip() -> None:
    table = _table()
    for symbol, venue_symbol in (("PEPE", "kPEPE"), ("BTC", "BTC")):
        mapping = table.to_venue(symbol)
        assert mapping is not None and mapping.venue_symbol == venue_symbol
        back = table.to_unified(venue_symbol)
        assert back is not None and back.symbol == symbol


def test_every_mapping_funds_hourly() -> None:
    """``04`` §3 / §5: Hyperliquid settles funding every hour, for every symbol.
    There is no per-symbol interval endpoint on this venue because there are no
    exceptions to list."""
    assert FUNDING_INTERVAL_H == 1
    for mapping in _table():
        assert mapping.funding_interval_h == 1


def test_the_kpepe_multiplier_matches_binances_1000pepe() -> None:
    """F3's promise: the two venues' numbers are comparable. They are only
    comparable because both mappings carry the same 1000 — Binance writes it
    into the contract name, Hyperliquid writes it as a prefix letter."""
    from hlens_core.adapters.binance import split_multiplier as binance_split

    hl_name, hl_mult = split_multiplier("kPEPE")
    bn_name, bn_mult = binance_split("1000PEPE")
    assert hl_name == bn_name == "PEPE"
    assert hl_mult == bn_mult == Decimal(1000)


def test_an_unknown_multiplier_yields_no_base_units_rather_than_a_wrong_one() -> None:
    from hlens_core.adapters.symbols import SymbolMapping

    mapping = SymbolMapping(
        symbol="BABYDOGE", venue_symbol="mBABYDOGE", funding_interval_h=1, mult=None
    )
    assert mapping.to_base_units(Decimal(5)) is None


def test_a_float_multiplier_is_refused_outright() -> None:
    """Seam ①: a float has already lost the venue's digits."""
    from hlens_core.adapters.symbols import SymbolMapping

    with pytest.raises(TypeError):
        SymbolMapping(
            symbol="PEPE",
            venue_symbol="kPEPE",
            funding_interval_h=1,
            mult=1000.0,  # type: ignore[arg-type]
        )
