"""One bucket: a rolling one-minute window and the three-tier admission rule.

Priority is **three tiers, not two** (§6.1, review fix): ``fast lane >
everything else resident (slow lane, ratios, universe, WS) > opportunistic``.
The original two-tier reading gave the fast-lane floor and the resident reserve
the same number, so the floor (50) came out below the steady load (54) and the
opportunistic formula returned a negative budget.

The opportunistic formula is the corrected one::

    available = ceiling - max(reserve, resident actual usage)   , then the cap

and **not** ``budget - reserve - resident actual``, which subtracts resident
twice and is exactly the shape the review threw out.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from .config import BucketKey, ResolvedBucket

__all__ = ["WINDOW_MS", "Bucket", "DenyReason", "Grant", "Priority"]

#: Every documented limit in docs/04 is expressed per minute.
WINDOW_MS = 60_000


class Priority(StrEnum):
    """The three tiers of §6.1, most privileged first."""

    FAST_LANE = "fast_lane"
    RESIDENT = "resident"
    OPPORTUNISTIC = "opportunistic"

    @property
    def rank(self) -> int:
        return _RANKS[self]

    @property
    def is_resident(self) -> bool:
        """The fast lane is resident too — it is the *protected part* of it."""
        return self is not Priority.OPPORTUNISTIC


_RANKS = {Priority.FAST_LANE: 0, Priority.RESIDENT: 1, Priority.OPPORTUNISTIC: 2}


class DenyReason(StrEnum):
    """Why a request was not admitted. Distinct reasons, on purpose:
    "we are banned" and "come back in 800 ms" call for different behaviour."""

    VENUE_HALTED = "venue_halted"
    OPPORTUNISTIC_FROZEN = "opportunistic_frozen"
    CEILING = "ceiling"
    FAST_LANE_FLOOR = "fast_lane_floor"
    OPPORTUNISTIC_BUDGET = "opportunistic_budget"
    PACED = "paced"
    COST_EXCEEDS_CEILING = "cost_exceeds_ceiling"
    WS_CONNECTION_RATE = "ws_connection_rate"
    WS_CONNECTION_SEATS = "ws_connection_seats"
    WS_SUBSCRIPTION_SEATS = "ws_subscription_seats"
    WS_USER_SEATS = "ws_user_seats"
    WS_STREAMS_PER_CONNECTION = "ws_streams_per_connection"


@dataclass(slots=True)
class _Spend:
    """One admitted request's charge against the window."""

    at_ms: int
    cost: int
    priority: Priority


@dataclass(frozen=True, slots=True)
class Grant:
    """The ledger's answer. Hand it back to ``settle`` when the call is over."""

    key: BucketKey
    priority: Priority
    cost: int
    granted: bool
    at_ms: int
    reason: DenyReason | None = None
    retry_after_ms: int = 0
    lane: str | None = None
    entry: _Spend | None = field(default=None, repr=False, compare=False)

    def __bool__(self) -> bool:
        return self.granted


