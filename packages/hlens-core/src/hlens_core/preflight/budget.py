"""The shared-egress deduction table — printed here, computed in ``ratelimit``.

Two design problems live in this file. Both are stated in ``03`` §6.1, and the
answers are the reason the module looks the way it does.

1. Using the ledger's arithmetic without importing the ledger
-------------------------------------------------------------
``03`` §6.1 gives the deduction one owner: ``config/venues.yaml`` plus
``config/egress-consumers.yaml``, resolved by ``hlens_core.ratelimit.config``,
which already computes ``官方上限 × 使用比例 − Σ reserved``, already refuses a
reservation table that overruns the ceiling, and already refuses a bare ``0``
with no assertion behind it. Preflight is that table's **printer**, never a
second implementation — two copies of one piece of arithmetic disagree
eventually, and the disagreement surfaces as a rate-limit ban.

Seam ③ forbids ``preflight`` from importing ``ratelimit``
(``tests/test_module_boundaries.py``: ``"preflight": frozenset({"contracts"})``).
The way through is the one ``M1-A4`` already established for the adapters:
**dependency inversion, structurally**. :class:`BudgetView`, :class:`BucketView`
and :class:`ReservationView` are :class:`typing.Protocol` classes written to
match what ``LedgerConfig`` and ``ResolvedBucket`` *already* look like, and
:data:`BudgetLoader` is written to match ``LedgerConfig.load`` exactly. Nothing
in ``ratelimit`` changes, nothing here imports it, and the one line that knows
both names lives in the composition root outside ``packages/``
(``scripts/preflight.py``). ``tests/test_preflight_seam.py`` proves the match
by assignment under mypy, exactly as ``tests/test_adapter_admission.py`` does
for the adapters' ``SpendAuthority``.

Every member below is a **read-only property**, so a frozen dataclass on the
far side satisfies it; and ``bucket()`` takes ``str`` while the ledger accepts
``BucketKey | str``, which is the contravariance that lets the bucket travel
as the greppable ``"venue:bucket"`` string the ledger already parses.

2. Printing a ledger that balances over an egress that does not
---------------------------------------------------------------
``03`` §6.1's Hyperliquid row used to end in a ✓. ``M1-B`` withdrew it, and the
reason is the whole design problem of this table: ``953 + 127 = 1080`` is an
**identity the loader guarantees** — it refuses to load anything else — so
printing it with a tick mark proves nothing about the egress and reads as if it
did. In the same 29-minute window that produced the 953, the neighbour alone
peaked at ``1147``, over our entire ``1080`` ceiling, and it allows itself
``1200`` while not knowing this project exists.

So each bucket prints **three blocks that cannot be confused for each other**:

* ``账面`` — the deduction, straight from the ledger's own resolution.
* ``现实`` — the observed peak and the self-configured cap of every other
  consumer on this egress, from the annotations described below.
* ``判定`` — one sentence, and it is allowed to say "the books balance and the
  egress does not". A bucket whose neighbour alone crosses the ceiling can
  never print green here, however neatly its arithmetic adds up.

Where the reality numbers come from
-----------------------------------
``config/egress-consumers.yaml`` is where ``03`` §6.1 puts everything known
about the other consumers, so the observations go in the same entry as the
reservation they qualify, as three optional keys — ``observed_max_per_min``,
``self_cap_per_min`` and ``qualifiers``. The ledger's loader ignores keys it
does not know (it validates the ones it needs and rejects unknown *venues* and
*buckets*, not unknown annotation keys), so this adds no second schema to the
one file and no change to ``ratelimit``; ``tests/test_preflight_budget.py``
pins that by loading the real file through the real loader.

A reservation with no ``observed_max_per_min`` is **unknown**, not fine: a
number nobody has watched peak is exactly the case §6.1 warns about, where "错
的方式是「我们以为有 960 权重其实没有」".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from .provenance import Provenance, Qualifier, Tag
from .verdict import Check, Status, worst

__all__ = [
    "BucketKeyView",
    "BucketView",
    "BudgetLoader",
    "BudgetView",
    "ConsumerObservation",
    "ProfileView",
    "ReservationView",
    "check_budget",
    "read_observations",
]


# --------------------------------------------------------------------------- #
# Seam ③: what preflight needs from the ledger's config, written as protocols
# the ledger already satisfies without knowing this file exists.
# --------------------------------------------------------------------------- #
@runtime_checkable
class BucketKeyView(Protocol):
    """``venue:bucket``. ``__str__`` is part of the contract: it is the form
    that appears in a log line and in ``status.json``."""

    @property
    def venue(self) -> str: ...

    @property
    def bucket(self) -> str: ...

    def __str__(self) -> str: ...


@runtime_checkable
class ReservationView(Protocol):
    """One other consumer's claim on one bucket, as the loader resolved it."""

    @property
    def consumer(self) -> str: ...

    @property
    def reserved_per_min(self) -> int: ...

    @property
    def source(self) -> str: ...

    @property
    def assertion(self) -> str | None: ...

    @property
    def checked_by(self) -> str | None: ...

    @property
    def doc(self) -> str | None: ...


