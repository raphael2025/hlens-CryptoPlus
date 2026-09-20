"""What the ledger produces instead of writing to the database.

The ledger does accounting and admission. It does not open sockets and it does
not open database connections: a 429, a 418 and a budget change each produce a
typed *event*, and the collector is the one that turns an event into an
``ingest_gap`` row, an ``ops_event`` row and an F4 private message. Keeping it
this way is what lets the whole state machine be tested offline, and it keeps
the single-writer rule of §4 intact — ``ratelimit`` writes no table.

``ingest_gap.cause`` is a closed ten-value enumeration (§5). It is reproduced
here in full, with the exact spellings, so that a reader can see that this
module emits two of the ten and invents none: :data:`LEDGER_CAUSES`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

__all__ = [
    "LEDGER_CAUSES",
    "EventKind",
    "GapCause",
    "HlBodyKind",
    "LedgerEvent",
]


class GapCause(StrEnum):
    """``ingest_gap.cause`` — the closed ten-value enumeration of §5.

    Reproduced whole on purpose. A partial copy is how an eleventh value gets
    invented six months later. The writer named in §5's table is in the
    comment; this module is the writer for exactly two of them.
    """

    VENUE_ERROR = "venue_error"  # collector
    RATE_LIMIT = "rate_limit"  # ratelimit — 429 后冻结
    IP_BAN = "ip_ban"  # ratelimit — 418 后停该所全部车道
    BACKPRESSURE = "backpressure"  # liquidation
    WS_RECONNECT = "ws_reconnect"  # liquidation
    POWER_LOSS = "power_loss"  # collector, at startup
    HOST_RESTART = "host_restart"  # collector, at startup
    EGRESS_DOWN = "egress_down"  # health
    EGRESS_CHANGE = "egress_change"  # preflight / health
    UNKNOWN = "unknown"  # collector, at startup


#: The two values this module is the writer for (§5's 写方 column). The test
#: suite asserts the ledger never emits anything else.
LEDGER_CAUSES: Final[frozenset[GapCause]] = frozenset({GapCause.RATE_LIMIT, GapCause.IP_BAN})


class EventKind(StrEnum):
    """What happened. ``ops_event.kind`` uses the same spellings."""

    RATE_LIMIT = "rate_limit"
    IP_BAN = "ip_ban"
    BUDGET_CHANGED = "budget_changed"
    BUDGET_RECLAIMED = "budget_reclaimed"


class HlBodyKind(StrEnum):
    """Which Hyperliquid 429 this was.

    04 §3 (实测): the two are not the same failure and do not have the same
    remedy — a JSON ``null`` body means the weight limit, an nginx HTML page
    means the connection-rate limit, which is answered by lowering concurrency
    rather than by spending less weight. The response carries no rate-limit
    headers at all, so the body is the only signal there is.
    """

    WEIGHT = "weight"  # response body is JSON `null`
    CONNECTION = "connection"  # response body is an nginx HTML page
    UNRECOGNIZED = "unrecognized"


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """One thing the collector has to act on.

    ``gap_cause`` is set when the event means minutes of data were lost and an
    ``ingest_gap`` row is owed. ``ops_event`` marks the ones that belong in the
    operations log. ``notify_f4`` marks the ones that must reach a human as an
    F4 private message — §6.1 makes that mandatory for every Hyperliquid 429
    (on a shared egress a 429 means another consumer may be getting hit too,
    so it is not a routine backoff to swallow) and for every 418.
    """

    kind: EventKind
    venue: str
    ts: int
    gap_cause: GapCause | None = None
    ops_event: bool = False
    notify_f4: bool = False
    bucket: str | None = None
    detail: dict[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.gap_cause is not None and self.gap_cause not in LEDGER_CAUSES:
            raise ValueError(
                f"{self.gap_cause!r} is not one of the two ingest_gap causes this module "
                f"writes ({sorted(c.value for c in LEDGER_CAUSES)}); §5 names a different "
                "writer for the other eight"
            )
