"""Symbol mapping: the rule that keeps 1000PEPE, kPEPE and PEPE one asset."""

from __future__ import annotations

from decimal import Decimal

import pytest

from hlens_core.adapters import SymbolMapper, derive_canonical, split_multiplier


@pytest.mark.parametrize(
    ("token", "base", "mult"),
    [
        ("BTC", "BTC", 1),
        ("1000PEPE", "PEPE", 1000),
        ("1000000MOG", "MOG", 1_000_000),
        ("kPEPE", "PEPE", 1000),
        ("kSHIB", "SHIB", 1000),
        ("1MBABYDOGE", "BABYDOGE", 1_000_000),
        # these must NOT be split: the prefix is part of the name
        ("1INCH", "1INCH", 1),
        ("KAVA", "KAVA", 1),
        ("KSM", "KSM", 1),
    ],
)
def test_split_multiplier(token, base, mult):
    assert split_multiplier(token) == (base, Decimal(mult))


@pytest.mark.parametrize(
    ("venue_symbol", "canonical"),
    [
        ("BTCUSDT", "BTC"),          # binance / bybit / bitget
        ("BTC-USDT-SWAP", "BTC"),    # okx
        ("BTC_USDT", "BTC"),         # gate
        ("BTC", "BTC"),              # hyperliquid
        ("1000PEPEUSDT", "PEPE"),
        ("kPEPE", "PEPE"),
        ("ETHUSDC", "ETH"),
        ("1INCHUSDT", "1INCH"),
    ],
)
def test_derive_canonical_across_venue_styles(venue_symbol, canonical):
    assert derive_canonical(venue_symbol) == canonical


def test_mapper_round_trip():
    m = SymbolMapper("binance")
    m.register("1000PEPEUSDT", "PEPE")
    assert m.canonical("1000PEPEUSDT") == "PEPE"
    assert m.venue_symbol("PEPE") == "1000PEPEUSDT"
    assert m.canonicals == ["PEPE"]


def test_first_registration_wins_for_the_reverse_map():
    m = SymbolMapper("binance")
    m.register("BTCUSDT", "BTC")
    m.register("BTCUSDC", "BTC")
    assert m.venue_symbol("BTC") == "BTCUSDT", "never silently flip the preferred contract"
    assert m.canonical("BTCUSDC") == "BTC"


def test_overrides_beat_the_rules():
    m = SymbolMapper("okx", overrides={"WEIRD-THING": "REAL"})
    assert m.canonical("WEIRD-THING") == "REAL"


def test_unregistered_symbols_still_derive():
    assert SymbolMapper("bybit").canonical("SOLUSDT") == "SOL"
