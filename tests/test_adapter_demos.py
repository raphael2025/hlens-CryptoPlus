"""Demonstrations printed for the PR body. `uv run pytest tests/test_adapter_demos.py -q -s`.

Nothing here is a fixture for the venue adapters: the declaration sheets below
are built inside the test to show what the rules force, and M1-A steps ⑤ and ⑥
own the real ones. No request is sent, no host is named.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest

from hlens_core.adapters import (
    LIQUIDATION_CAPABILITIES,
    M4_CAPABILITIES,
    VENUE_MUST_DECLARE_UNSUPPORTED,
    Admission,
    CallCost,
    Capability,
    CapabilityError,
    CapabilitySet,
    Completeness,
    LanePriority,
    LiquidationCapabilityDeclaration,
    LiquidationCompleteness,
    MarketCapabilityDeclaration,
    Mode,
    Support,
    unsupported,
)
from hlens_core.contracts import Venue
from hlens_core.ratelimit import FakeClock, Grant, LedgerConfig, Priority, RateLimitLedger
from test_module_boundaries import ALLOWED_EDGES

VENUES = (Venue.BINANCE, Venue.HYPERLIQUID)

#: mode + completeness for the capabilities each venue does publish, read off
#: `docs/04-DATA-SOURCES.md` §1–§5. Illustrative — the adapters own the real
#: answers; what the test proves is which of them the rules refuse to accept.
_PUBLISHED: dict[Capability, tuple[Mode, Completeness]] = {
    Capability.INSTRUMENTS: (Mode.POLL_SNAPSHOT, Completeness.FULL),
    Capability.MARK_PRICE: (Mode.POLL_SNAPSHOT, Completeness.FULL),
    Capability.FUNDING_RATE: (Mode.POLL_SNAPSHOT, Completeness.FULL),
    Capability.OPEN_INTEREST: (Mode.POLL_SNAPSHOT, Completeness.FULL),
    Capability.LONG_SHORT_RATIO: (Mode.POLL_SNAPSHOT, Completeness.FULL),
    Capability.TAKER_RATIO: (Mode.POLL_SNAPSHOT, Completeness.FULL),
    Capability.KLINES: (Mode.POLL_HISTORY, Completeness.PARTIAL_HISTORY),
    Capability.TICKER_24H: (Mode.POLL_SNAPSHOT, Completeness.FULL),
    Capability.MARK_PRICE_STREAM: (Mode.PUSH_STREAM, Completeness.FULL),
}


def _sheet(venue: Venue) -> CapabilitySet:
    declarations: list[Any] = []
    for capability in Capability:
        if capability in M4_CAPABILITIES:
            declarations.append(unsupported(capability, note="M4 (F20-F22), not before"))
        elif capability in VENUE_MUST_DECLARE_UNSUPPORTED[venue]:
            declarations.append(
                unsupported(capability, note="this venue does not publish it (04 §1/§8)")
            )
        elif capability in LIQUIDATION_CAPABILITIES:
            declarations.append(
                LiquidationCapabilityDeclaration(
                    capability=capability,
                    supported=Support.SUPPORTED,
                    mode=Mode.PUSH_STREAM,
                    completeness=Completeness.LOWER_BOUND,
                    throttled_source=True,
                    note="!forceOrder@arr: one order per symbol per second (04 §2)",
                )
            )
        else:
            mode, completeness = _PUBLISHED[capability]
            declarations.append(
                MarketCapabilityDeclaration(
                    capability=capability,
                    supported=Support.SUPPORTED,
                    mode=mode,
                    completeness=completeness,
                )
            )
    return CapabilitySet(venue=venue, declarations=tuple(declarations))


def test_demo_capability_sheet() -> None:
    print("\n=== DEMO 2: three answers, and the three M4 capabilities on both venues ===")
    for venue in VENUES:
        sheet = _sheet(venue)
        print(f"\n{venue.value}")
        print(f"  {'capability':<20} {'supported':<12} {'mode':<16} completeness")
        for declaration in sheet.declarations:
            mark = "  <-- M4" if declaration.capability in M4_CAPABILITIES else ""
            print(
                f"  {declaration.capability.value:<20} {declaration.supported.value:<12} "
                f"{declaration.mode.value:<16} {declaration.completeness.value}{mark}"
            )
        for capability in sorted(M4_CAPABILITIES):
            assert sheet[capability].supported is Support.UNSUPPORTED

    print("\nthe three answers are three fields, and a supported capability must answer all three:")
    try:
        MarketCapabilityDeclaration(
            capability=Capability.MARK_PRICE,
            supported=Support.SUPPORTED,
            mode=Mode.NOT_APPLICABLE,
            completeness=Completeness.FULL,
        )
    except CapabilityError as error:
        print(f"  refused: {error}")

    print("\nand no adapter can open an M4 capability early:")
    for capability in sorted(M4_CAPABILITIES):
        try:
            MarketCapabilityDeclaration(
                capability=capability,  # type: ignore[arg-type]
                supported=Support.SUPPORTED,
                mode=Mode.PUSH_STREAM,
                completeness=Completeness.FULL,
            )
        except CapabilityError as error:
            print(f"  refused: {error}")

    print("\nnor derive a substitute for what Hyperliquid does not publish:")
    for capability in sorted(VENUE_MUST_DECLARE_UNSUPPORTED[Venue.HYPERLIQUID]):
        print(f"  hyperliquid {capability.value:<20} -> unsupported (04 §1 / §8, AGENTS §3.4)")


def test_demo_liquidation_completeness_has_no_full() -> None:
    print("\n=== DEMO 3: a liquidation completeness cannot be 'full' ===")
    print(f"  Completeness has            : {[c.value for c in Completeness]}")
    print(
        "  LiquidationCompleteness has : "
        f"{[c.value for c in get_args(LiquidationCompleteness)]}"
    )
    assert Completeness.FULL not in get_args(LiquidationCompleteness)

    print(
        "\n  statically (uv run mypy, strict; the line is in "
        "tests/test_adapter_admission.py):"
    )
    print("    completeness=Completeness.FULL,  # type: ignore[arg-type]")
    print("    -> strict mode's warn_unused_ignores fails the build if that stops being an error")

    print("\n  at runtime, as the second fence:")
    try:
        LiquidationCapabilityDeclaration(
            capability=Capability.LIQUIDATION_STREAM,
            supported=Support.SUPPORTED,
            mode=Mode.PUSH_STREAM,
            completeness=Completeness.FULL,  # type: ignore[arg-type]
            throttled_source=True,
        )
    except CapabilityError as error:
        print(f"    refused: {error}")

    print("\n  and a throttled source cannot be anything but a lower bound (01 §4.7):")
    with pytest.raises(CapabilityError) as caught:
        LiquidationCapabilityDeclaration(
            capability=Capability.LIQUIDATION_STREAM,
            supported=Support.SUPPORTED,
            mode=Mode.PUSH_STREAM,
            completeness=Completeness.PARTIAL_HISTORY,
            throttled_source=True,
        )
    print(f"    refused: {caught.value}")


def test_demo_admission_without_an_import_edge() -> None:
    print("\n=== DEMO 4: the weight table stays on the adapter, the budget in the ledger ===")
    print(f"  ALLOWED_EDGES['adapters'] = {sorted(ALLOWED_EDGES['adapters'])}  (unchanged)")
    print(f"  ALLOWED_EDGES['wallet']   = {sorted(ALLOWED_EDGES['wallet'])}  (unchanged)")
    print("  adapters imports contracts only; the ledger is injected, structurally.\n")

    ledger = RateLimitLedger(
        LedgerConfig.load("config/venues.yaml", "config/egress-consumers.yaml"),
        clock=FakeClock(),
    )
    admission: Admission[Priority, Grant] = Admission(ledger, lambda lane: Priority(lane.value))

    rows = (
        CallCost(
            bucket="binance:fapi_weight",
            weight=10,
            priority=LanePriority.FAST_LANE,
            endpoint="/fapi/v1/premiumIndex (whole market, 04 §2: W=10)",
        ),
        CallCost(
            bucket="binance:futures_data",
            weight=1,
            priority=LanePriority.RESIDENT,
            lane="ratios",
            endpoint="/futures/data/topLongShortPositionRatio (no weight, own bucket)",
        ),
        CallCost(
            bucket="hyperliquid:info_weight",
            weight=20,
            priority=LanePriority.FAST_LANE,
            endpoint="metaAndAssetCtxs (04 §3: W=20)",
        ),
    )
    for cost in rows:
        grant = admission.acquire(cost)
        admission.settle(grant, actual_cost=cost.weight, status=200)
        print(
            f"  {cost.bucket:<26} {cost.priority.value:<14} cost={cost.weight:<3} "
            f"granted={bool(grant)}  {cost.endpoint}"
        )

    print("\n  what the ledger now has on its books:")
    for key in ("binance:fapi_weight", "binance:futures_data", "hyperliquid:info_weight"):
        snapshot = ledger.snapshot(key)
        print(
            f"  {key:<26} used_fast={snapshot.used_fast_per_min:<4} "
            f"used_resident={snapshot.used_resident_per_min:<4} "
            f"our ceiling={snapshot.our_ceiling_per_min}/min"
        )
