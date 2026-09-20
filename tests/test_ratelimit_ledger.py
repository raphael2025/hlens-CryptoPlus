"""The ledger's behaviour, run end to end on a clock the test owns.

Every span in here — the one-minute window, the one-hour opportunistic freeze,
the AIMD recovery, the five-minute WebSocket connection window, the 24-hour
rotation, a three-day ban — is exercised in full, and the whole file runs in
milliseconds because the clock is injected. No test here sleeps, and none of
them touches a socket.
"""

from __future__ import annotations

import ast
from fractions import Fraction
from pathlib import Path

import pytest

from hlens_core.ratelimit import (
    LEDGER_CAUSES,
    BurstShaper,
    DenyReason,
    EventKind,
    FakeClock,
    GapCause,
    HlBodyKind,
    LedgerConfig,
    Priority,
    RateLimitLedger,
    classify_hl_429_body,
)

BINANCE_WEIGHT = "binance:fapi_weight"
FUTURES_DATA = "binance:futures_data"
HL_WEIGHT = "hyperliquid:info_weight"

MINUTE_MS = 60_000
HOUR_MS = 60 * MINUTE_MS


def _spend(
    ledger: RateLimitLedger, key: str, *, cost: int, priority: Priority, times: int = 1
) -> int:
    granted = 0
    for _ in range(times):
        grant = ledger.acquire(key, cost=cost, priority=priority)
        if grant.granted:
            granted += cost
            ledger.settle(grant)
    return granted


# --------------------------------------------------------------------------- #
# The seam: this module cannot grow into an HTTP client or a database writer
# --------------------------------------------------------------------------- #
def test_the_ledger_imports_no_client_and_no_driver() -> None:
    """§6.1 / seam ③: it does accounting and admission, and sends nothing.

    An import of ``httpx`` here would mean the ledger had started making the
    calls it is supposed to be metering — which also makes it untestable
    offline. ``time`` is allowed in ``clock.py`` alone, which is the whole
    point of having a ``clock.py``.
    """
    package = (
        Path(__file__).resolve().parents[1]
        / "packages/hlens-core/src/hlens_core/ratelimit"
    )
    banned = {"httpx", "websockets", "requests", "psycopg", "aiohttp", "socket", "asyncio"}
    offences: list[str] = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # `time` is banned everywhere except clock.py, which is the one place
        # allowed to read a real clock — that is what clock.py is for.
        forbidden = banned if path.name == "clock.py" else banned | {"time"}
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module.split(".")[0]]
            else:
                # Narrows `node` to the two import statements, which are the
                # only nodes that carry a line number worth reporting.
                continue
            offences.extend(
                f"{path.name}:{node.lineno}: {name}" for name in names if name in forbidden
            )
    assert not offences, f"the ledger must not reach the network or the clock: {offences}"


def test_ingest_gap_causes_are_the_ten_of_section_5() -> None:
    """§5: `ingest_gap.cause` 是一个十值封闭枚举. Exact spellings, no inventions."""
    assert [cause.value for cause in GapCause] == [
        "venue_error",
        "rate_limit",
        "ip_ban",
        "backpressure",
        "ws_reconnect",
        "power_loss",
        "host_restart",
        "egress_down",
        "egress_change",
        "unknown",
    ]
    assert {cause.value for cause in LEDGER_CAUSES} == {"rate_limit", "ip_ban"}


# --------------------------------------------------------------------------- #
# Two kinds of bucket, never one
# --------------------------------------------------------------------------- #
def test_weight_and_request_buckets_do_not_share_an_account(
    ledger: RateLimitLedger,
) -> None:
    """§6: ``/futures/data/*`` 不吃权重、自己一个桶."""
    _spend(ledger, BINANCE_WEIGHT, cost=200, priority=Priority.RESIDENT)
    assert ledger.snapshot(BINANCE_WEIGHT).used_resident_per_min == 200
    assert ledger.snapshot(FUTURES_DATA).used_resident_per_min == 0

    _spend(ledger, FUTURES_DATA, cost=1, priority=Priority.RESIDENT, times=54)
    assert ledger.snapshot(FUTURES_DATA).used_resident_per_min == 54
    assert ledger.snapshot(BINANCE_WEIGHT).used_resident_per_min == 200


