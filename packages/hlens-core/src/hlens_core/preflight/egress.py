"""Public egress identity (IP / ASN / country) from two independent observers.

Why two: the whole rate-limit model rests on "which public IP am I". One observer that
is hijacked, cached, or answering with a CDN edge address takes the budget, the
uniqueness rule and the geo-eligibility rule down with it -- silently. Cross-checking
two costs one extra request per hour.

Failure is tolerated here (preflight reports it) rather than raised: a collector decides
for itself whether to refuse to start.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx

from .health import EgressIdentity

_ASN_RE = re.compile(r"AS(\d+)", re.IGNORECASE)


def normalize_asn(raw: str | None) -> str | None:
    """``"AS24757 Ethio Telecom"`` / ``"as132203"`` / ``"132203"`` -> ``"AS24757"``."""
    if not raw:
        return None
    m = _ASN_RE.search(raw)
    if m:
        return f"AS{int(m.group(1))}"
    s = raw.strip()
    return f"AS{int(s)}" if s.isdigit() else None


def normalize_country(raw: str | None) -> str | None:
    return (raw or "").strip().upper() or None


@dataclass(frozen=True, slots=True)
class Observer:
    name: str
    url: str
    ip_key: str
    asn_key: str | None
    country_key: str | None


#: Keyless, no-signup observers. ``ifconfig.me`` and ``ipinfo.io`` are run by different
#: operators, which is the point.
OBSERVERS: tuple[Observer, ...] = (
    Observer("ipinfo.io", "https://ipinfo.io/json", "ip", "org", "country"),
    Observer("ifconfig.me", "https://ifconfig.me/all.json", "ip_addr", None, None),
)


async def _observe(client: httpx.AsyncClient, o: Observer, timeout: float):
    try:
        r = await client.get(o.url, timeout=timeout, headers={"accept": "application/json"})
        if r.status_code != 200:
            return o.name, None, f"{o.name}: http {r.status_code}"
        d = r.json()
    except Exception as exc:  # noqa: BLE001 -- any failure is just "no observation"
        return o.name, None, f"{o.name}: {type(exc).__name__}"
    return (
        o.name,
        {
            "ip": d.get(o.ip_key) or None,
            "asn": normalize_asn(d.get(o.asn_key)) if o.asn_key else None,
            "country": normalize_country(d.get(o.country_key)) if o.country_key else None,
        },
        None,
    )


async def resolve_egress(
    client: httpx.AsyncClient | None = None,
    *,
    timeout_s: float = 6.0,
    observers: tuple[Observer, ...] = OBSERVERS,
) -> EgressIdentity:
    """Ask every observer concurrently and merge.

    A field missing from one observer is not a conflict (geoip coverage differs); two
    *different* values for the same field is. Conflicts are reported, and the first
    observer's value is kept so downstream code still has something to key on.
    """
    own = client is None
    client = client or httpx.AsyncClient(headers={"user-agent": "hlens-core/0.1 preflight"})
    try:
        results = await asyncio.gather(*(_observe(client, o, timeout_s) for o in observers))
    finally:
        if own:
            await client.aclose()

    sources: list[str] = []
    errors: list[str] = []
    merged: dict[str, str | None] = {"ip": None, "asn": None, "country": None}
    conflicts: list[str] = []
    for name, obs, err in results:
        if err or not obs:
            errors.append(err or f"{name}: empty")
            continue
        sources.append(name)
        for field in ("ip", "asn", "country"):
            v = obs.get(field)
            if v is None:
                continue
            if merged[field] is None:
                merged[field] = v
            elif merged[field] != v:
                conflicts.append(f"{field}: {merged[field]} != {v} ({name})")
    return EgressIdentity(
        ip=merged["ip"],
        asn=merged["asn"],
        country=merged["country"],
        sources=sources,
        agreed=not conflicts,
        conflicts=conflicts,
        errors=errors,
    )
