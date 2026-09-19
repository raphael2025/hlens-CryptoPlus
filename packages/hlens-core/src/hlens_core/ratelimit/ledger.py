"""The ledger itself: accounting and admission, and nothing else.

Three things it deliberately does not do (§6.1, seam ③):

* **It sends no request.** The caller asks for an allowance, gets a yes or a
  no, and afterwards tells the ledger what the call actually cost and what
  status came back. Moving the HTTP call in here would break seam ③ and, worse,
  would make the whole state machine untestable offline.
* **It writes no table.** A 429, a 418 and a budget change each produce a typed
  :class:`~hlens_core.ratelimit.events.LedgerEvent`; the collector turns those
  into ``ingest_gap`` and ``ops_event`` rows and into the F4 private message.
  ``ratelimit`` is not the writer of any table in §4's single-writer map.
* **It reads no clock of its own.** See :mod:`hlens_core.ratelimit.clock`.

The response policy, straight from the documents:

======  ===========================================================
429     opportunistic stops for an hour; resident is only slowed
        (AIMD, cut to 75 %). Emits ``ingest_gap(cause=rate_limit)``.
        On Hyperliquid it also always emits an F4 private message —
        on a shared egress IP a 429 means the other consumer may be
        getting hit too (§6.1).
418     read ``Retry-After``, then **stop every lane of that venue**
        — not a 25 % cut. Emits ``ingest_gap(cause=ip_ban)`` and an
        immediate F4 private message. 418 bans the machine's IP, and
        the machine is shared (§6 / §6.1).
======  ===========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .buckets import Bucket, DenyReason, Grant, Priority
from .clock import Clock, SystemClock
from .config import BucketKey, BucketKind, LedgerConfig, ResolvedBucket
from .events import EventKind, GapCause, HlBodyKind, LedgerEvent
from .pacing import BurstShaper, PacedLane
from .ws import WsLedger

__all__ = ["BucketSnapshot", "RateLimitLedger", "classify_hl_429_body"]

#: 04 §2: a 418's ``Retry-After`` runs from 2 minutes to 3 days and grows with
#: repetition. Used only when the header is absent — guessing short on a banned
#: IP earns another 418, on a machine whose egress is shared.
MAX_DOCUMENTED_BAN_S = 3 * 24 * 60 * 60


def classify_hl_429_body(body: str | bytes | None) -> HlBodyKind:
    """Which Hyperliquid 429 this is, from its body (04 §3, 实测).

    Hyperliquid's responses carry no rate-limit headers at all, so the body is
    the only evidence: a JSON ``null`` is the weight limiter, an nginx HTML
    page is the connection-rate limiter. The two need opposite remedies —
    spend less weight, or open fewer connections — so guessing is not an
    option, and an unrecognized body is reported as unrecognized rather than
    assumed to be either.
    """
    if body is None:
        return HlBodyKind.UNRECOGNIZED
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    stripped = text.strip()
    if stripped.lower() == "null":
        return HlBodyKind.WEIGHT
    if stripped.startswith("<"):
        return HlBodyKind.CONNECTION
    return HlBodyKind.UNRECOGNIZED


@dataclass(frozen=True, slots=True)
class BucketSnapshot:
    """Everything preflight prints and a test asserts, for one bucket."""

    key: BucketKey
    kind: BucketKind
    official_limit_per_min: int
    share: Fraction
    egress_ceiling_per_min: int
    reserved_per_min: int
    our_ceiling_per_min: int
    profile: str
    resident_steady_per_min: int
    reserve_per_min: int
    fast_lane_floor_per_min: int
    opportunistic_hard_cap_per_min: int
    used_fast_per_min: int
    used_resident_per_min: int
    used_opportunistic_per_min: int
    opportunistic_available_per_min: int
    resident_factor: Fraction
    resident_ceiling_per_min: int
    halted: bool
    halted_until_ms: int | None
    opportunistic_frozen: bool
    opportunistic_frozen_until_ms: int | None


@dataclass(slots=True)
class _VenueState:
    """The venue-wide part of the state. A 429 or a 418 hits a venue, not a bucket:
    ``/futures/data/*`` and ``/fapi/*`` are one IP to Binance (§6)."""

    resident_factor: Fraction = Fraction(1)
    factor_updated_ms: int = 0
    opportunistic_frozen_until_ms: int | None = None
    halted_until_ms: int | None = None
    inflight_limit: int | None = None
    inflight_updated_ms: int = 0


class RateLimitLedger:
    """Per public egress IP, per venue. One instance per process."""

    def __init__(self, config: LedgerConfig, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._config = config
        self._buckets: dict[BucketKey, Bucket] = {}
        self._venues: dict[str, _VenueState] = {}
        self._ws: dict[str, WsLedger] = {}
        self._lanes: dict[str, PacedLane] = {}
        self._events: list[LedgerEvent] = []
        self._install(config)

    # ----------------------------------------------------------------- #
    # Construction
    # ----------------------------------------------------------------- #
    @classmethod
    def from_files(
        cls,
        venues_path: Path | str,
        consumers_path: Path | str,
        *,
        clock: Clock | None = None,
    ) -> RateLimitLedger:
        return cls(LedgerConfig.load(venues_path, consumers_path), clock=clock)

    def _install(self, config: LedgerConfig) -> None:
        now = self._clock.monotonic_ms()
        self._buckets = {key: Bucket(spec) for key, spec in config.buckets.items()}
        for venue in config.venues():
            spec = config.ws.get(venue)
            state = self._venues.setdefault(venue, _VenueState(factor_updated_ms=now))
            if spec is not None:
                self._ws[venue] = WsLedger(spec)
                if spec.max_inflight is not None:
                    state.inflight_limit = spec.max_inflight
                    state.inflight_updated_ms = now

    @property
    def config(self) -> LedgerConfig:
        return self._config

    @property
    def clock(self) -> Clock:
        return self._clock

    def reload(self, config: LedgerConfig) -> tuple[LedgerEvent, ...]:
        """Apply a new budget without losing the window or the AIMD state.

        This is step ③ of §6.1's reclamation procedure — "重跑 preflight，确认
        打印出的 HL 可用预算变成 1080/分" — with the ``ops_event`` it requires
        produced as an event rather than written here.
        """
        produced: list[LedgerEvent] = []
        for key, new in config.buckets.items():
            old = self._config.buckets.get(key)
            if old is None or old.our_ceiling_per_min == new.our_ceiling_per_min:
                continue
            reclaimed = new.our_ceiling_per_min > old.our_ceiling_per_min
            produced.append(
                LedgerEvent(
                    kind=EventKind.BUDGET_RECLAIMED if reclaimed else EventKind.BUDGET_CHANGED,
                    venue=key.venue,
                    bucket=key.bucket,
                    ts=self._clock.now_ms(),
                    ops_event=True,
                    detail={
                        "from_per_min": old.our_ceiling_per_min,
                        "to_per_min": new.our_ceiling_per_min,
                        "reserved_from": old.reserved_per_min,
                        "reserved_to": new.reserved_per_min,
                        "profile_from": old.profile.name,
                        "profile_to": new.profile.name,
                    },
                )
            )
        for key, spec in config.buckets.items():
            bucket = self._buckets.get(key)
            if bucket is None:
                self._buckets[key] = Bucket(spec)
            else:
                bucket.spec = spec
        for key in set(self._buckets) - set(config.buckets):
            del self._buckets[key]
        self._config = config
        self._events.extend(produced)
        return tuple(produced)

    # ----------------------------------------------------------------- #
    # Lanes
    # ----------------------------------------------------------------- #
    def register_paced_lane(
        self, name: str, key: BucketKey | str, *, total: int, window_ms: int
    ) -> PacedLane:
        """Declare a lane whose window must be spread evenly (§6, burst shaping)."""
        bucket_key = self._key(key)
        lane = PacedLane(name, bucket_key, BurstShaper(total, window_ms), clock=self._clock)
        self._lanes[name] = lane
        return lane

    def lane(self, name: str) -> PacedLane:
        lane = self._lanes.get(name)
        if lane is None:
            raise KeyError(f"no paced lane named {name!r}")
        return lane

    # ----------------------------------------------------------------- #
    # Admission
    # ----------------------------------------------------------------- #
    def acquire(
        self,
        key: BucketKey | str,
        *,
        cost: int,
        priority: Priority,
        lane: str | None = None,
    ) -> Grant:
        """Ask for an allowance. Returns a :class:`Grant`; sends nothing."""
        bucket_key = self._key(key)
        bucket = self._bucket(bucket_key)
        now = self._clock.monotonic_ms()
        state = self._venue(bucket_key.venue)
        self._recover(bucket_key.venue, now)

        if state.halted_until_ms is not None:
            if now < state.halted_until_ms:
                return Grant(
                    key=bucket_key,
                    priority=priority,
                    cost=cost,
                    granted=False,
                    at_ms=now,
                    reason=DenyReason.VENUE_HALTED,
                    retry_after_ms=state.halted_until_ms - now,
                    lane=lane,
                )
            state.halted_until_ms = None

        if priority is Priority.OPPORTUNISTIC and state.opportunistic_frozen_until_ms is not None:
            if now < state.opportunistic_frozen_until_ms:
                return Grant(
                    key=bucket_key,
                    priority=priority,
                    cost=cost,
                    granted=False,
                    at_ms=now,
                    reason=DenyReason.OPPORTUNISTIC_FROZEN,
                    retry_after_ms=state.opportunistic_frozen_until_ms - now,
                    lane=lane,
                )
            state.opportunistic_frozen_until_ms = None

        paced = self._lanes.get(lane) if lane is not None else None
        if paced is not None and not paced.ready(now):
            return Grant(
                key=bucket_key,
                priority=priority,
                cost=cost,
                granted=False,
                at_ms=now,
                reason=DenyReason.PACED,
                retry_after_ms=paced.wait_ms(now),
                lane=lane,
            )

        grant = bucket.admit(
            now,
            cost=cost,
            priority=priority,
            resident_ceiling=self.resident_ceiling(bucket_key),
            lane=lane,
        )
        if grant.granted and paced is not None:
            paced.take(now)
        return grant

    def settle(
        self,
        grant: Grant,
        *,
        actual_cost: int | None = None,
        status: int = 200,
        retry_after_s: float | None = None,
        body: str | bytes | None = None,
    ) -> None:
        """Tell the ledger what the call actually cost and what came back."""
        if not grant.granted:
            raise ValueError("a denied acquisition has nothing to settle")
        self._bucket(grant.key).settle(grant, grant.cost if actual_cost is None else actual_cost)
        self.observe_response(
            grant.key, status=status, retry_after_s=retry_after_s, body=body
        )

    def release(self, grant: Grant) -> None:
        """Hand the allowance back — the call was never made."""
        if grant.granted:
            self._bucket(grant.key).release(grant)

    def observe_response(
        self,
        key: BucketKey | str,
        *,
        status: int,
        retry_after_s: float | None = None,
        body: str | bytes | None = None,
    ) -> None:
        """Feed a status code in without a grant (a WebSocket handshake, say)."""
        bucket_key = self._key(key)
        if status == 429:
            self._on_429(bucket_key, body=body, retry_after_s=retry_after_s)
        elif status == 418:
            self._on_418(bucket_key, retry_after_s=retry_after_s)

    # ----------------------------------------------------------------- #
    # Responses
    # ----------------------------------------------------------------- #
    def _on_429(
        self, key: BucketKey, *, body: str | bytes | None, retry_after_s: float | None
    ) -> None:
        venue = key.venue
        state = self._venue(venue)
        policy = self._config.aimd[venue]
        now = self._clock.monotonic_ms()

        freeze_ms = policy.opportunistic_freeze_s.as_int * 1000
        frozen_until = now + freeze_ms
        state.opportunistic_frozen_until_ms = max(
            frozen_until, state.opportunistic_frozen_until_ms or 0
        )

        factor_floor = policy.resident_factor_floor.value
        state.resident_factor = max(
            factor_floor, state.resident_factor * policy.resident_factor_on_429.value
        )
        state.factor_updated_ms = now

        body_kind = classify_hl_429_body(body) if body is not None else None
        if body_kind is HlBodyKind.CONNECTION:
            self._reduce_inflight(venue, now)

        self._events.append(
            LedgerEvent(
                kind=EventKind.RATE_LIMIT,
                venue=venue,
                bucket=key.bucket,
                ts=self._clock.now_ms(),
                gap_cause=GapCause.RATE_LIMIT,
                ops_event=True,
                notify_f4=policy.notify_f4_on_429.value,
                detail={
                    "body_kind": None if body_kind is None else body_kind.value,
                    "resident_factor": float(state.resident_factor),
                    "opportunistic_frozen_for_s": freeze_ms // 1000,
                    "retry_after_s": retry_after_s,
                    "inflight_limit": state.inflight_limit,
                },
            )
        )

    def _on_418(self, key: BucketKey, *, retry_after_s: float | None) -> None:
        """418: stop every lane of this venue. Not a 25 % cut (§6).

        ``Retry-After`` on Binance ranges from 2 minutes to 3 days and grows
        with repetition; when the header is missing the longest documented
        value is assumed, because the failure mode of guessing short is another
        418 on a machine whose IP is already banned — and the IP is shared with
        a collector that is not ours.

        Two things happen on top of the full stop, in the direction the
        document points rather than beyond it. §6 says a 418 is "不是只砍 25%"
        — *not merely* a 25 % cut — so the AIMD decrease is applied as well as
        the stop, and the opportunistic freeze is at least as long as a plain
        429's. Coming back onto a just-unbanned shared IP at full resident rate
        is how the second 418 is earned.
        """
        venue = key.venue
        state = self._venue(venue)
        policy = self._config.aimd[venue]
        now = self._clock.monotonic_ms()
        wait_s = retry_after_s if retry_after_s is not None else MAX_DOCUMENTED_BAN_S
        state.halted_until_ms = max(state.halted_until_ms or 0, now + int(wait_s * 1000))
        state.opportunistic_frozen_until_ms = max(
            state.halted_until_ms,
            now + policy.opportunistic_freeze_s.as_int * 1000,
            state.opportunistic_frozen_until_ms or 0,
        )
        state.resident_factor = max(
            policy.resident_factor_floor.value,
            state.resident_factor * policy.resident_factor_on_429.value,
        )
        state.factor_updated_ms = now

        self._events.append(
            LedgerEvent(
                kind=EventKind.IP_BAN,
                venue=venue,
                bucket=key.bucket,
                ts=self._clock.now_ms(),
                gap_cause=GapCause.IP_BAN,
                ops_event=True,
                notify_f4=True,
                detail={
                    "retry_after_s": wait_s,
                    "retry_after_present": retry_after_s is not None,
                    "halted_lanes": len(self._config.keys_of(venue)),
                    "resident_factor": float(state.resident_factor),
                    "note": "418 bans the whole egress IP, which is shared (§6.1)",
                },
            )
        )

    def _reduce_inflight(self, venue: str, now_ms: int) -> None:
        state = self._venue(venue)
        if state.inflight_limit is None:
            return
        policy = self._config.aimd[venue]
        reduced = int(state.inflight_limit * policy.resident_factor_on_429.value)
        state.inflight_limit = max(1, reduced)
        state.inflight_updated_ms = now_ms

    def _recover(self, venue: str, now_ms: int) -> None:
        """Additive increase — the half of AIMD no document writes down.

        §6 and 04 §3 both specify the decrease (cut to 75 %, freeze an hour) and
        neither specifies the recovery. The step and interval are configuration,
        tagged ``unverified`` in ``venues.yaml`` and named in the PR, so that a
        measurement can replace them without touching this code.
        """
        state = self._venue(venue)
        policy = self._config.aimd[venue]
        interval_ms = policy.resident_recovery_interval_s.as_int * 1000
        if interval_ms <= 0:
            return
        if state.resident_factor < 1:
            steps = (now_ms - state.factor_updated_ms) // interval_ms
            if steps > 0:
                state.resident_factor = min(
                    Fraction(1),
                    state.resident_factor + steps * policy.resident_recovery_step.value,
                )
                state.factor_updated_ms += steps * interval_ms
        ws_spec = self._config.ws.get(venue)
        if (
            state.inflight_limit is not None
            and ws_spec is not None
            and ws_spec.max_inflight is not None
            and state.inflight_limit < ws_spec.max_inflight
        ):
            steps = (now_ms - state.inflight_updated_ms) // interval_ms
            if steps > 0:
                state.inflight_limit = min(ws_spec.max_inflight, state.inflight_limit + steps)
                state.inflight_updated_ms += steps * interval_ms

    # ----------------------------------------------------------------- #
    # Reading the state
    # ----------------------------------------------------------------- #
    def resident_ceiling(self, key: BucketKey | str) -> int:
        """Our ceiling after AIMD, never below the fast lane's floor.

        AIMD slows resident traffic; it does not turn the fast-lane floor off.
        With the documented numbers the question is academic (960 x 75 % = 720,
        far above 120), but a smaller measured budget would make it real.
        """
        bucket_key = self._key(key)
        spec = self._bucket(bucket_key).spec
        now = self._clock.monotonic_ms()
        self._recover(bucket_key.venue, now)
        factor = self._venue(bucket_key.venue).resident_factor
        degraded = int(spec.our_ceiling_per_min * factor)
        return max(spec.fast_lane_floor_per_min, degraded)

    def resident_factor(self, venue: str) -> Fraction:
        self._recover(venue, self._clock.monotonic_ms())
        return self._venue(venue).resident_factor

    def inflight_limit(self, venue: str) -> int | None:
        self._recover(venue, self._clock.monotonic_ms())
        return self._venue(venue).inflight_limit

    def is_halted(self, venue: str) -> bool:
        state = self._venue(venue)
        if state.halted_until_ms is None:
            return False
        return self._clock.monotonic_ms() < state.halted_until_ms

    def is_opportunistic_frozen(self, venue: str) -> bool:
        state = self._venue(venue)
        if state.opportunistic_frozen_until_ms is None:
            return False
        return self._clock.monotonic_ms() < state.opportunistic_frozen_until_ms

    def ws(self, venue: str) -> WsLedger:
        ledger = self._ws.get(venue)
        if ledger is None:
            raise KeyError(f"no WebSocket accounting configured for {venue!r}")
        return ledger

    def snapshot(self, key: BucketKey | str) -> BucketSnapshot:
        bucket_key = self._key(key)
        bucket = self._bucket(bucket_key)
        spec: ResolvedBucket = bucket.spec
        now = self._clock.monotonic_ms()
        state = self._venue(bucket_key.venue)
        self._recover(bucket_key.venue, now)
        return BucketSnapshot(
            key=spec.key,
            kind=spec.kind,
            official_limit_per_min=spec.official_limit_per_min,
            share=spec.share,
            egress_ceiling_per_min=spec.egress_ceiling_per_min,
            reserved_per_min=spec.reserved_per_min,
            our_ceiling_per_min=spec.our_ceiling_per_min,
            profile=spec.profile.name,
            resident_steady_per_min=spec.resident_steady_per_min,
            reserve_per_min=spec.reserve_per_min,
            fast_lane_floor_per_min=spec.fast_lane_floor_per_min,
            opportunistic_hard_cap_per_min=spec.opportunistic_hard_cap_per_min,
            used_fast_per_min=bucket.used(now, Priority.FAST_LANE),
            used_resident_per_min=bucket.used(now, Priority.RESIDENT),
            used_opportunistic_per_min=bucket.used(now, Priority.OPPORTUNISTIC),
            opportunistic_available_per_min=bucket.opportunistic_available(now),
            resident_factor=state.resident_factor,
            resident_ceiling_per_min=self.resident_ceiling(bucket_key),
            halted=self.is_halted(bucket_key.venue),
            halted_until_ms=state.halted_until_ms,
            opportunistic_frozen=self.is_opportunistic_frozen(bucket_key.venue),
            opportunistic_frozen_until_ms=state.opportunistic_frozen_until_ms,
        )

    def snapshots(self) -> tuple[BucketSnapshot, ...]:
        return tuple(self.snapshot(key) for key in sorted(self._buckets))

    # ----------------------------------------------------------------- #
    # Events
    # ----------------------------------------------------------------- #
    def drain_events(self) -> tuple[LedgerEvent, ...]:
        """Take the events produced so far. The caller writes the rows."""
        events = tuple(self._events)
        self._events.clear()
        return events

    def peek_events(self) -> tuple[LedgerEvent, ...]:
        return tuple(self._events)

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #
    def _key(self, key: BucketKey | str) -> BucketKey:
        if isinstance(key, BucketKey):
            return key
        venue, _, bucket = key.partition(":")
        return BucketKey(venue, bucket)

    def _bucket(self, key: BucketKey) -> Bucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            raise KeyError(f"no such bucket: {key}; known: {sorted(str(k) for k in self._buckets)}")
        return bucket

    def _venue(self, venue: str) -> _VenueState:
        if venue not in self._venues:
            raise KeyError(f"unknown venue {venue!r}")
        return self._venues[venue]

    def bucket_keys(self) -> tuple[BucketKey, ...]:
        return tuple(sorted(self._buckets))

    def keys_of(self, venue: str) -> tuple[BucketKey, ...]:
        return tuple(key for key in sorted(self._buckets) if key.venue == venue)