def test_the_window_rolls(ledger: RateLimitLedger, clock: FakeClock) -> None:
    _spend(ledger, FUTURES_DATA, cost=1, priority=Priority.RESIDENT, times=60)
    assert ledger.snapshot(FUTURES_DATA).used_resident_per_min == 60
    clock.advance_ms(MINUTE_MS + 1)
    assert ledger.snapshot(FUTURES_DATA).used_resident_per_min == 0


# --------------------------------------------------------------------------- #
# Three tiers, and the fast lane's floor
# --------------------------------------------------------------------------- #
def test_other_resident_lanes_cannot_eat_the_fast_lane_floor(
    ledger: RateLimitLedger,
) -> None:
    """§6.1 row 1: 其中快道地板 **120/分**，任何时刻不被其余 resident 挤占."""
    spent = _spend(ledger, BINANCE_WEIGHT, cost=10, priority=Priority.RESIDENT, times=200)
    # M1-B: our ceiling is 918, not 960 — the development machine's resident
    # Binance collector reserves 42 on this shared egress. That leaves
    # 918 - 120 = 798 for the other resident lanes, and cost=10 grants divide
    # it 79 times with an 8-weight remainder that 960 did not leave.
    assert spent == 790

    blocked = ledger.acquire(BINANCE_WEIGHT, cost=10, priority=Priority.RESIDENT)
    assert not blocked.granted
    assert blocked.reason is DenyReason.FAST_LANE_FLOOR
    assert blocked.retry_after_ms > 0

    # The fast lane still gets its whole floor, right now, with no waiting.
    assert _spend(ledger, BINANCE_WEIGHT, cost=10, priority=Priority.FAST_LANE, times=12) == 120
    # 790 resident + 120 fast lane = 910 of 918. The 8 left over are the
    # remainder above; spend them and the bucket really is full.
    assert _spend(ledger, BINANCE_WEIGHT, cost=1, priority=Priority.FAST_LANE, times=8) == 8
    assert ledger.acquire(BINANCE_WEIGHT, cost=1, priority=Priority.FAST_LANE).granted is False


def test_opportunistic_is_capped_at_the_hard_cap(ledger: RateLimitLedger) -> None:
    """§6.1 row 1: 可用 660/分, 硬顶 **200/分** —— the cap is what is enforced."""
    assert ledger.snapshot(BINANCE_WEIGHT).opportunistic_available_per_min == 200
    spent = _spend(ledger, BINANCE_WEIGHT, cost=10, priority=Priority.OPPORTUNISTIC, times=30)
    assert spent == 200
    denied = ledger.acquire(BINANCE_WEIGHT, cost=10, priority=Priority.OPPORTUNISTIC)
    assert denied.reason is DenyReason.OPPORTUNISTIC_BUDGET


def test_opportunistic_shrinks_when_resident_exceeds_its_reserve(
    ledger: RateLimitLedger,
) -> None:
    """§6.1: 可用 = 天花板 − max(reserve, resident 实际用量).

    Below the reserve the opportunistic lane does not grow (the floor holds);
    above it, resident's real usage is what counts.
    """
    assert ledger.snapshot(BINANCE_WEIGHT).opportunistic_available_per_min == 200
    _spend(ledger, BINANCE_WEIGHT, cost=100, priority=Priority.RESIDENT, times=2)  # 200 < 300
    assert ledger.snapshot(BINANCE_WEIGHT).opportunistic_available_per_min == 200
    # M1-B: with the ceiling at 918 rather than 960, 100-unit grants stop at
    # 700 (the fast-lane floor fences off 120) and 918 - 700 = 218 is still
    # above the 200 hard cap — so the shrink this test exists to show would
    # not be visible at that granularity. Finer grants reach 790.
    _spend(ledger, BINANCE_WEIGHT, cost=10, priority=Priority.RESIDENT, times=60)  # > 300
    assert ledger.snapshot(BINANCE_WEIGHT).used_resident_per_min == 790
    assert ledger.snapshot(BINANCE_WEIGHT).opportunistic_available_per_min == 918 - 790 == 128


def test_a_cost_larger_than_the_ceiling_says_so_instead_of_asking_for_a_retry(
    ledger: RateLimitLedger,
) -> None:
    denied = ledger.acquire(BINANCE_WEIGHT, cost=5_000, priority=Priority.FAST_LANE)
    assert denied.reason is DenyReason.COST_EXCEEDS_CEILING
    assert denied.retry_after_ms == 0


