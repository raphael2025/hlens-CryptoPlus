"""The rows preflight would write, built whether or not a database exists.

``03`` §4 gives this module two tables to write: ``source_health`` (the
observation columns only — the verdict columns belong to ``health``) and
``ops_event``. §5 gives it one value in ``ingest_gap``'s ten-value closed
enumeration: ``egress_change``, written when the egress hash stops matching
``EXPECTED_EGRESS_HASH``.

None of those tables exist yet — they are ``M1-C`` — and preflight has to run
anyway. That is not a temporary awkwardness to be worked around; it is the
requirement. The first time this command runs on a new machine is before
``docker compose up``, and the one day it matters most is the day the database
will not start. So:

* the checks build rows as **values**, always;
* printing them to stdout is the primary path and needs nothing;
* writing them is a :class:`HealthSink` someone else supplies, and a sink that
  is absent or refuses is a line in the report, not an exception.

The rows carry ``source`` and ``ingest_ts`` because §5's general rule puts
those two columns on every table, and building a row without them here would
just move the omission to ``M1-C``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

__all__ = [
    "EGRESS_CHANGE",
    "PREFLIGHT_SOURCE",
    "HealthSink",
    "IngestGapRow",
    "OpsEventRow",
    "SourceHealthRow",
    "render_rows",
]

#: ``source`` on every row this module produces: seam ①'s provenance column,
#: and the answer to "who wrote this line" when ``health`` later overwrites the
#: verdict columns of the same ``source_health`` key.
PREFLIGHT_SOURCE: Final = "preflight"

#: The one member of §5's ten-value ``ingest_gap.cause`` enumeration this
#: module is the writer of. It is quoted rather than redefined: the enumeration
#: is closed, ``M1-C``'s CHECK constraint is its authority, and a preflight
#: that invented an eleventh value would be inventing an excuse.
EGRESS_CHANGE: Final = "egress_change"


@dataclass(frozen=True, slots=True)
class SourceHealthRow:
    """One ``source_health`` observation row — observation columns only.

    §4: "`source_health` 的观测列与判定列分属两个写方". ``ok`` and
    ``consecutive_fail`` are ``health``'s and are deliberately absent here; a
    preflight that set them would be deciding, from a single sample at
    startup, something the health module decides from a series.
    """

    venue: str
    capability: str
    transport: str
    last_ok_ts: int | None = None
    latency_ms: int | None = None
    last_error_class: str | None = None
    ingest_ts: int | None = None
    source: str = PREFLIGHT_SOURCE


@dataclass(frozen=True, slots=True)
class OpsEventRow:
    """One ``ops_event`` row: ``(kind, ts)``, ``ok``, ``detail`` jsonb."""

    kind: str
    ts: int
    ok: bool
    detail: Mapping[str, Any] = field(default_factory=dict)
    ingest_ts: int | None = None
    source: str = PREFLIGHT_SOURCE


@dataclass(frozen=True, slots=True)
class IngestGapRow:
    """One ``ingest_gap`` row. ``cause`` is quoted from §5's closed enum."""

    venue: str
    metric: str
    from_ts: int
    to_ts: int | None
    minutes: int | None
    cause: str
    symbols_expected: int | None = None
    symbols_present: int | None = None
    ingest_ts: int | None = None
    source: str = PREFLIGHT_SOURCE


@runtime_checkable
class HealthSink(Protocol):
    """Somewhere to put the rows. Implemented by ``M1-C``, not by this step.

    Written as a protocol for the same reason as everything else across a seam
    here: preflight must not know whether the far side is psycopg, a file or a
    test. The CLI's default is no sink at all.
    """

    def write_source_health(self, row: SourceHealthRow) -> None: ...

    def write_ops_event(self, row: OpsEventRow) -> None: ...

    def write_ingest_gap(self, row: IngestGapRow) -> None: ...


def render_rows(rows: tuple[object, ...]) -> list[str]:
    """The rows as text, for the run where there is nowhere to put them.

    This is not a debugging aid. Until ``M1-C`` it is the *only* output path
    for ``ingest_gap(cause=egress_change)``, and a change of public IP has to
    be visible on the day it happens rather than on the day the table exists.
    """
    out: list[str] = []
    for row in rows:
        if isinstance(row, SourceHealthRow):
            out.append(
                f"source_health  venue={row.venue} capability={row.capability} "
                f"transport={row.transport} latency_ms={row.latency_ms} "
                f"last_error_class={row.last_error_class} source={row.source}"
            )
        elif isinstance(row, OpsEventRow):
            out.append(
                f"ops_event      kind={row.kind} ok={row.ok} detail={dict(row.detail)} "
                f"source={row.source}"
            )
        elif isinstance(row, IngestGapRow):
            out.append(
                f"ingest_gap     venue={row.venue} metric={row.metric} cause={row.cause} "
                f"minutes={row.minutes} source={row.source}"
            )
    return out
