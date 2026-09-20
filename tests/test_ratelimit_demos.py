"""Two demonstrations, written as tests so they cannot rot.

Run them with output shown::

    uv run pytest tests/test_ratelimit_demos.py -q -s

They are ordinary tests — they assert, and they fail if the behaviour changes —
but they also print the table a person would want to look at, because both of
these are things a reviewer should be able to *see* rather than take on trust:

1. a 418 stops **every lane of that venue**, not a quarter of one;
2. retiring the legacy collector gives Hyperliquid the whole 1080/min, which is
   step ③ of §6.1's reclamation procedure, rehearsed before it is needed.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from conftest import reclaimed_consumers_yaml
from hlens_core.ratelimit import (
    BucketSnapshot,
    DenyReason,
    FakeClock,
    LedgerConfig,
    Priority,
    RateLimitLedger,
)

BINANCE_WEIGHT = "binance:fapi_weight"
FUTURES_DATA = "binance:futures_data"
FUNDING_RATE = "binance:funding_rate"
HL_WEIGHT = "hyperliquid:info_weight"


def _budget_line(snapshot: BucketSnapshot) -> str:
    return (
        f"  {snapshot.key!s:26} {snapshot.kind.value:7} "
        f"official {snapshot.official_limit_per_min:>5}/min "
        f"x {float(snapshot.share):>4.0%} = egress ceiling {snapshot.egress_ceiling_per_min:>5} "
        f"- reserved {snapshot.reserved_per_min:>4} "
        f"= ours {snapshot.our_ceiling_per_min:>5}/min "
        f"[{snapshot.profile}] reserve {snapshot.reserve_per_min:>4} "
        f"fast-floor {snapshot.fast_lane_floor_per_min:>4} "
        f"opportunistic {snapshot.opportunistic_available_per_min:>4}"
    )


def test_demo_one_a_418_stops_every_lane_of_that_venue(
    config: LedgerConfig, clock: FakeClock
) -> None:
    """DEMO 1 — §6: 见 418 → 读 Retry-After → 停掉该所全部车道（不是只砍 25%）."""
    ledger = RateLimitLedger(config, clock=clock)

    print("\n=== DEMO 1: one 418 on binance:futures_data ===")
    print("before: every lane of every Binance bucket is open")
    for key in (BINANCE_WEIGHT, FUTURES_DATA, FUNDING_RATE, HL_WEIGHT):
        for priority in Priority:
            grant = ledger.acquire(key, cost=1, priority=priority)
            print(f"  acquire {key:26} {priority.value:14} -> granted={grant.granted}")
            if grant.granted:
                ledger.release(grant)

    print("\nfeeding a 418 with Retry-After: 120 to binance:futures_data")
    ledger.observe_response(FUTURES_DATA, status=418, retry_after_s=120)

    print("after: every lane of every Binance bucket is stopped")
    for key in (BINANCE_WEIGHT, FUTURES_DATA, FUNDING_RATE):
        for priority in Priority:
            grant = ledger.acquire(key, cost=1, priority=priority)
            print(
                f"  acquire {key:26} {priority.value:14} -> granted={grant.granted} "
                f"reason={grant.reason} retry_after_ms={grant.retry_after_ms}"
            )
            assert not grant.granted
            assert grant.reason is DenyReason.VENUE_HALTED

    print("\n§6 says 「不是只砍 25%」 — not MERELY a cut. So: stopped, and also cut")
    snapshot = ledger.snapshot(BINANCE_WEIGHT)
    print(
        f"  halted={snapshot.halted}  (every lane, including the fast lane)\n"
        f"  resident_factor={float(snapshot.resident_factor)} "
        f"(applies when the ban lifts, not instead of the stop)\n"
        f"  opportunistic_frozen={snapshot.opportunistic_frozen} "
        f"(at least the full hour a 429 costs)"
    )
    assert snapshot.halted
    assert snapshot.resident_factor == Fraction(3, 4)

    print("\nhyperliquid is a different venue and a different budget:")
    grant = ledger.acquire(HL_WEIGHT, cost=20, priority=Priority.FAST_LANE)
    print(f"  acquire {HL_WEIGHT:26} fast_lane      -> granted={grant.granted}")
    assert grant.granted

    print("\nthe events the collector has to write:")
    for event in ledger.drain_events():
        print(
            f"  kind={event.kind.value} venue={event.venue} bucket={event.bucket} "
            f"ingest_gap.cause={None if event.gap_cause is None else event.gap_cause.value} "
            f"ops_event={event.ops_event} notify_f4={event.notify_f4}"
        )
        print(f"    detail={event.detail}")

    print("\nthe ban lifts exactly when Retry-After says, and not before:")
    clock.advance_ms(119_999)
    print(f"  t+119.999s halted={ledger.is_halted('binance')}")
    clock.advance_ms(1)
    print(
        f"  t+120.000s halted={ledger.is_halted('binance')}  "
        f"opportunistic_frozen={ledger.is_opportunistic_frozen('binance')}"
    )
    assert not ledger.is_halted("binance")
    assert ledger.is_opportunistic_frozen("binance")


def test_demo_two_retiring_the_legacy_collector_reclaims_1080(
    config: LedgerConfig, venues_path: Path, clock: FakeClock, tmp_path: Path
) -> None:
    """DEMO 2 — §6.1 退役回收流程 step ③: 确认 HL 可用预算变成 1080/分."""
    ledger = RateLimitLedger(config, clock=clock)

    print("\n=== DEMO 2: hub_legacy's Hyperliquid reservation 960 -> 0 ===")
    print("before (config/egress-consumers.yaml as committed):")
    for snapshot in ledger.snapshots():
        print(_budget_line(snapshot))
    before = ledger.snapshot(HL_WEIGHT)
    assert before.our_ceiling_per_min == 120
    assert before.reserved_per_min == 960

    path = tmp_path / "egress-consumers.yaml"
    path.write_text(reclaimed_consumers_yaml(), encoding="utf-8")
    events = ledger.reload(LedgerConfig.load(venues_path, path))

    print("\nafter (hub_legacy: reserved_per_min: 0, with its retirement assertion):")
    for snapshot in ledger.snapshots():
        print(_budget_line(snapshot))

    after = ledger.snapshot(HL_WEIGHT)
    print(
        f"\nHyperliquid available budget: {before.our_ceiling_per_min}/min "
        f"-> {after.our_ceiling_per_min}/min  "
        f"(= the whole 90 % egress ceiling of {after.egress_ceiling_per_min})"
    )
    assert after.our_ceiling_per_min == 1080
    assert after.our_ceiling_per_min == after.egress_ceiling_per_min
    assert after.profile == "reclaimed"

    print("\nthe ops_event §6.1 requires for the change:")
    for event in events:
        print(f"  kind={event.kind.value} venue={event.venue} bucket={event.bucket}")
        print(f"    detail={event.detail}")
    assert [event.kind.value for event in events] == ["budget_reclaimed"]

    print("\nand the opportunistic lane grows with it (§6.1 表第 4 行):")
    reclaimed_bucket = ledger.config.bucket(HL_WEIGHT)
    print(
        f"  formula {after.our_ceiling_per_min} - max({after.reserve_per_min}, 44) = "
        f"{reclaimed_bucket.opportunistic_formula_per_min(44)}"
        f"  hard cap {after.opportunistic_hard_cap_per_min}"
        f"  enforced {after.opportunistic_available_per_min}"
    )
    assert reclaimed_bucket.opportunistic_formula_per_min(44) == 880
    assert after.opportunistic_available_per_min == 400