def test_settle_replaces_the_estimate_with_the_real_cost(
    ledger: RateLimitLedger,
) -> None:
    """A weight is an estimate until the response arrives."""
    grant = ledger.acquire(BINANCE_WEIGHT, cost=40, priority=Priority.FAST_LANE)
    ledger.settle(grant, actual_cost=10)
    assert ledger.snapshot(BINANCE_WEIGHT).used_fast_per_min == 10

    released = ledger.acquire(BINANCE_WEIGHT, cost=40, priority=Priority.FAST_LANE)
    ledger.release(released)
    assert ledger.snapshot(BINANCE_WEIGHT).used_fast_per_min == 10


# --------------------------------------------------------------------------- #
# Burst shaping
# --------------------------------------------------------------------------- #
def test_the_540_request_lane_is_not_fired_in_the_first_second() -> None:
    """§6: 540 次请求必须匀速摊到窗口内（54 次/分），不许在窗口首秒打完."""
    shaper = BurstShaper(total=540, window_ms=10 * MINUTE_MS)
    assert shaper.due_by(0) == 1
    assert shaper.due_by(999) == 1
    assert shaper.due_by(MINUTE_MS - 1) == 54
    assert shaper.due_by(5 * MINUTE_MS) == 271
    assert shaper.due_by(10 * MINUTE_MS) == 540


def test_a_paced_lane_releases_54_in_the_first_minute(
    ledger: RateLimitLedger, clock: FakeClock
) -> None:
    ledger.register_paced_lane(
        "ls_ratio", FUTURES_DATA, total=540, window_ms=10 * MINUTE_MS
    )
    granted = 0
    for _ in range(600):
        grant = ledger.acquire(
            FUTURES_DATA, cost=1, priority=Priority.RESIDENT, lane="ls_ratio"
        )
        if not grant.granted:
            assert grant.reason is DenyReason.PACED
            assert grant.retry_after_ms > 0
            break
        ledger.settle(grant)
        granted += 1
    assert granted == 1  # one release is due at t=0, and exactly one

    clock.advance_ms(MINUTE_MS - 1)
    released = 1
    while ledger.lane("ls_ratio").ready(clock.monotonic_ms()):
        grant = ledger.acquire(
            FUTURES_DATA, cost=1, priority=Priority.RESIDENT, lane="ls_ratio"
        )
        if not grant.granted:
            break
        ledger.settle(grant)
        released += 1
    assert released == 54  # §6.1 row 2: the shaped rate is 54/min, not 540/min


def test_a_paced_lane_starts_a_fresh_window(
    ledger: RateLimitLedger, clock: FakeClock
) -> None:
    lane = ledger.register_paced_lane("ls", FUTURES_DATA, total=540, window_ms=10 * MINUTE_MS)
    assert lane.take(clock.monotonic_ms())
    clock.advance_ms(10 * MINUTE_MS)
    assert lane.released_in_window == 1
    assert lane.ready(clock.monotonic_ms())
    assert lane.take(clock.monotonic_ms())
    assert lane.released_in_window == 1


# --------------------------------------------------------------------------- #
# 429 — AIMD
# --------------------------------------------------------------------------- #
def test_a_429_freezes_opportunistic_for_exactly_one_hour(
    ledger: RateLimitLedger, clock: FakeClock
) -> None:
    """§6: 任何 429 让 opportunistic 停 1 小时，resident 只降速."""
    grant = ledger.acquire(BINANCE_WEIGHT, cost=10, priority=Priority.OPPORTUNISTIC)
    ledger.settle(grant, status=429)

    assert ledger.is_opportunistic_frozen("binance")
    denied = ledger.acquire(BINANCE_WEIGHT, cost=1, priority=Priority.OPPORTUNISTIC)
    assert denied.reason is DenyReason.OPPORTUNISTIC_FROZEN
    assert denied.retry_after_ms == HOUR_MS

    clock.advance_ms(HOUR_MS - 1)
    assert ledger.is_opportunistic_frozen("binance")
    clock.advance_ms(1)
    assert not ledger.is_opportunistic_frozen("binance")
    assert ledger.acquire(BINANCE_WEIGHT, cost=1, priority=Priority.OPPORTUNISTIC).granted


