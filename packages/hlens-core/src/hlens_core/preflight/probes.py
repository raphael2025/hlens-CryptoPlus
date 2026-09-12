"""Reachability, clock skew, rate-limit headers and the coverage-probe hook (06 §6)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ..contracts import now_ms
from ..ratelimit import VenueSpec, load_venues
from .health import CheckKind, SourceHealth, Verdict

#: 451 is always a policy block. 403 is ambiguous -- Bybit returns it both for
#: "access too frequent" and for a blocked egress -- so the body decides.
GEO_STATUS = (451, 403)
CLOCK_TOLERANCE_MS = 1000


def _dig(obj: Any, path: str) -> Any:
    """``"result.timeNano"`` / ``"data.0.ts"`` -> the nested value, or None."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


#: Divisor that turns a venue's time unit into milliseconds. Decimal, not float: a
#: nanosecond epoch is ~1.8e18 and float64 silently loses the low digits.
_TIME_DIVISOR = {"ms": Decimal(1), "s": Decimal("0.001"), "us": Decimal(1000),
                 "ns": Decimal(1_000_000)}


def _to_ms(value: Any, unit: str) -> int | None:
    try:
        n = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        return None
    return int(n / _TIME_DIVISOR.get(unit, Decimal(1)))


async def probe_reachability(
    client: httpx.AsyncClient, spec: VenueSpec, *, timeout_s: float = 10.0
) -> SourceHealth:
    """Hit the venue's cheapest endpoint. Records status, latency and geo classification."""
    url = spec.raw.get("probe_url")
    if not url:
        return SourceHealth(
            venue=spec.name, kind=CheckKind.reachability, verdict=Verdict.skipped,
            ok=True, detail="no probe_url in venues.yaml",
        )
    method = str(spec.raw.get("probe_method", "GET")).upper()
    body = spec.raw.get("probe_body")
    started = time.monotonic()
    try:
        r = await client.request(method, url, json=body, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001
        return SourceHealth.failure(
            spec.name, CheckKind.reachability,
            f"{type(exc).__name__}: {str(exc)[:80]}",
            latency_ms=(time.monotonic() - started) * 1000,
        )
    latency = (time.monotonic() - started) * 1000
    text = r.text[:200].lower()
    geo = r.status_code == 451 or (
        r.status_code == 403 and "frequent" not in text and "rate" not in text
    )
    if r.status_code == 200:
        return SourceHealth(
            venue=spec.name, kind=CheckKind.reachability, verdict=Verdict.ok, ok=True,
            http_status=200, latency_ms=latency, detail=url,
            data={"headers": _rate_headers(r)},
        )
    return SourceHealth(
        venue=spec.name, kind=CheckKind.reachability,
        verdict=Verdict.fail, ok=False, http_status=r.status_code, latency_ms=latency,
        geo_blocked=geo,
        detail=("geo/policy block" if geo else "unexpected status") + f" from {url}",
    )


def _rate_headers(r: httpx.Response) -> dict[str, str]:
    keys = ("x-mbx-used-weight-1m", "x-gate-ratelimit-limit",
            "x-gate-ratelimit-requests-remain", "x-gate-ratelimit-reset-timestamp",
            "retry-after")
    return {k: v for k, v in ((k, r.headers.get(k)) for k in keys) if v}


async def probe_clock(
    client: httpx.AsyncClient, spec: VenueSpec, *, timeout_s: float = 10.0
) -> SourceHealth:
    """Local clock vs the venue's.

    Venues that publish no server time (Gate, Hyperliquid) fall back to the HTTP
    ``Date`` header, which has 1 s resolution -- enough to catch a machine that is
    minutes off, which is what this check is actually for.
    """
    url = spec.raw.get("time_url") or spec.raw.get("probe_url")
    if not url:
        return SourceHealth(
            venue=spec.name, kind=CheckKind.clock, verdict=Verdict.skipped, ok=True,
            detail="no time endpoint",
        )
    method = "GET" if spec.raw.get("time_url") else str(spec.raw.get("probe_method", "GET"))
    body = None if spec.raw.get("time_url") else spec.raw.get("probe_body")
    try:
        r = await client.request(method.upper(), url, json=body, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001
        return SourceHealth.failure(
            spec.name, CheckKind.clock, f"{type(exc).__name__}: {str(exc)[:60]}"
        )
    local = now_ms()
    server_ms: int | None = None
    field = spec.raw.get("time_field")
    if field and r.status_code == 200:
        try:
            server_ms = _to_ms(_dig(r.json(), field), str(spec.raw.get("time_unit", "ms")))
        except ValueError:
            server_ms = None
    resolution = "endpoint"
    if server_ms is None and r.headers.get("date"):
        try:
            server_ms = int(parsedate_to_datetime(r.headers["date"]).timestamp() * 1000)
            resolution = "Date header (1 s)"
        except (TypeError, ValueError):
            server_ms = None
    if server_ms is None:
        return SourceHealth.failure(
            spec.name, CheckKind.clock, "no server time available",
            http_status=r.status_code,
        )
    skew = local - server_ms
    tolerance = CLOCK_TOLERANCE_MS + (1000 if resolution != "endpoint" else 0)
    ok = abs(skew) <= tolerance
    return SourceHealth(
        venue=spec.name, kind=CheckKind.clock,
        verdict=Verdict.ok if ok else Verdict.warn, ok=ok, http_status=r.status_code,
        latency_ms=float(skew), detail=f"skew {skew:+d} ms via {resolution}",
        data={"skew_ms": skew, "resolution": resolution},
    )


#: A coverage probe answers one question: does (venue, symbol, endpoint) return data?
#: Ratio endpoints come back empty for illiquid symbols, and an empty list is NOT an
#: error -- it is a facet that must be tagged `insufficient` rather than shown as zero.
CoverageProbe = Callable[[str, str], Awaitable[bool]]


async def probe_coverage(
    venue: str,
    symbols: Sequence[str],
    probes: dict[str, CoverageProbe],
    *,
    concurrency: int = 4,
) -> list[SourceHealth]:
    """Build the venue x symbol x endpoint coverage matrix.

    ``probes`` maps an endpoint label to a coroutine ``(venue, symbol) -> bool``. The
    adapter owns those callables; this function only schedules them and shapes the
    result, so it works for a venue whose adapter does not exist yet.
    """
    sem = asyncio.Semaphore(concurrency)

    async def one(label: str, probe: CoverageProbe, symbol: str) -> tuple[str, str, bool, str]:
        async with sem:
            try:
                return label, symbol, bool(await probe(venue, symbol)), ""
            except Exception as exc:  # noqa: BLE001
                return label, symbol, False, f"{type(exc).__name__}: {str(exc)[:60]}"

    tasks = [one(lbl, p, s) for lbl, p in probes.items() for s in symbols]
    results = await asyncio.gather(*tasks)
    by_label: dict[str, dict[str, bool]] = {}
    errs: dict[str, str] = {}
    for label, symbol, has_data, err in results:
        by_label.setdefault(label, {})[symbol] = has_data
        if err:
            errs[f"{label}:{symbol}"] = err
    out = []
    for label, matrix in by_label.items():
        missing = sorted(s for s, ok in matrix.items() if not ok)
        out.append(
            SourceHealth(
                venue=venue, kind=CheckKind.coverage,
                verdict=Verdict.ok if not missing else Verdict.warn,
                ok=True,  # missing coverage greys out a facet; it does not fail the venue
                detail=f"{label}: {len(matrix) - len(missing)}/{len(matrix)} symbols"
                + (f"; empty: {','.join(missing[:6])}" if missing else ""),
                data={"endpoint": label, "matrix": matrix, "errors": errs},
            )
        )
    return out


async def run_probes(
    venues: Sequence[str] | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = 10.0,
) -> list[SourceHealth]:
    """Reachability + clock for each venue, concurrently."""
    specs = load_venues()
    names = list(venues) if venues else list(specs)
    own = client is None
    client = client or httpx.AsyncClient(
        headers={"user-agent": "hlens-core/0.1 preflight"}, follow_redirects=True
    )
    try:
        jobs: list[Any] = []
        for n in names:
            spec = specs[n]
            jobs.append(probe_reachability(client, spec, timeout_s=timeout_s))
            jobs.append(probe_clock(client, spec, timeout_s=timeout_s))
        return list(await asyncio.gather(*jobs))
    finally:
        if own:
            await client.aclose()
