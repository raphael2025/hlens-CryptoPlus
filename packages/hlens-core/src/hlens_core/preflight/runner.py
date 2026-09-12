"""Tie the checks together into one report."""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from .egress import resolve_egress
from .health import CheckKind, PreflightReport, SourceHealth, Verdict
from .probes import run_probes


async def run_preflight(
    venues: Sequence[str] | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = 10.0,
) -> PreflightReport:
    """Run every check once. Never fails fast: one report lists everything that is wrong."""
    own = client is None
    client = client or httpx.AsyncClient(
        headers={"user-agent": "hlens-core/0.1 preflight"}, follow_redirects=True
    )
    try:
        egress = await resolve_egress(client, timeout_s=timeout_s)
        checks = await run_probes(venues, client=client, timeout_s=timeout_s)
    finally:
        if own:
            await client.aclose()

    egress_check = SourceHealth(
        venue="-",
        kind=CheckKind.egress,
        verdict=Verdict.ok if (egress.ip and egress.agreed) else Verdict.warn,
        ok=bool(egress.ip),
        detail=(
            f"{egress.ip} {egress.asn or ''} {egress.country or ''}".strip()
            if egress.ip
            else "egress IP unknown: " + "; ".join(egress.errors)
        ),
        data=egress.model_dump(),
    )
    return PreflightReport(egress=egress, checks=[egress_check, *checks])