def test_a_429_slows_resident_to_75_percent_but_does_not_stop_it(
    ledger: RateLimitLedger,
) -> None:
    grant = ledger.acquire(BINANCE_WEIGHT, cost=10, priority=Priority.FAST_LANE)
    ledger.settle(grant, status=429)

    assert ledger.resident_factor("binance") == Fraction(3, 4)
    assert ledger.resident_ceiling(BINANCE_WEIGHT) == 688  # 918 x 0.75, floored
    assert ledger.acquire(BINANCE_WEIGHT, cost=10, priority=Priority.FAST_LANE).granted


def test_resident_recovers_additively_and_never_past_full(
    ledger: RateLimitLedger, clock: FakeClock
) -> None:
    """The increase half of AIMD is this task's choice, not the document's.

    It is configuration (``resident_recovery_step``, tagged ``unverified``), so
    a measurement can replace it without touching the code. What matters here
    is that it is monotonic, bounded at 1, and deterministic.
    """
    grant = ledger.acquire(BINANCE_WEIGHT, cost=10, priority=Priority.FAST_LANE)
    ledger.settle(grant, status=429)
    assert ledger.resident_factor("binance") == Fraction(3, 4)

    clock.advance_ms(MINUTE_MS)
    assert ledger.resident_factor("binance") == Fraction(4, 5)  # 0.75 + 0.05
    clock.advance_ms(4 * MINUTE_MS)
    assert ledger.resident_factor("binance") == Fraction(1)
    clock.advance_ms(60 * MINUTE_MS)
    assert ledger.resident_factor("binance") == Fraction(1)


def test_repeated_429s_compound_but_stop_at_the_floor(
    ledger: RateLimitLedger,
) -> None:
    for _ in range(20):
        ledger.observe_response(BINANCE_WEIGHT, status=429)
    assert ledger.resident_factor("binance") == Fraction(1, 4)


def test_a_429_emits_a_rate_limit_event_for_the_collector_to_write(
    ledger: RateLimitLedger,
) -> None:
    """§5: `rate_limit` 的写方是 ratelimit，写的时机是「429 后冻结」."""
    ledger.observe_response(FUTURES_DATA, status=429)
    (event,) = ledger.drain_events()
    assert event.kind is EventKind.RATE_LIMIT
    assert event.gap_cause is GapCause.RATE_LIMIT
    assert event.venue == "binance"
    assert event.bucket == "futures_data"
    assert event.ops_event is True
    assert ledger.drain_events() == ()


def test_the_ledger_only_ever_emits_its_own_two_causes(
    ledger: RateLimitLedger,
) -> None:
    ledger.observe_response(BINANCE_WEIGHT, status=429)
    ledger.observe_response(BINANCE_WEIGHT, status=418, retry_after_s=120)
    ledger.observe_response(HL_WEIGHT, status=429, body="null")
    causes = {event.gap_cause for event in ledger.drain_events()}
    assert causes <= LEDGER_CAUSES
    assert causes == {GapCause.RATE_LIMIT, GapCause.IP_BAN}


# --------------------------------------------------------------------------- #
# 429 on Hyperliquid — body type, and the mandatory private message
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("null", HlBodyKind.WEIGHT),
        ("  null\n", HlBodyKind.WEIGHT),
        ("<html><head><title>429 Too Many Requests</title></head></html>", HlBodyKind.CONNECTION),
        ('{"error":"?"}', HlBodyKind.UNRECOGNIZED),
        (b"null", HlBodyKind.WEIGHT),
    ],
)
def test_hyperliquid_429_bodies_are_classified(body: str | bytes, expected: HlBodyKind) -> None:
    """04 §3 实测 ②: JSON `null` = 权重限速; nginx HTML 页 = 连接速率限速."""
    assert classify_hl_429_body(body) is expected


def test_every_hyperliquid_429_also_demands_an_f4_private_message(
    ledger: RateLimitLedger,
) -> None:
    """§6.1: 任何一次 HL 429 … 还必须发一条 F4 私聊 —— 不是可以静默吞掉的常规退避.

    On a shared egress a 429 means the other consumer may be getting hit too.
    """
    ledger.observe_response(HL_WEIGHT, status=429, body="null")
    (event,) = ledger.drain_events()
    assert event.notify_f4 is True
    assert event.detail["body_kind"] == "weight"


def test_a_binance_429_backs_off_without_waking_anyone(ledger: RateLimitLedger) -> None:
    """§6.1 names the F4 obligation for Hyperliquid only; the 418 covers Binance."""
    ledger.observe_response(BINANCE_WEIGHT, status=429)
    (event,) = ledger.drain_events()
    assert event.notify_f4 is False
    assert event.gap_cause is GapCause.RATE_LIMIT