@runtime_checkable
class ProfileView(Protocol):
    """The budget profile the resolved ceiling selected (§6.1's transitional
    and post-reclamation rows)."""

    @property
    def name(self) -> str: ...


@runtime_checkable
class BucketView(Protocol):
    """One resolved bucket: the deduction, already done."""

    @property
    def kind(self) -> str: ...

    @property
    def official_limit_per_min(self) -> int: ...

    @property
    def share(self) -> Fraction: ...

    @property
    def egress_ceiling_per_min(self) -> int: ...

    @property
    def reserved_per_min(self) -> int: ...

    @property
    def our_ceiling_per_min(self) -> int: ...

    @property
    def resident_steady_per_min(self) -> int: ...

    @property
    def reserve_per_min(self) -> int: ...

    @property
    def fast_lane_floor_per_min(self) -> int: ...

    @property
    def opportunistic_hard_cap_per_min(self) -> int: ...

    @property
    def profile(self) -> ProfileView: ...

    @property
    def reservations(self) -> Sequence[ReservationView]: ...

    def opportunistic_formula_per_min(self, resident_used_per_min: int) -> int: ...

    def opportunistic_available(self, resident_used_per_min: int) -> int: ...


@runtime_checkable
class BudgetView(Protocol):
    """Every bucket, keyed the way the ledger keys them."""

    def venues(self) -> tuple[str, ...]: ...

    def keys_of(self, venue: str) -> Sequence[BucketKeyView]: ...

    def bucket(self, key: str) -> BucketView: ...


#: ``(venues.yaml, egress-consumers.yaml) -> the resolved budget``.
#: ``hlens_core.ratelimit.LedgerConfig.load`` satisfies this as written: its
#: parameters accept ``Path | str`` (wider than ``Path``, which is what
#: contravariance requires) and it returns a ``LedgerConfig``, which satisfies
#: :class:`BudgetView`. It raises ``ConfigError``, a ``ValueError`` subclass,
#: which this module catches as ``ValueError`` — so even the refusal path needs
#: no import.
BudgetLoader = Callable[[Path, Path], BudgetView]


# --------------------------------------------------------------------------- #
# The observations that make the second block of the table possible.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ConsumerObservation:
    """What is known about one consumer's *actual* behaviour on one bucket.

    ``reserved_per_min`` is what the ledger deducts. These two are what it
    could not deduct: the highest minute anyone watched, and the ceiling the
    consumer grants itself. They are annotations on the same YAML entry and
    they are optional, because for most consumers nobody has measured either.
    """

    consumer: str
    bucket: str
    observed_max_per_min: int | None
    self_cap_per_min: int | None
    qualifiers: frozenset[Qualifier]

    @property
    def worst_known_per_min(self) -> int | None:
        """The largest number anyone can point at for this consumer.

        A self-configured cap counts even though it has never been reached:
        §6.1's whole argument about the Hyperliquid collector is that ``1200``
        is what it is *allowed* to do while not knowing we exist, and planning
        against its p95 is planning against its good behaviour.
        """
        candidates = [
            value
            for value in (self.observed_max_per_min, self.self_cap_per_min)
            if value is not None
        ]
        return max(candidates) if candidates else None