class Bucket:
    """The rolling window and the admission arithmetic for one bucket."""

    __slots__ = ("_spend", "_window_ms", "spec")

    def __init__(self, spec: ResolvedBucket, *, window_ms: int = WINDOW_MS) -> None:
        self.spec = spec
        self._window_ms = window_ms
        self._spend: deque[_Spend] = deque()

    @property
    def key(self) -> BucketKey:
        return self.spec.key

    @property
    def window_ms(self) -> int:
        return self._window_ms

    # ----------------------------------------------------------------- #
    # Window bookkeeping
    # ----------------------------------------------------------------- #
    def prune(self, now_ms: int) -> None:
        cutoff = now_ms - self._window_ms
        while self._spend and self._spend[0].at_ms <= cutoff:
            self._spend.popleft()

    def used(self, now_ms: int, *priorities: Priority) -> int:
        self.prune(now_ms)
        wanted = set(priorities) if priorities else None
        return sum(
            entry.cost
            for entry in self._spend
            if wanted is None or entry.priority in wanted
        )

    def resident_used(self, now_ms: int) -> int:
        return self.used(now_ms, Priority.FAST_LANE, Priority.RESIDENT)

    def opportunistic_available(self, now_ms: int) -> int:
        return self.spec.opportunistic_available(self.resident_used(now_ms))

    def _expiry_ms(self, now_ms: int) -> int:
        """When the oldest charge falls out of the window."""
        self.prune(now_ms)
        if not self._spend:
            return 0
        return max(1, self._spend[0].at_ms + self._window_ms - now_ms + 1)

    # ----------------------------------------------------------------- #
    # Admission
    # ----------------------------------------------------------------- #
    def admit(
        self,
        now_ms: int,
        *,
        cost: int,
        priority: Priority,
        resident_ceiling: int,
        lane: str | None = None,
    ) -> Grant:
        """Answer yes or no. Never sleeps, never sends anything.

        ``resident_ceiling`` is our ceiling after any AIMD degradation; the
        caller (the ledger) owns that state because a 429 degrades a whole
        venue, not one bucket.
        """
        if cost <= 0:
            raise ValueError("a request costs at least 1 (weight or one request)")
        self.prune(now_ms)

        used_total = self.used(now_ms)
        used_fast = self.used(now_ms, Priority.FAST_LANE)
        used_opportunistic = self.used(now_ms, Priority.OPPORTUNISTIC)

        # The fast-lane floor: whatever the fast lane has not yet spent of its
        # floor is fenced off from every other resident lane, at all times
        # (§6.1, Binance weight row).
        fenced = max(0, self.spec.fast_lane_floor_per_min - used_fast)

        if priority is Priority.FAST_LANE:
            headroom = resident_ceiling - used_total
            reason: DenyReason | None = DenyReason.CEILING
        elif priority is Priority.RESIDENT:
            headroom = resident_ceiling - fenced - used_total
            reason = DenyReason.FAST_LANE_FLOOR if fenced else DenyReason.CEILING
        else:
            budget = self.spec.opportunistic_available(self.resident_used(now_ms))
            headroom = min(
                budget - used_opportunistic,
                resident_ceiling - fenced - used_total,
            )
            reason = (
                DenyReason.OPPORTUNISTIC_BUDGET
                if budget - used_opportunistic <= resident_ceiling - fenced - used_total
                else DenyReason.CEILING
            )

        if cost <= headroom:
            entry = _Spend(at_ms=now_ms, cost=cost, priority=priority)
            self._spend.append(entry)
            return Grant(
                key=self.key,
                priority=priority,
                cost=cost,
                granted=True,
                at_ms=now_ms,
                lane=lane,
                entry=entry,
            )

        if cost > self.spec.our_ceiling_per_min:
            # No amount of waiting fixes this one; say so instead of asking the
            # caller to spin.
            return Grant(
                key=self.key,
                priority=priority,
                cost=cost,
                granted=False,
                at_ms=now_ms,
                reason=DenyReason.COST_EXCEEDS_CEILING,
                lane=lane,
            )
        return Grant(
            key=self.key,
            priority=priority,
            cost=cost,
            granted=False,
            at_ms=now_ms,
            reason=reason,
            retry_after_ms=self._expiry_ms(now_ms),
            lane=lane,
        )

    def settle(self, grant: Grant, actual_cost: int) -> None:
        """Correct the charge once the real cost is known.

        A weight is an estimate until the response arrives (a full-market
        endpoint charges 10 and a per-symbol one charges 1). The entry stays
        in the window at its real size.
        """
        if not grant.granted or grant.entry is None:
            raise ValueError("only a granted acquisition can be settled")
        if actual_cost < 0:
            raise ValueError("a request cannot cost less than nothing")
        grant.entry.cost = actual_cost

    def release(self, grant: Grant) -> None:
        """Give the charge back — the call never happened (it was never sent)."""
        self.settle(grant, 0)