def test_a_connection_type_429_lowers_concurrency_instead_of_weight(
    ledger: RateLimitLedger, clock: FakeClock
) -> None:
    """04 §3 实测 ②: HTML body → 降并发, with a measured hard cap of 10."""
    assert ledger.inflight_limit("hyperliquid") == 8
    ledger.observe_response(HL_WEIGHT, status=429, body="<html>429</html>")
    (event,) = ledger.drain_events()
    assert event.detail["body_kind"] == "connection"
    assert ledger.inflight_limit("hyperliquid") == 6
    clock.advance_ms(2 * MINUTE_MS)
    assert ledger.inflight_limit("hyperliquid") == 8


# --------------------------------------------------------------------------- #
# 418 — stop everything for that venue
# --------------------------------------------------------------------------- #
def test_a_418_stops_every_lane_of_that_venue_not_just_a_quarter(
    ledger: RateLimitLedger, clock: FakeClock
) -> None:
    """§6: 见 418 → 读 `Retry-After` → **停掉该所全部车道**（不是只砍 25%）."""
    ledger.observe_response(FUTURES_DATA, status=418, retry_after_s=120)

    for key in (BINANCE_WEIGHT, FUTURES_DATA):
        for priority in Priority:
            denied = ledger.acquire(key, cost=1, priority=priority)
            assert not denied.granted, (key, priority)
            assert denied.reason is DenyReason.VENUE_HALTED
            assert denied.retry_after_ms == 120_000

    assert ledger.is_halted("binance")
    clock.advance_ms(120_000)
    assert not ledger.is_halted("binance")
    assert ledger.acquire(BINANCE_WEIGHT, cost=10, priority=Priority.FAST_LANE).granted


def test_a_418_is_not_merely_a_cut_but_is_not_less_than_one_either(
    ledger: RateLimitLedger, clock: FakeClock
) -> None:
    """§6: 「不是只砍 25%」 reads as *not merely* a cut, so both apply.

    Coming back onto a just-unbanned shared IP at the full resident rate is
    how the next 418 is earned, and the opportunistic lane stays frozen for at
    least the hour a plain 429 costs even when the ban itself was shorter.
    """
    ledger.observe_response(BINANCE_WEIGHT, status=418, retry_after_s=120)
    assert ledger.resident_factor("binance") == Fraction(3, 4)

    clock.advance_ms(120_000)
    assert not ledger.is_halted("binance")
    assert ledger.is_opportunistic_frozen("binance")
    denied = ledger.acquire(BINANCE_WEIGHT, cost=1, priority=Priority.OPPORTUNISTIC)
    assert denied.reason is DenyReason.OPPORTUNISTIC_FROZEN

    clock.advance_ms(HOUR_MS)
    assert not ledger.is_opportunistic_frozen("binance")


def test_a_418_on_one_venue_leaves_the_other_alone(ledger: RateLimitLedger) -> None:
    """Limits are per venue per IP; a Binance ban is not a Hyperliquid ban."""
    ledger.observe_response(BINANCE_WEIGHT, status=418, retry_after_s=120)
    assert ledger.is_halted("binance")
    assert not ledger.is_halted("hyperliquid")
    assert ledger.acquire(HL_WEIGHT, cost=20, priority=Priority.FAST_LANE).granted


def test_a_418_emits_an_ip_ban_event_that_demands_an_f4_message(
    ledger: RateLimitLedger,
) -> None:
    """§6: → `ingest_gap(cause=ip_ban)` → 立即发 F4 私聊."""
    ledger.observe_response(BINANCE_WEIGHT, status=418, retry_after_s=7_200)
    (event,) = ledger.drain_events()
    assert event.kind is EventKind.IP_BAN
    assert event.gap_cause is GapCause.IP_BAN
    assert event.notify_f4 is True
    assert event.ops_event is True
    assert event.detail["retry_after_s"] == 7_200
    assert event.detail["halted_lanes"] == 3  # all three Binance buckets


