"""`SourceHealth` -- what one preflight check found, and what the status page shows."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import now_ms


class CheckKind(StrEnum):
    egress = "egress"
    reachability = "reachability"
    clock = "clock"
    coverage = "coverage"
    rate_limit_headers = "rate_limit_headers"


class Verdict(StrEnum):
    ok = "ok"
    warn = "warn"
    fail = "fail"
    skipped = "skipped"


class SourceHealth(BaseModel):
    """One (venue, kind) result. Rows land in the `source_health` table (06 §6)."""

    model_config = ConfigDict(extra="forbid")

    venue: str
    kind: CheckKind
    verdict: Verdict = Verdict.ok
    ok: bool = True
    http_status: int | None = None
    latency_ms: float | None = None
    geo_blocked: bool = False
    detail: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    checked_ts: int = Field(default_factory=now_ms)

    @classmethod
    def failure(cls, venue: str, kind: CheckKind, detail: str, **kw: Any) -> SourceHealth:
        return cls(venue=venue, kind=kind, verdict=Verdict.fail, ok=False, detail=detail, **kw)


class EgressIdentity(BaseModel):
    """Public egress identity: the key every rate-limit budget is accounted against.

    Two independent observers are queried. Disagreement is reported rather than
    silently averaged, and a total failure is tolerated (``ip=None``) so preflight can
    still report the rest -- but a collector must refuse to start without an IP,
    because with no key there is no shared ledger.
    """

    model_config = ConfigDict(extra="forbid")

    ip: str | None = None
    asn: str | None = None
    country: str | None = None
    sources: list[str] = Field(default_factory=list)
    agreed: bool = True
    conflicts: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PreflightReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    egress: EgressIdentity
    checks: list[SourceHealth] = Field(default_factory=list)
    started_ts: int = Field(default_factory=now_ms)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def venue_ok(self, venue: str) -> bool:
        return all(c.ok for c in self.checks if c.venue == venue)

    def to_table(self) -> str:
        """Fixed-width table; the CLI prints this above the JSON."""
        head = ("venue", "check", "verdict", "http", "ms", "detail")
        rows = [
            (
                c.venue,
                c.kind.value,
                c.verdict.value,
                "" if c.http_status is None else str(c.http_status),
                "" if c.latency_ms is None else f"{c.latency_ms:.0f}",
                c.detail[:58],
            )
            for c in self.checks
        ]
        widths = [max(len(str(r[i])) for r in (head, *rows)) for i in range(len(head))]
        line = "  ".join("-" * w for w in widths)
        out = [
            "  ".join(str(h).ljust(w) for h, w in zip(head, widths, strict=True)),
            line,
            *(
                "  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)).rstrip()
                for r in rows
            ),
        ]
        e = self.egress
        out.append("")
        out.append(
            f"egress: ip={e.ip or '?'} asn={e.asn or '?'} country={e.country or '?'} "
            f"sources={','.join(e.sources) or 'none'}"
            + ("" if e.agreed else f" CONFLICT: {'; '.join(e.conflicts)}")
        )
        return "\n".join(out)
