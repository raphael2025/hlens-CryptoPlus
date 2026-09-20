"""Demonstrations printed for the PR body.

``uv run pytest tests/test_adapter_hyperliquid_demos.py -q -s``

Nothing here sends a request. The three demos are the three things a reviewer
would otherwise have to take on trust: that seam ③ still holds, what this
venue's capability sheet actually refuses, and that the symbol mapping goes
both ways for a ``k`` coin as well as an ordinary one — and lands on the same
unified name as Binance's ``1000PEPE``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import VENUES_PATH, hyperliquid_payload
from hlens_core.adapters import (
    M4_CAPABILITIES,
    VENUE_MUST_DECLARE_UNSUPPORTED,
    Admission,
    Capability,
    Support,
    SymbolTable,
)
from hlens_core.adapters.binance import symbol_table_of as binance_symbol_table_of
from hlens_core.adapters.hyperliquid import (
    HyperliquidCall,
    cost_of_call,
    hyperliquid_capabilities,
    normalize_meta,
    split_multiplier,
    symbol_table_of,
)
from hlens_core.contracts import Venue
from hlens_core.ratelimit import (
    FakeClock,
    Grant,
    LedgerConfig,
    Priority,
    RateLimitLedger,
)
from test_module_boundaries import ALLOWED_EDGES, collect_edges

T0 = 1_789_819_200_000

#: Words that would mean seam ⑤ had leaked into the market-data adapter.
WALLET_WORDS = (
    "wallet",
    "userFills",
    "userFunding",
    "clearinghouseState",
    "liquidatedUser",
    "assetPositions",
    "leaderboard",
)


def _symbols() -> SymbolTable:
    return symbol_table_of(
        normalize_meta(hyperliquid_payload("meta"), ingest_ts=T0, observed_ts=T0)
    )


# --------------------------------------------------------------------------- #
# DEMO 1 — seam ③ still holds
# --------------------------------------------------------------------------- #
def test_demo_seam_three_still_holds() -> None:
    print("\n=== DEMO 1: seam ③ — the adapter never imports the ledger ===")
    print(f"  ALLOWED_EDGES['adapters'] = {sorted(ALLOWED_EDGES['adapters'])}   (unchanged)")

    edges, scanned, _ = collect_edges()
    from_hl = sorted(
        {
            edge.target
            for edge in edges
            if "adapters/hyperliquid" in str(edge.path) and edge.owner == "adapters"
        }
    )
    print(f"  files scanned                          : {len(scanned)}")
    print(f"  modules adapters/hyperliquid/* reaches for: {from_hl or ['contracts (only)']}")
    assert set(from_hl) <= ALLOWED_EDGES["adapters"]
    assert "ratelimit" not in from_hl
    assert "wallet" not in from_hl

    print("\n  the ledger arrives instead, structurally:")
    ledger = RateLimitLedger(
        LedgerConfig.load(VENUES_PATH, VENUES_PATH.parent / "egress-consumers.yaml"),
        clock=FakeClock(),
    )
    admission: Admission[Priority, Grant] = Admission(ledger, lambda lane: Priority(lane.value))

    print(f"\n  {'call':<24} {'bucket':<26} {'tier':<14} cost")
    for call in HyperliquidCall:
        per_symbol = call in (HyperliquidCall.FUNDING_HISTORY, HyperliquidCall.CANDLE_SNAPSHOT)
        cost = cost_of_call(
            call,
            symbols=1 if per_symbol else None,
            rows=400 if per_symbol else None,
        )
        grant = admission.acquire(cost)
        if grant:
            admission.settle(grant, actual_cost=cost.weight, status=200)
        print(
            f"  {call.value:<24} {cost.bucket:<26} {cost.priority.value:<14} "
            f"{cost.weight}{'' if grant else '   (denied)'}"
        )

    print("\n  what the ledger now has on its books:")
    snapshot = ledger.snapshot("hyperliquid:info_weight")
    print(
        f"  hyperliquid:info_weight  used_fast={snapshot.used_fast_per_min:<4} "
        f"used_resident={snapshot.used_resident_per_min:<4} "
        f"used_opp={snapshot.used_opportunistic_per_min:<4} "
        f"our ceiling={snapshot.our_ceiling_per_min}/min "
        f"opp hard cap={snapshot.opportunistic_hard_cap_per_min}/min"
    )
    print(
        "\n  the two backfill rows above asked for 400 rows each. fundingHistory's 20 filled\n"
        "  the opportunistic hard cap for the minute, so candleSnapshot's 7 was DENIED -\n"
        "  which is the tier working, not a bug. A FULL 500-row fundingHistory page would\n"
        "  cost 25 and never fit at all, so the backfill has to page at <=400 rows."
    )


# --------------------------------------------------------------------------- #
# DEMO 2 — the capability sheet, as declared
# --------------------------------------------------------------------------- #
def test_demo_hyperliquid_capability_sheet() -> None:
    print("\n=== DEMO 2: Hyperliquid's thirteen capabilities, three answers each ===")
    sheet = hyperliquid_capabilities()
    print(f"  {'capability':<20} {'supported':<12} {'mode':<16} {'completeness':<16} why")
    for declaration in sheet.declarations:
        mark = ""
        if declaration.capability in VENUE_MUST_DECLARE_UNSUPPORTED[Venue.HYPERLIQUID]:
            mark = "  <-- this venue does not publish it; no substitute derived"
        elif declaration.capability in M4_CAPABILITIES:
            mark = "  <-- M4, unsupported until then"
        elif declaration.completeness.value == "partial_history":
            mark = "  <-- ~5000 bars = ~3.5 days at 1m (04 §5)"
        print(
            f"  {declaration.capability.value:<20} {declaration.supported.value:<12} "
            f"{declaration.mode.value:<16} {declaration.completeness.value:<16}{mark}"
        )

    print(f"\n  capabilities declared : {len(sheet.declarations)}")
    print(f"  unsupported           : {[c.value for c in sheet.unsupported_capabilities()]}")
    assert len(sheet.declarations) == 13

    print("\n  the three this step exists to say no to (04 §1 and §8):")
    for capability in (
        Capability.LONG_SHORT_RATIO,
        Capability.TAKER_RATIO,
        Capability.LIQUIDATION_STREAM,
    ):
        declaration = sheet[capability]
        assert declaration.supported is Support.UNSUPPORTED
        print(f"    {capability.value:<20} {declaration.supported.value}")

    print("\n  and the three both venues refuse until M4 (seam ②):")
    for capability in (Capability.TRADE_STREAM, Capability.BOOK_L2, Capability.SPOT):
        assert sheet[capability].supported is Support.UNSUPPORTED
        print(f"    {capability.value:<20} {sheet[capability].supported.value}")

    print("\n  and it is structural, not a habit — VENUE_MUST_DECLARE_UNSUPPORTED:")
    print(
        f"    {sorted(c.value for c in VENUE_MUST_DECLARE_UNSUPPORTED[Venue.HYPERLIQUID])}"
    )

    print("\n  wallet keywords in the capability enum and on this sheet (seam ⑤):")
    names = " ".join(c.value for c in Capability).lower()
    hits = [word for word in WALLET_WORDS if word.lower() in names]
    print(f"    in the Capability enum      : {hits or 'none'}")
    print(f"    wallet methods on the adapter: {_wallet_methods() or 'none'}")
    print(
        "    (the NOTES do cite 'per-wallet userFills', to say why the liquidation stream\n"
        "     is unsupported. Explaining an absence is not reaching for one — the ws test\n"
        "     greps the package's code, with docstrings stripped, for endpoints it could send)"
    )
    assert hits == []
    assert _wallet_methods() == []


def _wallet_methods() -> list[str]:
    """Seam ⑤: no wallet method reaches the market-data adapter."""
    from hlens_core.adapters.hyperliquid import HyperliquidMarketDataAdapter

    return [
        name
        for name in dir(HyperliquidMarketDataAdapter)
        if any(word.lower() in name.lower() for word in WALLET_WORDS)
    ]


# --------------------------------------------------------------------------- #
# DEMO 3 — the symbol mapping, both ways, and across the two venues
# --------------------------------------------------------------------------- #
def test_demo_symbol_mapping_round_trip() -> None:
    print("\n=== DEMO 3: symbol mapping, both ways, with units and interval ===")
    table = _symbols()
    print(
        f"  {'unified':<10} {'hl contract':<14} {'mult':>8}  "
        f"{'funding interval':>16}  round trip"
    )
    for symbol in ("PEPE", "BTC"):
        mapping = table.to_venue(symbol)
        assert mapping is not None
        back = table.to_unified(mapping.venue_symbol)
        assert back is not None and back.symbol == symbol
        print(
            f"  {mapping.symbol:<10} {mapping.venue_symbol:<14} {mapping.mult!s:>8}  "
            f"{str(mapping.funding_interval_h) + 'h':>16}  "
            f"{symbol} -> {mapping.venue_symbol} -> {back.symbol}  OK"
        )

    print("\n  the same coin on both venues — F3's whole point:")
    binance = binance_symbol_table_of(
        _binance_instruments(),
    )
    for unified in ("PEPE", "BTC"):
        hl = table.to_venue(unified)
        bn = binance.to_venue(unified)
        assert hl is not None and bn is not None
        assert hl.symbol == bn.symbol == unified
        assert hl.mult == bn.mult
        print(
            f"    binance {bn.venue_symbol:<14} (mult {bn.mult}, {bn.funding_interval_h}h)"
            f"   ==  {unified:<6} ==   "
            f"hyperliquid {hl.venue_symbol:<8} (mult {hl.mult}, {hl.funding_interval_h}h)"
        )
    print(
        "\n  so one 1000PEPE contract and one kPEPE contract are both 1000 PEPE, and the\n"
        "  intervals differ (8h vs 1h) — which is exactly why the raw rate is stored with\n"
        "  its own funding_interval_h and the 8-hour figure is derived on read, not stored."
    )

    print("\n  a unit prefix no document explains stays unknown rather than 1 (03 §5):")
    for coin in ("BTC", "kPEPE", "KAITO", "HYPE", "mBABYDOGE"):
        name, mult = split_multiplier(coin)
        print(f"    {coin:<12} -> symbol={name:<12} mult={mult}")
    assert split_multiplier("mBABYDOGE")[1] is None
    assert split_multiplier("KAITO")[1] == Decimal(1)


def _binance_instruments() -> tuple[object, ...]:
    """The Binance side of the comparison, from that adapter's own fixture."""
    from conftest import binance_payload
    from hlens_core.adapters.binance import normalize_exchange_info, normalize_funding_info

    intervals = normalize_funding_info(binance_payload("funding_info"))
    return normalize_exchange_info(
        binance_payload("exchange_info"), ingest_ts=T0, funding_interval_h=intervals
    )


@pytest.mark.live
def test_live_is_still_skipped_by_default() -> None:  # pragma: no cover
    """A placeholder that proves the marker works: M1-A6 sent no request, and
    the live suite belongs to ``M1-G``, on the production host, after the
    budget has been coordinated with the legacy collector — which on this venue
    is collecting Hyperliquid right now (``04`` §11)."""
    raise AssertionError("this must never run here")
