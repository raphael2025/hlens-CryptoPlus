"""Demonstrations printed for the PR body.

``uv run pytest tests/test_adapter_binance_demos.py -q -s``

Nothing here sends a request. The three demos are the three things a reviewer
would otherwise have to take on trust: that seam ③ still holds, what the
capability sheet actually says, and that the symbol mapping goes both ways for
a ``1000`` coin as well as an ordinary one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import VENUES_PATH, binance_payload
from hlens_core.adapters import (
    M4_CAPABILITIES,
    Admission,
    Capability,
    LiquidationCapabilityDeclaration,
    Support,
    SymbolTable,
)
from hlens_core.adapters.binance import (
    BinanceCall,
    binance_capabilities,
    cost_of_call,
    normalize_exchange_info,
    normalize_funding_info,
    symbol_table_of,
)
from hlens_core.ratelimit import (
    FakeClock,
    Grant,
    LedgerConfig,
    Priority,
    RateLimitLedger,
)
from test_module_boundaries import ALLOWED_EDGES, collect_edges

T0 = 1_789_819_200_000


def _symbols() -> SymbolTable:
    intervals = normalize_funding_info(binance_payload("funding_info"))
    return symbol_table_of(
        normalize_exchange_info(
            binance_payload("exchange_info"), ingest_ts=T0, funding_interval_h=intervals
        )
    )


# --------------------------------------------------------------------------- #
# DEMO 1 — seam ③ still holds
# --------------------------------------------------------------------------- #
def test_demo_seam_three_still_holds() -> None:
    print("\n=== DEMO 1: seam ③ — the adapter never imports the ledger ===")
    print(f"  ALLOWED_EDGES['adapters'] = {sorted(ALLOWED_EDGES['adapters'])}   (unchanged)")

    edges, scanned, _ = collect_edges()
    from_binance = sorted(
        {
            edge.target
            for edge in edges
            if "adapters/binance" in str(edge.path) and edge.owner == "adapters"
        }
    )
    print(f"  files scanned                : {len(scanned)}")
    print(f"  modules adapters/binance/* reaches for: {from_binance or ['contracts (only)']}")
    assert set(from_binance) <= ALLOWED_EDGES["adapters"]
    assert "ratelimit" not in from_binance

    print("\n  the ledger arrives instead, structurally:")
    ledger = RateLimitLedger(
        LedgerConfig.load(VENUES_PATH, VENUES_PATH.parent / "egress-consumers.yaml"),
        clock=FakeClock(),
    )
    admission: Admission[Priority, Grant] = Admission(ledger, lambda lane: Priority(lane.value))

    print(f"\n  {'call':<32} {'bucket':<24} {'tier':<14} cost")
    for call in BinanceCall:
        cost = cost_of_call(call, symbols=1 if call is BinanceCall.OPEN_INTEREST else None)
        grant = admission.acquire(cost)
        admission.settle(grant, actual_cost=cost.weight, status=200)
        print(
            f"  {call.value:<32} {cost.bucket:<24} {cost.priority.value:<14} {cost.weight}"
        )

    print("\n  what the ledger now has on its books:")
    for key in ("binance:fapi_weight", "binance:futures_data", "binance:funding_rate"):
        snapshot = ledger.snapshot(key)
        print(
            f"  {key:<24} used_fast={snapshot.used_fast_per_min:<4} "
            f"used_resident={snapshot.used_resident_per_min:<4} "
            f"used_opp={snapshot.used_opportunistic_per_min:<4} "
            f"our ceiling={snapshot.our_ceiling_per_min}/min"
        )


# --------------------------------------------------------------------------- #
# DEMO 2 — the capability sheet, as declared
# --------------------------------------------------------------------------- #
def test_demo_binance_capability_sheet() -> None:
    print("\n=== DEMO 2: Binance's thirteen capabilities, three answers each ===")
    sheet = binance_capabilities()
    print(f"  {'capability':<20} {'supported':<12} {'mode':<16} {'completeness':<16} note")
    for declaration in sheet.declarations:
        mark = ""
        if declaration.capability in M4_CAPABILITIES:
            mark = "  <-- M4, unsupported until then"
        if isinstance(declaration, LiquidationCapabilityDeclaration):
            mark = f"  <-- throttled_source={declaration.throttled_source}"
        print(
            f"  {declaration.capability.value:<20} {declaration.supported.value:<12} "
            f"{declaration.mode.value:<16} {declaration.completeness.value:<16}{mark}"
        )

    print(f"\n  capabilities declared        : {len(sheet.declarations)}")
    print(f"  unsupported                  : {[c.value for c in sheet.unsupported_capabilities()]}")
    liquidation = sheet[Capability.LIQUIDATION_STREAM]
    print(f"  liquidation completeness     : {liquidation.completeness.value}  (never `full`)")

    assert len(sheet.declarations) == 13
    for capability in (Capability.TRADE_STREAM, Capability.BOOK_L2, Capability.SPOT):
        assert sheet[capability].supported is Support.UNSUPPORTED
    assert liquidation.completeness.value == "lower_bound"

    print("\n  and there is no wallet capability in this enum at all (seam ⑤):")
    print(f"  {[c.value for c in Capability if 'wallet' in c.value] or 'none'}")


# --------------------------------------------------------------------------- #
# DEMO 3 — the symbol mapping, both ways
# --------------------------------------------------------------------------- #
def test_demo_symbol_mapping_round_trip() -> None:
    print("\n=== DEMO 3: symbol mapping, both ways, with units and interval ===")
    table = _symbols()
    print(
        f"  {'unified':<10} {'binance contract':<18} {'mult':>10}  "
        f"{'funding interval':>16}  round trip"
    )
    for symbol in ("PEPE", "BTC"):
        mapping = table.to_venue(symbol)
        assert mapping is not None
        back = table.to_unified(mapping.venue_symbol)
        assert back is not None and back.symbol == symbol
        print(
            f"  {mapping.symbol:<10} {mapping.venue_symbol:<18} {mapping.mult!s:>10}  "
            f"{str(mapping.funding_interval_h) + 'h':>16}  "
            f"{symbol} -> {mapping.venue_symbol} -> {back.symbol}  OK"
        )

    pepe = table.to_venue("PEPE")
    assert pepe is not None
    print(
        f"\n  one 1000PEPE contract unit is {pepe.to_base_units(Decimal(1))} PEPE "
        "— which is what makes it comparable with Hyperliquid's kPEPE (F3)"
    )
    print(
        "  and PEPE's funding settles every 4h on Binance, so the raw rate is stored "
        "with funding_interval_h=4;"
    )
    print("  the 8-hour figure is the contract's property, computed on read, never stored.")

    print("\n  a unit prefix no document explains stays unknown rather than 1 (03 §5):")
    from hlens_core.adapters.binance import split_multiplier

    for base_asset in ("BTC", "1000PEPE", "1000000MOG", "1INCH", "1MBABYDOGE"):
        name, mult = split_multiplier(base_asset)
        print(f"    {base_asset:<12} -> symbol={name:<12} mult={mult}")


@pytest.mark.live
def test_live_is_still_skipped_by_default() -> None:  # pragma: no cover
    """A placeholder that proves the marker works: M1-A5 sent no request, and
    the live suite belongs to ``M1-G``, on the production host, after the
    budget has been coordinated with the legacy collector (``04`` §11)."""
    raise AssertionError("this must never run here")
