"""How an adapter obeys the rate-limit ledger without importing it.

The problem
-----------
An adapter is the only thing that knows what a call costs: that
``premiumIndex`` without a symbol charges 10 and with one charges 1, that
``/futures/data/*`` charges no weight at all and is metered in requests
against its own bucket, that Hyperliquid's ``candleSnapshot`` charges 1 per 60
rows. The ledger is the only thing that knows whether we may spend it — the
egress IP is shared with an older collector, so "may I" is not a question an
adapter can answer locally (``03`` §6.1).

Seam ③ forbids ``adapters`` from importing ``ratelimit``
(``tests/test_module_boundaries.py``: ``"adapters": frozenset({"contracts"})``),
and the two bad ways out are bad for the same reason — they move venue
knowledge or module coupling to where it does not belong:

* adding the edge would make the weight table's owner depend on the budget's
  owner, in the one package M4 and M5 must reopen;
* leaving the weights to the caller scatters a per-venue table across the
  collector, so adding a venue would mean editing every call site — the exact
  cost seam ② exists to prevent.

The way out: dependency inversion, structurally
-----------------------------------------------
:class:`SpendAuthority` is a :class:`typing.Protocol` written to match what the
ledger **already** looks like. ``hlens_core.ratelimit.RateLimitLedger``
satisfies ``SpendAuthority[Priority, Grant]`` with no base class, no
registration and no change to it whatsoever — ``tests/test_adapter_admission.py``
asserts that by assignment, which is checked statically by mypy (that one test
file is in ``[tool.mypy] files``) and again at runtime. Neither package imports
the other; the collector, which imports both anyway, passes one to the other.

Two typing details decide the shape
-----------------------------------
``acquire``'s parameters are checked **contravariantly**: an implementation may
accept *wider* types than the protocol declares, never narrower.

* ``key``: the ledger accepts ``BucketKey | str``, which is wider than the
  ``str`` declared here — fine, and it is why the bucket travels as the
  ``"venue:bucket"`` string the ledger already parses and logs.
* ``priority``: the ledger accepts ``Priority``, a ``StrEnum``, which is
  *narrower* than ``str``. Declaring ``priority: str`` would therefore be the
  one thing that does not work — the ledger would fail to satisfy the protocol.
  Declaring ``Any`` would hide the mismatch instead of fixing it: it would type
  check while letting a :class:`LanePriority` member — a different enum, with
  equal string values but different identity — reach the ledger, where
  ``priority is Priority.FAST_LANE`` is an identity test and would silently
  treat the fast lane as opportunistic. So the protocol is **generic** in the
  priority type (contravariant) and in the grant type (invariant: the grant is
  returned by ``acquire`` and consumed by ``settle``), and the adapter never
  constructs a priority value at all. It names a :class:`LanePriority`, and
  :class:`Admission` — built by the caller, which owns both vocabularies —
  translates it once.

So the weight table travels with the adapter, the budget stays with the ledger,
and the one line that knows both is in the collector's wiring.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, TypeVar

__all__ = [
    "Admission",
    "AnyAdmission",
    "CallCost",
    "LanePriority",
    "SpendAuthority",
]

PriorityT_contra = TypeVar("PriorityT_contra", contravariant=True)
GrantT = TypeVar("GrantT")

#: ``venue:bucket``, exactly as ``config/venues.yaml`` names it and as the
#: ledger prints it in a log line. Adapters do not invent bucket names; they
#: quote the config's, and preflight (M1-B) is where a name that does not exist
#: is caught before a single request is sent.
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9_]*:[a-z0-9][a-z0-9_]*$")


class LanePriority(StrEnum):
    """The adapter's word for which tier a call belongs to (``03`` §6.1).

    A separate enum from ``ratelimit.Priority`` on purpose — seam ③ — with
    deliberately identical **values**, so the caller's translation is one call
    and so a drift is detectable: ``tests/test_adapter_admission.py`` asserts
    the two value sets are equal, and that assertion is the only place in the
    repository allowed to know both names.
    """

    FAST_LANE = "fast_lane"
    """The 30-second price/funding lane, whose floor is fenced off from every
    other resident lane at all times."""

    RESIDENT = "resident"
    """Every other standing lane: the 60-second slow lane, the ratio lane, the
    daily universe reconciliation."""

    OPPORTUNISTIC = "opportunistic"
    """Backfill and anything else that must give way to the resident lanes
    entirely (决定 A7)."""


@dataclass(frozen=True, slots=True)
class CallCost:
    """What one call costs, as the adapter alone can state it.

    This is the whole weight table, one row at a time: which bucket it charges,
    how much, which tier it runs in, and which paced lane it belongs to. A
    caller reads it off the adapter and hands it back to the ledger; it never
    holds a number of its own.
    """

    bucket: str
    """``"<venue>:<bucket>"`` — e.g. ``"binance:fapi_weight"``,
    ``"binance:futures_data"``, ``"hyperliquid:info_weight"``. A weight bucket
    and a request bucket are never the same account (``04`` §4)."""

    weight: int
    """Weight for a weight bucket, or 1 for one request against a request
    bucket. At least 1 — a call that costs nothing does not exist on a metered
    endpoint, and a 0 would make the ledger's arithmetic pass silently."""

    priority: LanePriority
    lane: str | None = None
    """The paced lane this call belongs to, if the caller registered one
    (``03`` §6, burst shaping: 540 requests per 10 minutes are spread over the
    window instead of fired in its first second)."""

    endpoint: str | None = None
    """Which endpoint this row is about, for the log line and the PR table.
    Never a URL — hosts live in ``config/venues.yaml`` (AGENTS §2.3)."""

    def __post_init__(self) -> None:
        if not _BUCKET_RE.match(self.bucket):
            raise ValueError(
                f"bucket must be '<venue>:<bucket>' as config/venues.yaml spells it; "
                f"got {self.bucket!r}"
            )
        if self.weight < 1:
            raise ValueError(f"a metered call costs at least 1; got {self.weight}")
        if self.endpoint is not None and "://" in self.endpoint:
            raise ValueError("endpoints are paths, not URLs; hosts live in config/venues.yaml")


