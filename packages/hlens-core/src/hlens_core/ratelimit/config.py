"""Reading ``config/venues.yaml`` and ``config/egress-consumers.yaml``.

The ceiling this module computes is the whole point of the pair of files::

    egress ceiling = official limit x share          # the WHOLE IP's cap
    our ceiling    = egress ceiling - sum(reserved)  # what is left for us

docs/03-ARCHITECTURE.md §6.1, corrected by §20: the share is a cap on the sum
of every consumer on this public egress IP, **not** an allowance for this
project alone. For Hyperliquid that is 1200 x 90 % = 1080, the legacy collector
reserves 960, and we get 120 — 960 + 120 = 1080 = the ceiling, with 120
weight/min of backoff headroom left for the whole IP.

Three things this loader refuses to do
--------------------------------------
* **Recompute a number the document already decided.** Every ceiling is written
  out in ``venues.yaml`` *and* recomputed here; a disagreement is a load error,
  not a silent preference for one of them.
* **Treat a missing entry as zero.** §6.1 requires "旧采集器不碰 Binance" to be
  *asserted*, not remembered. A consumer that omits a venue, or a venue entry
  that omits a bucket, fails to load; a reservation of 0 must carry the
  sentence that claims it and the name of whoever re-checks it.
* **Accept a reserve below the resident steady load.** §6.1: ``reserve`` is the
  floor for resident traffic and must be >= what resident actually uses,
  otherwise "常驻保底" means nothing — and the review found the original
  ``futures_data`` numbers (reserve 50 < steady 54) made the opportunistic
  formula return -24.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Final

import yaml

__all__ = [
    "AimdPolicy",
    "BucketKey",
    "BucketKind",
    "BudgetProfile",
    "CapacityModel",
    "CoinHeadroom",
    "ConfigError",
    "Constant",
    "EgressConsumers",
    "Flag",
    "LedgerConfig",
    "Reservation",
    "ResolvedBucket",
    "SeatReservation",
    "SourceTag",
    "VenuesConfig",
    "WsSpec",
]

SCHEMA: Final = 1


class ConfigError(ValueError):
    """The configuration is not usable. Never fall back to a default."""


class SourceTag(StrEnum):
    """Where a constant comes from.

    AGENTS.md §2 names the first three. ``derived`` is this task's addition:
    §6.1's budget table is made of numbers that are exact arithmetic on an
    official constant (960 is 2400 x 40 %), and calling those ``official``
    would be false while calling them ``unverified`` would hide that they are
    checkable. Every ``derived`` constant carries the document line it comes
    from. See the PR's "Doc corrections".
    """

    OFFICIAL = "official"
    MEASURED = "measured"
    UNVERIFIED = "unverified"
    DERIVED = "derived"


class BucketKind(StrEnum):
    """The two kinds of ledger, which never share an account.

    §6: ``/futures/data/*`` "不吃权重、自己一个桶" — it charges no weight and
    has its own request-count limit. Mixing the two is how the tightest bucket
    in the system (54 of 80 requests/min) hides behind a weight bucket that is
    only 25 % full.
    """

    WEIGHT = "weight"
    REQUEST = "request"


@dataclass(frozen=True, slots=True, order=True)
class BucketKey:
    """``venue:bucket`` — greppable in a log, which is the point (§5)."""

    venue: str
    bucket: str

    def __str__(self) -> str:
        return f"{self.venue}:{self.bucket}"


@dataclass(frozen=True, slots=True)
class Constant:
    """A number plus where it came from. The tag is not optional."""

    value: Fraction
    source: SourceTag
    doc: str | None = None
    note: str | None = None

    @property
    def as_int(self) -> int:
        if self.value.denominator != 1:
            raise ConfigError(f"{self.value} is not a whole number")
        return int(self.value)


@dataclass(frozen=True, slots=True)
class Flag:
    """A yes/no policy that still has to say where it comes from."""

    value: bool
    source: SourceTag
    doc: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class CapacityModel:
    """How a bucket's usage grows with the number of coins (§6).

    Used only to answer "how many coins fit"; admission never touches it.
    """

    fixed_per_min: Constant
    per_coin_per_min: Constant


@dataclass(frozen=True, slots=True)
class BudgetProfile:
    """One row of §6.1's budget table.

    A bucket can have more than one, selected by the ceiling actually
    available: Hyperliquid has a transitional row (we get 120) and a
    post-reclamation row (we get all 1080), and §6.1's three-step reclamation
    procedure says explicitly that switching between them changes no code.
    """

    name: str
    min_our_ceiling_per_min: int
    reserve_per_min: Constant
    fast_lane_floor_per_min: Constant
    opportunistic_hard_cap_per_min: Constant


@dataclass(frozen=True, slots=True)
class AimdPolicy:
    """What a 429 does, and how the ledger comes back from it."""

    resident_factor_on_429: Constant
    opportunistic_freeze_s: Constant
    resident_recovery_step: Constant
    resident_recovery_interval_s: Constant
    resident_factor_floor: Constant
    notify_f4_on_429: Flag


@dataclass(frozen=True, slots=True)
class WsSpec:
    """WebSocket limits. Seats are indivisible and zero-sum across the IP."""

    venue: str
    max_new_connections_per_window: int | None = None
    window_s: int | None = None
    max_connection_age_s: int | None = None
    max_streams_per_connection: int | None = None
    max_connections: int | None = None
    max_subscriptions: int | None = None
    max_distinct_users: int | None = None
    max_inflight: int | None = None
    max_inflight_hard_cap: int | None = None


@dataclass(frozen=True, slots=True)
class RawBucket:
    """A bucket as ``venues.yaml`` declares it, before reservations apply."""

    key: BucketKey
    kind: BucketKind
    official_limit_per_min: Constant
    share: Constant
    egress_ceiling_per_min: Constant
    resident_steady_per_min: Constant
    profiles: tuple[BudgetProfile, ...]
    capacity_model: CapacityModel | None


@dataclass(frozen=True, slots=True)
class VenueSpec:
    name: str
    aimd: AimdPolicy
    buckets: Mapping[str, RawBucket]
    ws: WsSpec


@dataclass(frozen=True, slots=True)
class VenuesConfig:
    venues: Mapping[str, VenueSpec]

    @classmethod
    def load(cls, path: Path | str) -> VenuesConfig:
        return _parse_venues(_read_yaml(Path(path)), source=str(path))

    def bucket_keys(self) -> tuple[BucketKey, ...]:
        return tuple(
            bucket.key for venue in self.venues.values() for bucket in venue.buckets.values()
        )


@dataclass(frozen=True, slots=True)
class Reservation:
    """One consumer's claim on one bucket."""

    consumer: str
    key: BucketKey
    reserved_per_min: int
    source: SourceTag
    assertion: str | None = None
    checked_by: str | None = None
    doc: str | None = None
    note: str | None = None

    @property
    def is_assertion_of_zero(self) -> bool:
        return self.reserved_per_min == 0


@dataclass(frozen=True, slots=True)
class SeatReservation:
    """A WebSocket seat claim. ``reserved is None`` means *unknown*, not zero.

    §6.1 row 4: how many connection and user seats the legacy collector holds
    was never measured — "上机第一件事就是数". An unknown is carried as an
    unknown so preflight can say so, instead of being rounded to zero, which
    is the one answer that is certainly wrong.
    """

    consumer: str
    venue: str
    seat: str
    reserved: int | None
    source: SourceTag
    doc: str | None = None


@dataclass(frozen=True, slots=True)
class EgressConsumers:
    """``config/egress-consumers.yaml`` — hand-maintained, machine-checked."""

    consumers: tuple[str, ...]
    reservations: tuple[Reservation, ...]
    seats: tuple[SeatReservation, ...]

    @classmethod
    def load(cls, path: Path | str, *, venues: VenuesConfig) -> EgressConsumers:
        return _parse_consumers(_read_yaml(Path(path)), venues=venues, source=str(path))

    def for_bucket(self, key: BucketKey) -> tuple[Reservation, ...]:
        return tuple(r for r in self.reservations if r.key == key)

    def reserved_per_min(self, key: BucketKey) -> int:
        return sum(r.reserved_per_min for r in self.for_bucket(key))

    def unknown_seats(self, venue: str) -> tuple[SeatReservation, ...]:
        return tuple(s for s in self.seats if s.venue == venue and s.reserved is None)


@dataclass(frozen=True, slots=True)
class ResolvedBucket:
    """A bucket with the deduction applied — what the ledger actually enforces."""

    key: BucketKey
    kind: BucketKind
    official_limit_per_min: int
    share: Fraction
    egress_ceiling_per_min: int
    reserved_per_min: int
    our_ceiling_per_min: int
    resident_steady_per_min: int
    profile: BudgetProfile
    reservations: tuple[Reservation, ...]
    capacity_model: CapacityModel | None

    @property
    def reserve_per_min(self) -> int:
        return self.profile.reserve_per_min.as_int

    @property
    def fast_lane_floor_per_min(self) -> int:
        return self.profile.fast_lane_floor_per_min.as_int

    @property
    def opportunistic_hard_cap_per_min(self) -> int:
        return self.profile.opportunistic_hard_cap_per_min.as_int

    def opportunistic_formula_per_min(self, resident_used_per_min: int) -> int:
        """§6.1's corrected formula, before the hard cap::

            available = ceiling - max(reserve, resident actual usage)

        The formula the review replaced was ``budget - reserve - resident
        actual``, which subtracts resident twice. Writing that one here would
        make the opportunistic lane negative exactly when resident is healthy —
        which is how ``futures_data`` ended up with a backfill quota of -24.
        """
        floor_or_actual = max(self.reserve_per_min, resident_used_per_min)
        return max(0, self.our_ceiling_per_min - floor_or_actual)

    def opportunistic_available(self, resident_used_per_min: int) -> int:
        """The formula, then the hard cap. This is what admission enforces.

        The two are kept separate because §6.1's table prints both, and because
        on ``futures_data`` they are equal on purpose ("硬顶 20 现在等于算式结
        果"), while on the Binance weight bucket the cap (200) is far below the
        formula (660) to leave backoff headroom.
        """
        return min(
            self.opportunistic_formula_per_min(resident_used_per_min),
            self.opportunistic_hard_cap_per_min,
        )


@dataclass(frozen=True, slots=True)
class CoinHeadroom:
    """§6's "how many more coins fit" answer for one bucket."""

    key: BucketKey
    wall_coins: int
    safe_coins: int
    retry_margin: Fraction


@dataclass(frozen=True, slots=True)
class LedgerConfig:
    """Everything the ledger needs, with the deduction already applied."""

    buckets: Mapping[BucketKey, ResolvedBucket]
    aimd: Mapping[str, AimdPolicy]
    ws: Mapping[str, WsSpec]
    consumers: EgressConsumers

    @classmethod
    def load(cls, venues_path: Path | str, consumers_path: Path | str) -> LedgerConfig:
        venues = VenuesConfig.load(venues_path)
        consumers = EgressConsumers.load(consumers_path, venues=venues)
        return cls.resolve(venues, consumers)

    @classmethod
    def resolve(cls, venues: VenuesConfig, consumers: EgressConsumers) -> LedgerConfig:
        buckets: dict[BucketKey, ResolvedBucket] = {}
        for venue in venues.venues.values():
            for raw in venue.buckets.values():
                buckets[raw.key] = _resolve_bucket(raw, consumers)
        return cls(
            buckets=buckets,
            aimd={name: venue.aimd for name, venue in venues.venues.items()},
            ws={name: venue.ws for name, venue in venues.venues.items()},
            consumers=consumers,
        )

    def bucket(self, key: BucketKey | str) -> ResolvedBucket:
        resolved = self.buckets.get(_as_key(key))
        if resolved is None:
            raise ConfigError(f"no such bucket: {key}")
        return resolved

    def venues(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(key.venue for key in self.buckets))

    def keys_of(self, venue: str) -> tuple[BucketKey, ...]:
        return tuple(key for key in self.buckets if key.venue == venue)

    def coin_headroom(self, key: BucketKey | str) -> CoinHeadroom:
        """The two numbers of §6: where the wall is, and where backoff still works.

        ``wall`` is ``(ceiling - fixed) / per coin`` — the point at which the
        bucket is exactly full. ``safe`` divides that by the bucket's own retry
        margin (``reserve / resident steady``), because a bucket that is exactly
        full has no room to retry anything. §6: 266 is the wall, 240 is the
        point that can still back off.
        """
        bucket = self.bucket(key)
        if bucket.capacity_model is None:
            raise ConfigError(f"{bucket.key} declares no capacity_model")
        per_coin = bucket.capacity_model.per_coin_per_min.value
        if per_coin <= 0:
            raise ConfigError(f"{bucket.key} has a non-positive per-coin cost")
        spare = Fraction(bucket.our_ceiling_per_min) - bucket.capacity_model.fixed_per_min.value
        margin = Fraction(bucket.reserve_per_min, bucket.resident_steady_per_min)
        return CoinHeadroom(
            key=bucket.key,
            wall_coins=math.floor(spare / per_coin),
            safe_coins=math.floor(spare / per_coin / margin),
            retry_margin=margin,
        )

    def coin_headroom_overall(self) -> CoinHeadroom:
        """The binding one. §6: "两路各算一次，取小者"."""
        candidates = [
            self.coin_headroom(key)
            for key, bucket in self.buckets.items()
            if bucket.capacity_model is not None
        ]
        if not candidates:
            raise ConfigError("no bucket declares a capacity_model")
        return min(candidates, key=lambda headroom: (headroom.safe_coins, headroom.wall_coins))


# --------------------------------------------------------------------------- #
# Parsing. Everything below turns untyped YAML into the types above, and
# refuses anything it cannot fully account for.
# --------------------------------------------------------------------------- #
def _read_yaml(path: Path) -> object:
    if not path.is_file():
        raise ConfigError(
            f"{path} is missing. §6.1: preflight refuses to start without it — a missing "
            "reservation table is indistinguishable from a reservation of zero, and the "
            "wrong way to be wrong is 「我们以为有 960 权重其实没有」"
        )
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _as_key(key: BucketKey | str) -> BucketKey:
    if isinstance(key, BucketKey):
        return key
    venue, _, bucket = key.partition(":")
    if not venue or not bucket:
        raise ConfigError(f"bucket key must be 'venue:bucket', got {key!r}")
    return BucketKey(venue, bucket)


def _mapping(node: object, where: str) -> Mapping[str, object]:
    if not isinstance(node, Mapping):
        raise ConfigError(f"{where}: expected a mapping, got {type(node).__name__}")
    out: dict[str, object] = {}
    for name, value in node.items():
        if not isinstance(name, str):
            raise ConfigError(f"{where}: key {name!r} is not a string")
        out[name] = value
    return out


def _sequence(node: object, where: str) -> Sequence[object]:
    if not isinstance(node, Sequence) or isinstance(node, str | bytes):
        raise ConfigError(f"{where}: expected a list, got {type(node).__name__}")
    return node


def _require(node: Mapping[str, object], name: str, where: str) -> object:
    if name not in node:
        raise ConfigError(f"{where}: missing required key {name!r}")
    return node[name]


def _text(node: object, where: str) -> str:
    if not isinstance(node, str) or not node.strip():
        raise ConfigError(f"{where}: expected a non-empty string")
    return node.strip()


def _number(node: object, where: str) -> Fraction:
    if isinstance(node, bool) or not isinstance(node, int | float):
        raise ConfigError(f"{where}: expected a number, got {type(node).__name__}")
    return Fraction(str(node))


def _constant(node: object, where: str) -> Constant:
    body = _mapping(node, where)
    source = _text(_require(body, "source", where), f"{where}.source")
    if source not in set(SourceTag):
        raise ConfigError(
            f"{where}.source: {source!r} is not one of "
            f"{sorted(tag.value for tag in SourceTag)}"
        )
    doc = body.get("doc")
    note = body.get("note")
    return Constant(
        value=_number(_require(body, "value", where), f"{where}.value"),
        source=SourceTag(source),
        doc=_text(doc, f"{where}.doc") if doc is not None else None,
        note=_text(note, f"{where}.note") if note is not None else None,
    )


def _flag(node: object, where: str) -> Flag:
    body = _mapping(node, where)
    value = _require(body, "value", where)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}.value: expected true or false, got {value!r}")
    source = _text(_require(body, "source", where), f"{where}.source")
    if source not in set(SourceTag):
        raise ConfigError(f"{where}.source: {source!r} is not a known source tag")
    doc = body.get("doc")
    note = body.get("note")
    return Flag(
        value=value,
        source=SourceTag(source),
        doc=_text(doc, f"{where}.doc") if doc is not None else None,
        note=_text(note, f"{where}.note") if note is not None else None,
    )


def _int_constant(node: object, where: str) -> Constant:
    constant = _constant(node, where)
    if constant.value.denominator != 1:
        raise ConfigError(f"{where}.value: expected a whole number, got {constant.value}")
    return constant


def _optional_int_constant(body: Mapping[str, object], name: str, where: str) -> int | None:
    if name not in body:
        return None
    return _int_constant(body[name], f"{where}.{name}").as_int


def _parse_aimd(node: object, where: str) -> AimdPolicy:
    body = _mapping(node, where)
    return AimdPolicy(
        resident_factor_on_429=_constant(
            _require(body, "resident_factor_on_429", where), f"{where}.resident_factor_on_429"
        ),
        opportunistic_freeze_s=_int_constant(
            _require(body, "opportunistic_freeze_s", where), f"{where}.opportunistic_freeze_s"
        ),
        resident_recovery_step=_constant(
            _require(body, "resident_recovery_step", where), f"{where}.resident_recovery_step"
        ),
        resident_recovery_interval_s=_int_constant(
            _require(body, "resident_recovery_interval_s", where),
            f"{where}.resident_recovery_interval_s",
        ),
        resident_factor_floor=_constant(
            _require(body, "resident_factor_floor", where), f"{where}.resident_factor_floor"
        ),
        notify_f4_on_429=_flag(
            _require(body, "notify_f4_on_429", where), f"{where}.notify_f4_on_429"
        ),
    )


def _parse_profiles(body: Mapping[str, object], where: str) -> tuple[BudgetProfile, ...]:
    if "profiles" in body:
        raw_profiles = _sequence(body["profiles"], f"{where}.profiles")
        profiles = [
            _parse_profile(item, f"{where}.profiles[{index}]")
            for index, item in enumerate(raw_profiles)
        ]
    else:
        profiles = [_parse_profile(body, where, name="default", threshold=0)]
    if not profiles:
        raise ConfigError(f"{where}: at least one budget profile is required")
    if not any(profile.min_our_ceiling_per_min == 0 for profile in profiles):
        raise ConfigError(
            f"{where}: one profile must have min_our_ceiling_per_min: 0, otherwise a "
            "reservation larger than expected leaves the bucket with no applicable profile"
        )
    return tuple(sorted(profiles, key=lambda profile: profile.min_our_ceiling_per_min))


def _parse_profile(
    node: object, where: str, *, name: str | None = None, threshold: int | None = None
) -> BudgetProfile:
    body = _mapping(node, where)
    profile_name = name if name is not None else _text(_require(body, "name", where), where)
    if threshold is None:
        threshold = _int_value(
            _require(body, "min_our_ceiling_per_min", where), f"{where}.min_our_ceiling_per_min"
        )
    profile = BudgetProfile(
        name=profile_name,
        min_our_ceiling_per_min=threshold,
        reserve_per_min=_int_constant(
            _require(body, "reserve_per_min", where), f"{where}.reserve_per_min"
        ),
        fast_lane_floor_per_min=_int_constant(
            _require(body, "fast_lane_floor_per_min", where), f"{where}.fast_lane_floor_per_min"
        ),
        opportunistic_hard_cap_per_min=_int_constant(
            _require(body, "opportunistic_hard_cap_per_min", where),
            f"{where}.opportunistic_hard_cap_per_min",
        ),
    )
    if profile.fast_lane_floor_per_min.as_int > profile.reserve_per_min.as_int:
        raise ConfigError(
            f"{where}: the fast-lane floor ({profile.fast_lane_floor_per_min.as_int}) is larger "
            f"than the resident reserve ({profile.reserve_per_min.as_int}); the fast lane is "
            "part of resident, so its floor cannot exceed the floor it lives inside"
        )
    return profile


def _int_value(node: object, where: str) -> int:
    number = _number(node, where)
    if number.denominator != 1:
        raise ConfigError(f"{where}: expected a whole number, got {number}")
    return int(number)


def _parse_capacity(node: object, where: str) -> CapacityModel:
    body = _mapping(node, where)
    return CapacityModel(
        fixed_per_min=_constant(_require(body, "fixed_per_min", where), f"{where}.fixed_per_min"),
        per_coin_per_min=_constant(
            _require(body, "per_coin_per_min", where), f"{where}.per_coin_per_min"
        ),
    )


def _parse_bucket(venue: str, name: str, node: object, where: str) -> RawBucket:
    body = _mapping(node, where)
    kind = _text(_require(body, "kind", where), f"{where}.kind")
    if kind not in set(BucketKind):
        raise ConfigError(
            f"{where}.kind: {kind!r} is not one of {sorted(k.value for k in BucketKind)}"
        )
    official = _int_constant(
        _require(body, "official_limit_per_min", where), f"{where}.official_limit_per_min"
    )
    share = _constant(_require(body, "share", where), f"{where}.share")
    if not 0 < share.value <= 1:
        raise ConfigError(f"{where}.share: expected a fraction in (0, 1], got {share.value}")
    declared_ceiling = _int_constant(
        _require(body, "egress_ceiling_per_min", where), f"{where}.egress_ceiling_per_min"
    )
    computed_ceiling = math.floor(official.value * share.value)
    if declared_ceiling.as_int != computed_ceiling:
        raise ConfigError(
            f"{where}: egress_ceiling_per_min says {declared_ceiling.as_int} but "
            f"{official.as_int} x {share.value} = {computed_ceiling}. One of the two is a "
            "typo; this loader will not guess which"
        )
    resident_steady = _int_constant(
        _require(body, "resident_steady_per_min", where), f"{where}.resident_steady_per_min"
    )
    capacity = (
        _parse_capacity(body["capacity_model"], f"{where}.capacity_model")
        if "capacity_model" in body
        else None
    )
    profiles = _parse_profiles(body, where)
    for profile in profiles:
        if profile.reserve_per_min.as_int < resident_steady.as_int:
            raise ConfigError(
                f"{where}: profile {profile.name!r} reserves "
                f"{profile.reserve_per_min.as_int}/min for resident traffic but resident's "
                f"steady load is {resident_steady.as_int}/min. §6.1: reserve is a FLOOR and "
                "must be >= the steady load, or 「常驻保底」 means nothing and the "
                "opportunistic formula goes negative"
            )
    return RawBucket(
        key=BucketKey(venue, name),
        kind=BucketKind(kind),
        official_limit_per_min=official,
        share=share,
        egress_ceiling_per_min=declared_ceiling,
        resident_steady_per_min=resident_steady,
        profiles=profiles,
        capacity_model=capacity,
    )


def _parse_ws(venue: str, node: object, where: str) -> WsSpec:
    body = _mapping(node, where)
    spec = WsSpec(
        venue=venue,
        max_new_connections_per_window=_optional_int_constant(
            body, "max_new_connections_per_window", where
        ),
        window_s=_optional_int_constant(body, "window_s", where),
        max_connection_age_s=_optional_int_constant(body, "max_connection_age_s", where),
        max_streams_per_connection=_optional_int_constant(
            body, "max_streams_per_connection", where
        ),
        max_connections=_optional_int_constant(body, "max_connections", where),
        max_subscriptions=_optional_int_constant(body, "max_subscriptions", where),
        max_distinct_users=_optional_int_constant(body, "max_distinct_users", where),
        max_inflight=_optional_int_constant(body, "max_inflight", where),
        max_inflight_hard_cap=_optional_int_constant(body, "max_inflight_hard_cap", where),
    )
    if (spec.max_new_connections_per_window is None) != (spec.window_s is None):
        raise ConfigError(
            f"{where}: a connection-rate limit needs both a count and the window it is "
            "counted over"
        )
    return spec


def _parse_venues(document: object, *, source: str) -> VenuesConfig:
    root = _mapping(document, source)
    _check_schema(root, source)
    venues_node = _mapping(_require(root, "venues", source), f"{source}.venues")
    venues: dict[str, VenueSpec] = {}
    for venue_name, venue_node in venues_node.items():
        where = f"{source}.venues.{venue_name}"
        body = _mapping(venue_node, where)
        buckets_node = _mapping(_require(body, "buckets", where), f"{where}.buckets")
        buckets = {
            bucket_name: _parse_bucket(
                venue_name, bucket_name, bucket_node, f"{where}.buckets.{bucket_name}"
            )
            for bucket_name, bucket_node in buckets_node.items()
        }
        if not buckets:
            raise ConfigError(f"{where}: a venue with no bucket cannot be accounted for")
        venues[venue_name] = VenueSpec(
            name=venue_name,
            aimd=_parse_aimd(_require(body, "aimd", where), f"{where}.aimd"),
            buckets=buckets,
            ws=_parse_ws(venue_name, body.get("ws", {}), f"{where}.ws"),
        )
    if not venues:
        raise ConfigError(f"{source}: no venues declared")
    return VenuesConfig(venues=venues)


def _check_schema(root: Mapping[str, object], source: str) -> None:
    schema = _require(root, "schema", source)
    if schema != SCHEMA:
        raise ConfigError(f"{source}: schema {schema!r}, expected {SCHEMA}")


def _parse_consumers(
    document: object, *, venues: VenuesConfig, source: str
) -> EgressConsumers:
    root = _mapping(document, source)
    _check_schema(root, source)
    raw_consumers = _sequence(_require(root, "consumers", source), f"{source}.consumers")
    names: list[str] = []
    reservations: list[Reservation] = []
    seats: list[SeatReservation] = []

    for index, consumer_node in enumerate(raw_consumers):
        where = f"{source}.consumers[{index}]"
        body = _mapping(consumer_node, where)
        name = _text(_require(body, "name", where), f"{where}.name")
        if name in names:
            raise ConfigError(f"{where}: duplicate consumer name {name!r}")
        names.append(name)
        where = f"{source}.consumers[{name}]"
        venues_node = _mapping(_require(body, "venues", where), f"{where}.venues")
        _reject_unknown(venues_node.keys(), set(venues.venues), f"{where}.venues", "venue")
        missing_venues = set(venues.venues) - set(venues_node)
        if missing_venues:
            raise ConfigError(
                f"{where}.venues: {sorted(missing_venues)} not listed. An omission is not an "
                "assertion (§6.1): say either what this consumer reserves there, or that it "
                "reserves 0 and why"
            )
        for venue_name, venue_node in venues_node.items():
            venue_where = f"{where}.venues.{venue_name}"
            venue_body = _mapping(venue_node, venue_where)
            declared_buckets = {
                key for key in venue_body if key not in {"ws_seats", "role", "note"}
            }
            expected = set(venues.venues[venue_name].buckets)
            _reject_unknown(declared_buckets, expected, venue_where, "bucket")
            missing = expected - declared_buckets
            if missing:
                raise ConfigError(
                    f"{venue_where}: bucket(s) {sorted(missing)} not listed. An omission is "
                    "not an assertion (§6.1)"
                )
            for bucket_name in sorted(declared_buckets):
                reservations.append(
                    _parse_reservation(
                        consumer=name,
                        key=BucketKey(venue_name, bucket_name),
                        node=venue_body[bucket_name],
                        where=f"{venue_where}.{bucket_name}",
                    )
                )
            if "ws_seats" in venue_body:
                seats.extend(
                    _parse_seats(
                        consumer=name,
                        venue=venue_name,
                        node=venue_body["ws_seats"],
                        where=f"{venue_where}.ws_seats",
                    )
                )
    if not names:
        raise ConfigError(f"{source}: no consumers declared")
    return EgressConsumers(
        consumers=tuple(names), reservations=tuple(reservations), seats=tuple(seats)
    )


def _reject_unknown(
    declared: Iterable[str], expected: set[str], where: str, noun: str
) -> None:
    unknown = set(declared) - expected
    if unknown:
        raise ConfigError(
            f"{where}: unknown {noun}(s) {sorted(unknown)}; known: {sorted(expected)}"
        )


def _parse_reservation(*, consumer: str, key: BucketKey, node: object, where: str) -> Reservation:
    body = _mapping(node, where)
    reserved = _int_value(_require(body, "reserved_per_min", where), f"{where}.reserved_per_min")
    if reserved < 0:
        raise ConfigError(f"{where}: a reservation cannot be negative")
    source_tag = _text(_require(body, "source", where), f"{where}.source")
    if source_tag not in set(SourceTag):
        raise ConfigError(
            f"{where}.source: {source_tag!r} is not one of "
            f"{sorted(tag.value for tag in SourceTag)}"
        )
    assertion = body.get("assertion")
    checked_by = body.get("checked_by")
    if reserved == 0 and (assertion is None or checked_by is None):
        raise ConfigError(
            f"{where}: a reservation of 0 must carry `assertion:` (what claim about the world "
            "makes it zero) and `checked_by:` (who re-checks it). §6.1: 「旧采集器不碰 "
            "Binance」必须被断言，不能靠记忆 — a bare 0 is exactly the memory it forbids"
        )
    doc = body.get("doc")
    note = body.get("note")
    return Reservation(
        consumer=consumer,
        key=key,
        reserved_per_min=reserved,
        source=SourceTag(source_tag),
        assertion=_text(assertion, f"{where}.assertion") if assertion is not None else None,
        checked_by=_text(checked_by, f"{where}.checked_by") if checked_by is not None else None,
        doc=_text(doc, f"{where}.doc") if doc is not None else None,
        note=_text(note, f"{where}.note") if note is not None else None,
    )


def _parse_seats(
    *, consumer: str, venue: str, node: object, where: str
) -> list[SeatReservation]:
    body = _mapping(node, where)
    seats: list[SeatReservation] = []
    for seat_name, seat_node in body.items():
        seat_where = f"{where}.{seat_name}"
        seat_body = _mapping(seat_node, seat_where)
        raw = _require(seat_body, "reserved", seat_where)
        source_tag = _text(_require(seat_body, "source", seat_where), f"{seat_where}.source")
        if source_tag not in set(SourceTag):
            raise ConfigError(f"{seat_where}.source: {source_tag!r} is not a known source tag")
        doc = seat_body.get("doc")
        seats.append(
            SeatReservation(
                consumer=consumer,
                venue=venue,
                seat=seat_name,
                reserved=None if raw is None else _int_value(raw, f"{seat_where}.reserved"),
                source=SourceTag(source_tag),
                doc=_text(doc, f"{seat_where}.doc") if doc is not None else None,
            )
        )
    return seats


def _resolve_bucket(raw: RawBucket, consumers: EgressConsumers) -> ResolvedBucket:
    reservations = consumers.for_bucket(raw.key)
    reserved = sum(reservation.reserved_per_min for reservation in reservations)
    egress_ceiling = raw.egress_ceiling_per_min.as_int
    our_ceiling = egress_ceiling - reserved
    if our_ceiling < 0:
        raise ConfigError(
            f"{raw.key}: reservations total {reserved}/min but the egress ceiling is "
            f"{egress_ceiling}/min. The other consumers alone are over the line"
        )
    # §6.1's ceiling rule, restated as an invariant so it cannot drift: what is
    # reserved plus what we may use must land inside the EGRESS total, never
    # inside the official limit.
    if reserved + our_ceiling != egress_ceiling:
        raise ConfigError(f"{raw.key}: reserved + ours != egress ceiling")
    applicable = [
        profile for profile in raw.profiles if profile.min_our_ceiling_per_min <= our_ceiling
    ]
    if not applicable:
        raise ConfigError(f"{raw.key}: no budget profile applies to a ceiling of {our_ceiling}")
    profile = applicable[-1]
    if profile.reserve_per_min.as_int > our_ceiling:
        raise ConfigError(
            f"{raw.key}: profile {profile.name!r} reserves {profile.reserve_per_min.as_int}/min "
            f"for resident traffic but only {our_ceiling}/min is left after other consumers "
            "are deducted. Measure the other consumers or cut our own load; do not raise the "
            "share"
        )
    return ResolvedBucket(
        key=raw.key,
        kind=raw.kind,
        official_limit_per_min=raw.official_limit_per_min.as_int,
        share=raw.share.value,
        egress_ceiling_per_min=egress_ceiling,
        reserved_per_min=reserved,
        our_ceiling_per_min=our_ceiling,
        resident_steady_per_min=raw.resident_steady_per_min.as_int,
        profile=profile,
        reservations=reservations,
        capacity_model=raw.capacity_model,
    )