def test_a_418_without_a_retry_after_assumes_the_longest_documented_ban(
    ledger: RateLimitLedger,
) -> None:
    """04 §2: `Retry-After` 2min–3 天递增. Guessing short earns another 418."""
    ledger.observe_response(BINANCE_WEIGHT, status=418)
    (event,) = ledger.drain_events()
    assert event.detail["retry_after_s"] == 3 * 24 * 3600
    assert event.detail["retry_after_present"] is False


# --------------------------------------------------------------------------- #
# WebSocket accounting
# --------------------------------------------------------------------------- #
def test_binance_allows_300_connections_per_five_minutes(
    ledger: RateLimitLedger, clock: FakeClock
) -> None:
    """§6 / 04 §2: 每 IP 每 5 分钟 ≤ 300 次连接；重连风暴要记账并退避."""
    ws = ledger.ws("binance")
    for index in range(300):
        assert ws.open(f"c{index}", clock.monotonic_ms()), index
        ws.close(f"c{index}")
    denied = ws.open("storm", clock.monotonic_ms())
    assert not denied.granted
    assert denied.reason is DenyReason.WS_CONNECTION_RATE
    assert denied.retry_after_ms == 5 * MINUTE_MS + 1

    clock.advance_ms(5 * MINUTE_MS + 1)
    assert ws.open("after", clock.monotonic_ms())


def test_binance_connections_are_rotated_at_24_hours(
    ledger: RateLimitLedger, clock: FakeClock
) -> None:
    ws = ledger.ws("binance")
    assert ws.open("market", clock.monotonic_ms(), streams=2)
    clock.advance_ms(24 * 60 * MINUTE_MS - 1)
    assert ws.due_for_rotation(clock.monotonic_ms()) == ()
    clock.advance_ms(1)
    assert ws.due_for_rotation(clock.monotonic_ms()) == ("market",)


def test_hyperliquid_websocket_seats_are_80_percent_of_the_official_numbers(
    ledger: RateLimitLedger, clock: FakeClock
) -> None:
    """§6.1 row 4: 官方 10 连接 / 1000 订阅 / 10 user，取 80% → **8 / 800 / 8**."""
    ws = ledger.ws("hyperliquid")
    assert ws.spec.max_connections == 8
    assert ws.spec.max_subscriptions == 800
    assert ws.spec.max_distinct_users == 8

    for index in range(8):
        assert ws.open(f"c{index}", clock.monotonic_ms())
    denied = ws.open("ninth", clock.monotonic_ms())
    assert denied.reason is DenyReason.WS_CONNECTION_SEATS

    for index in range(8):
        assert ws.claim_user(f"0xuser{index}")
    assert ws.claim_user("0xuser0")  # already held, not a new seat
    assert ws.claim_user("0xninth").reason is DenyReason.WS_USER_SEATS


def test_hyperliquid_subscription_seats_are_bounded(
    ledger: RateLimitLedger, clock: FakeClock
) -> None:
    ws = ledger.ws("hyperliquid")
    assert ws.open("a", clock.monotonic_ms(), streams=800)
    assert ws.subscriptions == 800
    assert ws.open("b", clock.monotonic_ms(), streams=1).reason is (
        DenyReason.WS_SUBSCRIPTION_SEATS
    )
    ws.close("a")
    assert ws.subscriptions == 0
    assert ws.open("c", clock.monotonic_ms(), streams=1)


# --------------------------------------------------------------------------- #
# Reloading the budget — §6.1's retirement procedure, step ③
# --------------------------------------------------------------------------- #
def test_reloading_a_smaller_reservation_reclaims_the_budget(
    ledger: RateLimitLedger, venues_path: Path, tmp_path: Path
) -> None:
    from conftest import reclaimed_consumers_yaml

    path = tmp_path / "egress-consumers.yaml"
    path.write_text(reclaimed_consumers_yaml(), encoding="utf-8")

    assert ledger.snapshot(HL_WEIGHT).our_ceiling_per_min == 127
    events = ledger.reload(LedgerConfig.load(venues_path, path))

    assert [event.kind for event in events] == [EventKind.BUDGET_RECLAIMED]
    assert events[0].detail == {
        "from_per_min": 127,
        "to_per_min": 1080,
        "reserved_from": 953,
        "reserved_to": 0,
        "profile_from": "transitional",
        "profile_to": "reclaimed",
    }
    snapshot = ledger.snapshot(HL_WEIGHT)
    assert snapshot.our_ceiling_per_min == 1080
    assert snapshot.opportunistic_available_per_min == 400
    assert snapshot.profile == "reclaimed"