class SpendAuthority(Protocol[PriorityT_contra, GrantT]):
    """The ledger, seen structurally — the only thing an adapter needs of it.

    Every signature below is the one ``RateLimitLedger`` already has. Nothing
    here asks it to change, and nothing here is allowed to: ``ratelimit`` is
    M1-A2's, frozen for this step.
    """

    def acquire(
        self,
        key: str,
        *,
        cost: int,
        priority: PriorityT_contra,
        lane: str | None = None,
    ) -> GrantT:
        """May I spend ``cost`` on ``key``? Answers; sends nothing."""
        ...

    def settle(
        self,
        grant: GrantT,
        *,
        actual_cost: int | None = None,
        status: int = 200,
        retry_after_s: float | None = None,
        body: str | bytes | None = None,
    ) -> None:
        """The call is over: here is what it really cost and what came back."""
        ...

    def release(self, grant: GrantT) -> None:
        """Hand the allowance back — the call was never sent."""
        ...

    def observe_response(
        self,
        key: str,
        *,
        status: int,
        retry_after_s: float | None = None,
        body: str | bytes | None = None,
    ) -> None:
        """A status code with no grant behind it — a WebSocket handshake, say."""
        ...


@dataclass(frozen=True, slots=True)
class Admission[PriorityT, GrantT]:
    """A :class:`SpendAuthority` bound to the caller's priority vocabulary.

    The collector builds one of these when it wires an adapter up::

        admission = Admission(ledger, lambda lane: Priority(lane.value))

    and every adapter method takes it and spends through it. That lambda is the
    only place in the system where the two priority vocabularies meet, and it
    contains no weights: the numbers stay on the adapter, where adding a venue
    changes nothing anywhere else.
    """

    authority: SpendAuthority[PriorityT, GrantT]
    priority_of: Callable[[LanePriority], PriorityT]

    def acquire(self, cost: CallCost) -> GrantT:
        """Ask for the allowance one :class:`CallCost` describes."""
        return self.authority.acquire(
            cost.bucket,
            cost=cost.weight,
            priority=self.priority_of(cost.priority),
            lane=cost.lane,
        )

    def settle(
        self,
        grant: GrantT,
        *,
        actual_cost: int | None = None,
        status: int = 200,
        retry_after_s: float | None = None,
        body: str | bytes | None = None,
    ) -> None:
        self.authority.settle(
            grant,
            actual_cost=actual_cost,
            status=status,
            retry_after_s=retry_after_s,
            body=body,
        )

    def release(self, grant: GrantT) -> None:
        self.authority.release(grant)

    def observe(
        self,
        bucket: str,
        *,
        status: int,
        retry_after_s: float | None = None,
        body: str | bytes | None = None,
    ) -> None:
        """Report a status code that had no grant — a stream handshake, a 418
        seen on a connection. ``04`` §3: a Hyperliquid 429's **body type** is
        the only evidence of which limiter fired, so it is passed through
        verbatim rather than classified here."""
        self.authority.observe_response(
            bucket, status=status, retry_after_s=retry_after_s, body=body
        )


#: What an adapter method annotates: an :class:`Admission` whose priority and
#: grant types belong to whoever wired it up. The adapter never inspects either
#: — it receives the grant from :meth:`Admission.acquire` and hands the same
#: object back to :meth:`Admission.settle` — so naming them here would make
#: every adapter generic for no gain.
AnyAdmission = Admission[Any, Any]
