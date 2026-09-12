"""Per-egress-IP, per-venue rate-limit ledger.

Three facts drive this design (06 §4, and the hub's measurements):

1. **The accounting key is the public egress IP, not the process or the host.**
   Two machines behind one NAT share one budget; one machine that redials gets a
   fresh one. So budgets live in a registry keyed by ``(egress_ip, venue)`` and every
   collector in the process asks for the same object.
2. **Requests are not the unit.** Binance and Hyperliquid bill *weight*, and HL bills
   history endpoints by rows returned -- which you only know after the response. Hence
   ``reserve(upper_bound) -> call -> settle(actual)``.
3. **Concurrency is a separate limiter.** Above ~10 in-flight requests Hyperliquid
   trips a connection-rate limiter that has nothing to do with the weight budget, so
   more concurrency buys zero throughput. ``max_inflight`` is a hard gate, and a
   connection-kind 429 shrinks it instead of the weight budget.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .venues import BucketSpec, VenueSpec
from .venues import venue as _venue_spec


class BudgetExhausted(RuntimeError):
    """Not enough budget and the caller asked not to wait."""


class RateLimitKind(StrEnum):
    """Which limiter we hit. They need opposite reactions, so never merge them."""

    weight = "weight"
    connection = "connection"
    ban = "ban"
    unknown = "unknown"


def classify_rate_limit(status: int, body: str | bytes | None, *, venue: str) -> RateLimitKind:
    """Tell the two Hyperliquid 429s apart -- by body, because the status is identical.

    **The body test applies to Hyperliquid only.** HL is the one venue we have measured
    running two independent limiters behind one status code:

    * body is JSON ``null`` -> HL's application-level **weight** limiter: back off on
      weight, the connection count is fine.
    * body is nginx's HTML ``429 Too Many Requests`` page -> **connection/request-rate**
      limiter: lower ``max_inflight``; the weight budget is not even spent.

    Every other venue serves its 429s through whatever proxy it happens to use, so an
    HTML error page from Binance says nothing about *which* limiter fired -- it is a
    plain rate limit and must cut the weight budget. Reading it as "too many sockets"
    would leave the budget untouched and walk us straight into the 418 ban.

    Binance's 418 is a straight IP ban (``Retry-After`` grows from 2 min to 3 days) and
    is reported as :attr:`RateLimitKind.ban`.
    """
    if status == 418:
        return RateLimitKind.ban
    if status not in (403, 429):
        return RateLimitKind.unknown
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else (body or "")
    stripped = text.strip()
    lowered = stripped.lower()
    if venue == "hyperliquid":
        if lowered.startswith("<") or "<html" in lowered[:200] or "nginx" in lowered:
            return RateLimitKind.connection
        # HL's weight limiter answers with a bare JSON null (and nothing else).
        return RateLimitKind.weight if stripped in ("null", '"null"', "") else RateLimitKind.unknown
    if status == 403:
        # Bybit answers 403 both for "access too frequent" and for a geo block.
        return RateLimitKind.weight if "frequent" in lowered else RateLimitKind.unknown
    return RateLimitKind.weight


@dataclass(slots=True)
class _Entry:
    at: float
    cost: float


class _Window:
    """A sliding-window ledger: at most ``capacity`` units within the last ``window_s``."""

    def __init__(self, spec: BucketSpec) -> None:
        self.spec = spec
        self.window_s = spec.window_s
        self.base_capacity = float(spec.budget)
        self.entries: deque[_Entry] = deque()

    def expire(self, now: float) -> None:
        cutoff = now - self.window_s
        while self.entries and self.entries[0].at <= cutoff:
            self.entries.popleft()

    def used(self, now: float) -> float:
        self.expire(now)
        return sum(e.cost for e in self.entries)

    def charge(self, now: float, cost: float) -> _Entry:
        entry = _Entry(at=now, cost=cost)
        self.entries.append(entry)
        return entry

    def wait_for(self, now: float, need: float) -> float:
        """Seconds until ``need`` units of room free up. ``inf`` if never (too big)."""
        freed = 0.0
        for e in self.entries:
            freed += e.cost
            if freed >= need:
                return max(0.0, e.at + self.window_s - now)
        return float("inf")


@dataclass(slots=True)
class Reservation:
    reservation_id: int
    endpoint: str
    reserved: float
    _entries: list[_Entry] = field(default_factory=list, repr=False)
    settled: bool = False
    actual: float | None = None

    @property
    def delta(self) -> float:
        return 0.0 if self.actual is None else self.actual - self.reserved


@dataclass(slots=True)
class BudgetStats:
    reserved_total: float = 0.0
    settled_total: float = 0.0
    refunded: float = 0.0
    overdrawn: float = 0.0
    waits: int = 0
    wait_seconds: float = 0.0
    rate_limited: int = 0
    rate_limited_by_kind: dict[str, int] = field(default_factory=dict)
    header_syncs: int = 0
    header_corrections: float = 0.0


class Budget:
    """One venue's ledger for one egress IP.

    Use :meth:`call` for fixed-cost endpoints and the explicit
    :meth:`reserve`/:meth:`settle` pair when the real cost is only known from the
    response (HL history endpoints, Binance tiered klines).
    """

    _registry: dict[tuple[str, str], Budget] = {}

    def __init__(
        self,
        egress_ip: str,
        spec: VenueSpec,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        if not egress_ip:
            raise ValueError("budgets are keyed by public egress IP; it must be known")
        self.egress_ip = egress_ip
        self.spec = spec
        self.venue = spec.name
        self._clock = clock
        self._sleep = sleep
        self._main = _Window(spec.main)
        self._extra: dict[str, _Window] = {}
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()
        self.stats = BudgetStats()
        # AIMD state: multiplicative cut on 429, frozen for an hour, then additive
        # recovery one step per window until we are back at the configured budget.
        self._factor = 1.0
        self._frozen_until = 0.0
        self._last_recover = self._clock()
        # A hard block is the venue telling us when to come back (Retry-After), or a
        # ban. It is time, not budget: it must survive AIMD recovery and it must not be
        # a ledger entry, because a Binance 418 can be three days long and the window is
        # sixty seconds.
        self._hard_block_until = 0.0
        # in-flight gate (resizable, unlike asyncio.Semaphore)
        self.max_inflight = spec.max_inflight
        self._inflight = 0
        self._inflight_cv = asyncio.Condition()
        # Concurrency AIMD, mirroring the weight one: a connection-kind 429 cuts
        # max_inflight by one and freezes it, then it climbs back one per clean window.
        # The flag keeps recovery away from a max_inflight a caller set by hand.
        self._inflight_recovering = False
        self._inflight_frozen_until = 0.0
        self._last_inflight_recover = self._clock()

    # -- construction -------------------------------------------------------

    @classmethod
    def for_venue(cls, egress_ip: str, venue_name: str, **kw: Any) -> Budget:
        """Get (or create) the shared ledger for this egress IP and venue."""
        key = (egress_ip, venue_name)
        existing = cls._registry.get(key)
        if existing is not None:
            return existing
        b = cls(egress_ip, _venue_spec(venue_name), **kw)
        cls._registry[key] = b
        return b

    @classmethod
    def reset_registry(cls) -> None:
        cls._registry.clear()

    # -- capacity -----------------------------------------------------------

    def _recover(self, now: float) -> None:
        """Additive increase for both limiters, one step per clean window."""
        if self._factor < 1.0 and now >= self._frozen_until:
            steps, rem = divmod(now - self._last_recover, self._main.window_s)
            if steps >= 1:
                self._factor = min(1.0, self._factor + self.spec.aimd_recover_per_window * steps)
                self._last_recover = now - rem
        if self._inflight_recovering and now >= self._inflight_frozen_until:
            steps, rem = divmod(now - self._last_inflight_recover, self._main.window_s)
            if steps >= 1:
                self.max_inflight = min(self.spec.max_inflight, self.max_inflight + int(steps))
                self._last_inflight_recover = now - rem
                if self.max_inflight >= self.spec.max_inflight:
                    self._inflight_recovering = False

    @property
    def capacity(self) -> float:
        """Current allowance: the configured budget scaled by the AIMD factor."""
        now = self._clock()
        self._recover(now)
        return self._main.base_capacity * self._factor

    @property
    def factor(self) -> float:
        self._recover(self._clock())
        return self._factor

    @property
    def available(self) -> float:
        """Spendable right now. A hard block reports zero however full the window is."""
        now = self._clock()
        if now < self._hard_block_until:
            return 0.0
        return max(0.0, self.capacity - self._main.used(now))

    @property
    def blocked_for(self) -> float:
        """Seconds until the venue's Retry-After / ban expires. 0 when not blocked."""
        return max(0.0, self._hard_block_until - self._clock())

    def used(self) -> float:
        return self._main.used(self._clock())

    def _window_for(self, endpoint: str) -> _Window | None:
        bucket = self.spec.bucket_for(endpoint)
        if bucket is None:
            return None
        w = self._extra.get(bucket.name)
        if w is None:
            w = self._extra[bucket.name] = _Window(bucket)
        return w

    def _capacity_of(self, w: _Window) -> float:
        return w.base_capacity * (self._factor if w is self._main else 1.0)

    # -- reserve / settle ---------------------------------------------------

    def weight_for(self, endpoint: str, *, weight: int | None = None) -> int:
        return self.spec.weight_for(endpoint, override=weight)

    async def reserve(
        self, endpoint: str, weight: int | None = None, *, wait: bool = True
    ) -> Reservation:
        """Book ``weight`` units up front. ``weight=None`` uses the endpoint table.

        Weight-0 endpoints (Binance ``/futures/data/*``) still take a slot: they are
        free by weight but are absolutely not free by request rate, so they are charged
        a floor of 1 against the ledger.
        """
        cost = float(max(1, self.weight_for(endpoint, weight=weight)))
        async with self._lock:
            while True:
                now = self._clock()
                self._recover(now)

                blocked = self._hard_block_until - now
                if blocked > 0:
                    # The venue named a time. Nothing in the ledger can shorten it.
                    if not wait:
                        raise BudgetExhausted(
                            f"{self.venue}:{endpoint} is hard-blocked for "
                            f"{blocked:.0f}s (Retry-After / ban)"
                        )
                    self.stats.waits += 1
                    self.stats.wait_seconds += blocked
                    await self._sleep(min(blocked, self._main.window_s))
                    continue

                targets: list[_Window] = [self._main]
                extra = self._window_for(endpoint)
                if extra is not None:
                    targets.append(extra)
                waits = []
                for w in targets:
                    # Compare against the UNSCALED budget: a request too big for the
                    # configured budget can never be sent and must fail now. One that
                    # only exceeds the AIMD-reduced capacity is merely early, and takes
                    # the normal wait path until the factor recovers.
                    if cost > w.base_capacity:
                        raise BudgetExhausted(
                            f"{self.venue}:{endpoint} costs {cost:.0f} but the whole "
                            f"budget is {w.base_capacity:.0f}; split the request "
                            f"instead of waiting"
                        )
                    short = w.used(now) + cost - self._capacity_of(w)
                    if short > 0:
                        waits.append(w.wait_for(now, short))
                if not waits:
                    entries = [w.charge(now, cost) for w in targets]
                    break
                delay = max(waits)
                if not wait:
                    raise BudgetExhausted(
                        f"{self.venue}:{endpoint} needs {cost:.0f}; "
                        f"{self.available:.0f} available, {delay:.2f}s to wait"
                    )
                # wait_for returns inf when the window alone cannot free enough room
                # (we are under an AIMD cut): re-check once a window has rolled.
                step = self._main.window_s if delay == float("inf") else min(
                    delay, self._main.window_s
                )
                self.stats.waits += 1
                self.stats.wait_seconds += step
                await self._sleep(step)
        self.stats.reserved_total += cost
        return Reservation(
            reservation_id=next(self._ids), endpoint=endpoint, reserved=cost, _entries=entries
        )

    def settle(self, res: Reservation, actual_weight: float) -> float:
        """Book the real cost. Returns ``actual - reserved``.

        Must be called even when the request failed: the venue charged us anyway.
        """
        if res.settled:
            raise RuntimeError(f"reservation {res.reservation_id} already settled")
        if actual_weight < 0:
            raise ValueError("actual weight cannot be negative")
        res.settled = True
        res.actual = float(actual_weight)
        delta = res.delta
        for entry in res._entries:
            entry.cost = float(actual_weight)
        self.stats.settled_total += actual_weight
        if delta > 0:
            self.stats.overdrawn += delta
        else:
            self.stats.refunded += -delta
        return delta

    @asynccontextmanager
    async def call(self, endpoint: str, weight: int | None = None):
        """Hold an in-flight slot, then reserve, for the duration of one request.

        **The slot is taken first, and that order is the point.** Reserving first would
        stamp the ledger at the moment the coroutine queued rather than the moment the
        request went out; with a narrow ``max_inflight`` a burst of callers would book
        their whole cost in one instant, let it age out of the 60 s window while they sat
        in the queue, and then send for real against a ledger that reads empty. Taking
        the slot first means an entry is written when the request is actually about to
        leave, so the window describes traffic the venue saw.

        Yields the :class:`Reservation`; settle it inside the block when the true cost
        is only known from the response, otherwise it settles at the reserved amount.
        """
        async with self._inflight_cv:
            self._recover(self._clock())
            while self._inflight >= self.max_inflight:
                await self._inflight_cv.wait()
                self._recover(self._clock())
            self._inflight += 1
        try:
            res = await self.reserve(endpoint, weight)
        except BaseException:
            async with self._inflight_cv:
                self._inflight -= 1
                self._inflight_cv.notify()
            raise
        try:
            yield res
        finally:
            async with self._inflight_cv:
                self._inflight -= 1
                self._inflight_cv.notify()
            if not res.settled:
                self.settle(res, res.reserved)

    # -- feedback -----------------------------------------------------------

    def on_rate_limited(
        self, kind: RateLimitKind = RateLimitKind.weight, *, retry_after_s: float | None = None
    ) -> None:
        """React to a 429/418. Weight-kind shrinks the budget, connection-kind the concurrency.

        Weight and ban kinds: multiplicative decrease to 75% of the current allowance,
        frozen for an hour (no additive recovery during the freeze), plus a *hard block*
        until ``retry_after_s`` -- or one window when the venue named no time. The hard
        block is held here rather than by the calling coroutine so that every other task
        behind this egress IP stops too; a ban is a property of the IP, not of one
        request.

        Connection kind: the weight ledger is fine, we simply opened too many sockets, so
        ``max_inflight`` drops by one and is frozen for the same hour before climbing
        back one per clean window.
        """
        now = self._clock()
        self.stats.rate_limited += 1
        self.stats.rate_limited_by_kind[kind.value] = (
            self.stats.rate_limited_by_kind.get(kind.value, 0) + 1
        )
        if kind is RateLimitKind.connection:
            self.max_inflight = max(1, self.max_inflight - 1)
            self._inflight_recovering = self.max_inflight < self.spec.max_inflight
            self._inflight_frozen_until = now + self.spec.aimd_freeze_s
            self._last_inflight_recover = self._inflight_frozen_until
            return
        self._factor = max(0.05, self._factor * self.spec.aimd_cut)
        self._frozen_until = now + self.spec.aimd_freeze_s
        self._last_recover = self._frozen_until
        # Our ledger and the venue's disagree; stop sending until the venue says we may.
        penalty = retry_after_s if retry_after_s is not None else self._main.window_s
        self._hard_block_until = max(self._hard_block_until, now + max(0.0, penalty))

    def sync_used(self, used: float) -> float:
        """Reconcile the ledger against a venue-reported usage figure.

        Binance publishes ``X-MBX-USED-WEIGHT-1M``; Gate publishes remaining/limit/reset
        (see :meth:`sync_remaining`). Both are in the same unit as our ledger, but they
        count against the *official* ceiling while we count against our 40% budget --
        which is exactly why the venue's number wins: anything else behind this egress
        IP is spending from the same ceiling and our ledger cannot see it. When the
        venue says we used more than we booked, top the ledger up. Returns the
        correction applied (0 when we were already at or above the reported figure).
        """
        now = self._clock()
        ours = self._main.used(now)
        self.stats.header_syncs += 1
        correction = float(used) - ours
        if correction > 0:
            self._main.charge(now, correction)
            self.stats.header_corrections += correction
            return correction
        return 0.0

    def sync_remaining(self, remaining: float, limit: float) -> float:
        """Gate-style headers: ``x-gate-ratelimit-requests-remain`` out of ``-limit``."""
        return self.sync_used(max(0.0, float(limit) - float(remaining)))

    def snapshot(self) -> dict[str, Any]:
        return {
            "egress_ip": self.egress_ip,
            "venue": self.venue,
            "unit": self.spec.main.unit,
            "limit": self.spec.main.limit,
            "budget": self.spec.main.budget,
            "source": self.spec.source,
            "capacity": round(self.capacity, 2),
            "used": round(self.used(), 2),
            "available": round(self.available, 2),
            "aimd_factor": round(self.factor, 3),
            "hard_block_s": round(self.blocked_for, 2),
            "max_inflight": self.max_inflight,
            "inflight": self._inflight,
            "rate_limited": self.stats.rate_limited,
            "rate_limited_by_kind": dict(self.stats.rate_limited_by_kind),
        }