def read_observations(path: Path) -> dict[tuple[str, str], ConsumerObservation]:
    """Read preflight's optional annotations out of ``egress-consumers.yaml``.

    Deliberately a second, tolerant read of a file the ledger has already
    validated: by the time this runs, the loader has accepted the file, so
    anything malformed *here* is malformed in an annotation, and an annotation
    that cannot be read is reported as an absent observation rather than as a
    crash — the deduction itself never depends on these keys.
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], ConsumerObservation] = {}
    if not isinstance(document, Mapping):
        return out
    consumers = document.get("consumers")
    if not isinstance(consumers, Sequence):
        return out
    for consumer_node in consumers:
        if not isinstance(consumer_node, Mapping):
            continue
        name = consumer_node.get("name")
        venues = consumer_node.get("venues")
        if not isinstance(name, str) or not isinstance(venues, Mapping):
            continue
        for venue_name, venue_node in venues.items():
            if not isinstance(venue_node, Mapping) or not isinstance(venue_name, str):
                continue
            for bucket_name, bucket_node in venue_node.items():
                if not isinstance(bucket_node, Mapping) or not isinstance(bucket_name, str):
                    continue
                key = f"{venue_name}:{bucket_name}"
                out[(name, key)] = ConsumerObservation(
                    consumer=name,
                    bucket=key,
                    observed_max_per_min=_optional_int(bucket_node.get("observed_max_per_min")),
                    self_cap_per_min=_optional_int(bucket_node.get("self_cap_per_min")),
                    qualifiers=_qualifiers(bucket_node.get("qualifiers")),
                )
    return out


def _optional_int(node: Any) -> int | None:
    if isinstance(node, bool) or not isinstance(node, int):
        return None
    return node


def _qualifiers(node: Any) -> frozenset[Qualifier]:
    if not isinstance(node, Sequence) or isinstance(node, str | bytes):
        return frozenset()
    known = {qualifier.value: qualifier for qualifier in Qualifier}
    return frozenset(known[item] for item in node if isinstance(item, str) and item in known)


# --------------------------------------------------------------------------- #
# The table.
# --------------------------------------------------------------------------- #
_TAGS: Mapping[str, Tag] = {tag.value: tag for tag in Tag}


def _reservation_provenance(reservation: ReservationView) -> Provenance:
    tag = _TAGS.get(reservation.source, Tag.UNVERIFIED)
    if tag is Tag.DERIVED:
        # A reservation tagged `derived` has no inputs recorded in the file, so
        # the honest display is the weakest possible one rather than a guess.
        return Provenance(
            tag=Tag.DERIVED,
            effective=Tag.UNVERIFIED,
            qualifiers=frozenset({Qualifier.NOT_MEASURED}),
        )
    return Provenance.of(tag)


def check_budget(
    budget: BudgetView,
    observations: Mapping[tuple[str, str], ConsumerObservation],
) -> Check:
    """§6.1's deduction table: the process, the result, and the ⚠ that follows.

    One check for every bucket of every venue, because the operator's decision
    is one decision — start or do not start — and because the interesting
    comparison is between buckets: on this egress the Binance weight bucket is
    a quarter full and the Hyperliquid one is over the line.
    """
    lines: list[str] = []
    statuses: list[Status] = []
    provenances: list[Provenance] = []

    for venue in budget.venues():
        for key in budget.keys_of(venue):
            bucket = budget.bucket(str(key))
            block, status, provenance = _render_bucket(str(key), bucket, observations)
            lines.extend(block)
            lines.append("")
            statuses.append(status)
            provenances.append(provenance)

    status = worst(statuses)
    counts = {
        name: sum(1 for item in statuses if item is name)
        for name in (Status.GREEN, Status.YELLOW, Status.UNKNOWN, Status.RED)
    }
    headline = (
        f"{len(statuses)} 个桶："
        + " · ".join(
            f"{count} {name.value}" for name, count in counts.items() if count
        )
    )
    if status is not Status.GREEN:
        headline += "（账面平不等于现实平，见下）"
    return Check(
        key="budget_deduction",
        title="共享出口预算扣减",
        status=status,
        headline=headline,
        lines=tuple(lines[:-1] if lines else lines),
        provenance=Provenance.derive(*provenances) if provenances else None,
    )


def _render_bucket(
    key: str,
    bucket: BucketView,
    observations: Mapping[tuple[str, str], ConsumerObservation],
) -> tuple[list[str], Status, Provenance]:
    share_text = f"{float(bucket.share):.0%}"
    lines = [f"{key}  ({bucket.kind})"]
    lines.append(
        f"  账面  官方 {bucket.official_limit_per_min}/min x {share_text}"
        f" = 出口合计上限 {bucket.egress_ceiling_per_min}/min"
    )

    reservation_provenances: list[Provenance] = []
    for reservation in sorted(bucket.reservations, key=lambda item: item.consumer):
        provenance = _reservation_provenance(reservation)
        observation = observations.get((reservation.consumer, key))
        if observation is not None and observation.qualifiers:
            provenance = Provenance(
                tag=provenance.tag,
                effective=provenance.effective,
                qualifiers=provenance.qualifiers | observation.qualifiers,
            )
        reservation_provenances.append(provenance)
        lines.append(
            f"        - {reservation.consumer:<14}{reservation.reserved_per_min:>6}/min"
            f"   {provenance.render()}"
        )
        if reservation.assertion:
            lines.append(f"            断言：{_clip(reservation.assertion)}")
            lines.append(f"            复核：{reservation.checked_by}")
        if reservation.doc:
            lines.append(f"            出处：{reservation.doc}")

    ours = Provenance.derive(
        Provenance.of(Tag.OFFICIAL, source="config/venues.yaml"),
        *reservation_provenances,
        note="config/egress-consumers.yaml",
    ) if reservation_provenances else Provenance.of(Tag.OFFICIAL)
    lines.append(
        f"        = Σ 预留 {bucket.reserved_per_min}/min"
        f"  ->  我们可用 {bucket.our_ceiling_per_min}/min   {ours.render()}"
    )
    formula = bucket.opportunistic_formula_per_min(bucket.resident_steady_per_min)
    lines.append(
        f"        档位 {bucket.profile.name}：resident 稳态 {bucket.resident_steady_per_min}"
        f" · reserve {bucket.reserve_per_min}"
        f"（快道地板 {bucket.fast_lane_floor_per_min}）"
        f" · opportunistic 算式 {formula} -> 硬顶 {bucket.opportunistic_hard_cap_per_min}"
    )

    reality, status = _render_reality(key, bucket, observations)
    lines.extend(reality)
    return lines, status, ours


def _render_reality(
    key: str,
    bucket: BucketView,
    observations: Mapping[tuple[str, str], ConsumerObservation],
) -> tuple[list[str], Status]:
    """The second and third blocks: what the egress actually does, and the call.

    ``our_practical_max`` is ``reserve + opportunistic 硬顶`` rather than our
    whole ceiling, because that is the most the ledger will ever admit: the
    reserve is resident's floor *including* its retry margin, and opportunistic
    is hard-capped above it. Using the ceiling instead would make every bucket
    look over the line, which is the other way to make this table unreadable.
    """
    ceiling = bucket.egress_ceiling_per_min
    our_practical_max = bucket.reserve_per_min + bucket.opportunistic_hard_cap_per_min
    lines = ["  现实"]
    others_worst = 0
    unmeasured: list[str] = []
    crossings: list[str] = []

    for reservation in sorted(bucket.reservations, key=lambda item: item.consumer):
        observation = observations.get((reservation.consumer, key))
        worst_known = observation.worst_known_per_min if observation else None
        if worst_known is None:
            if reservation.reserved_per_min == 0 and reservation.assertion:
                lines.append(
                    f"        {reservation.consumer:<14}"
                    f"   0/min —— 由断言保证，不是没测（见上）"
                )
                continue
            unmeasured.append(reservation.consumer)
            lines.append(
                f"        {reservation.consumer:<14}"
                f"   预留 {reservation.reserved_per_min}/min，但没有观测峰值："
                f"不知道它最多会打到多少"
            )
            others_worst += reservation.reserved_per_min
            continue
        assert observation is not None
        detail = []
        if observation.observed_max_per_min is not None:
            detail.append(f"观测最大 {observation.observed_max_per_min}/min")
        if observation.self_cap_per_min is not None:
            detail.append(f"自配上限 {observation.self_cap_per_min}/min")
        lines.append(f"        {reservation.consumer:<14}   " + " · ".join(detail))
        others_worst += worst_known
        # Both numbers are reported separately when both cross: "it已经越过一次"
        # and "它随时可以再越" are different arguments, and §6.1 makes both.
        if (observation.observed_max_per_min or 0) > ceiling:
            crossings.append(
                f"{reservation.consumer} 的观测最大 {observation.observed_max_per_min}"
                f" > 天花板 {ceiling} —— 已经发生过，而且只是它一家"
            )
        if (observation.self_cap_per_min or 0) > ceiling:
            crossings.append(
                f"{reservation.consumer} 的自配上限 {observation.self_cap_per_min}"
                f" > 天花板 {ceiling} —— 它随时可以再越，而且它不知道本项目存在"
            )

    lines.append(
        f"        我们实际最多打得出 reserve {bucket.reserve_per_min}"
        f" + 机会硬顶 {bucket.opportunistic_hard_cap_per_min}"
        f" = {our_practical_max}/min"
    )
    together = others_worst + our_practical_max
    lines.append(
        f"        合计最坏 {others_worst} + {our_practical_max} = {together}/min"
        f"  vs 天花板 {ceiling}/min"
    )

    if crossings:
        return (
            [
                *lines,
                "  判定  ⚠ 账面平，现实没平：",
                *(f"        · {crossing}" for crossing in crossings),
                f"        {bucket.reserved_per_min} + {bucket.our_ceiling_per_min}"
                f" = {ceiling} 是加载器保证的恒等式（预留额超过天花板它会直接拒载），",
                "        不是出口占用的证据。它越线的那些分钟里，我们这一份在出口上并不存在。",
                "        修法不在本项目手里：把那个消费者的自配额压到天花板以内，由 raphael 决定"
                "（03 §6.1 ⚠）。",
            ],
            Status.YELLOW,
        )
    if unmeasured:
        return (
            [
                *lines,
                f"  判定  ?? 未检出：{'、'.join(unmeasured)} 的峰值没有观测值，"
                "只有一个预留额。",
                "        §6.1：「它对不上现实时，错的方式是「我们以为有 X 权重其实没有」」——"
                "所以这里不作绿。",
            ],
            Status.UNKNOWN,
        )
    if together > ceiling:
        return (
            [
                *lines,
                f"  判定  ⚠ 账面平，最坏情形没平：{together} > {ceiling}。",
                "        两家各自打到自己允许的上限就会越线；账面等式仍然成立，"
                "它描述的是账本不是出口。",
            ],
            Status.YELLOW,
        )
    return (
        [
            *lines,
            f"  判定  账面与实测都在天花板内（最坏 {together} <= {ceiling}）。"
            "注意这句话是关于观测窗口的，不是关于未来的。",
        ],
        Status.GREEN,
    )


def _clip(text: str, limit: int = 88) -> str:
    flattened = " ".join(text.split())
    return flattened if len(flattened) <= limit else flattened[: limit - 1] + "…"
