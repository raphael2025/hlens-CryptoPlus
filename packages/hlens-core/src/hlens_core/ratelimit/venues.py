"""Venue rate-limit constants, loaded from ``config/venues.yaml``.

The YAML is the single source of truth (06 §4) and carries a ``source`` column per
venue: ``official`` / ``measured`` / ``unverified``. Code never hardcodes a ceiling;
it asks :func:`load_venues`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_NAME = "config/venues.yaml"

# Charged when an HL /info type is not in the table. The documented default for
# "other info requests" is 20; over-estimating is the only safe direction, because
# under-estimating makes the scheduler think it has headroom and walk into a 429
# that stalls every collector behind the same egress.
HL_DEFAULT_WEIGHT = 20


class VenueConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BucketSpec:
    """One accounting bucket: ``budget`` units per ``window_s`` seconds."""

    name: str
    unit: str  # "weight" | "request"
    limit: int  # official upstream ceiling
    window_s: float
    budget: int  # what we allow ourselves
    source: str  # official | measured | unverified

    def __post_init__(self) -> None:
        if self.budget <= 0 or self.window_s <= 0:
            raise VenueConfigError(f"bucket {self.name}: budget and window must be > 0")
        if self.budget > self.limit:
            raise VenueConfigError(
                f"bucket {self.name}: budget {self.budget} exceeds the upstream limit {self.limit}"
            )
        if self.source not in {"official", "measured", "unverified"}:
            raise VenueConfigError(f"bucket {self.name}: unknown source {self.source!r}")


@dataclass(frozen=True, slots=True)
class VenueSpec:
    name: str
    main: BucketSpec
    max_inflight: int
    aimd_cut: float
    aimd_freeze_s: float
    aimd_recover_per_window: float
    buckets: dict[str, BucketSpec] = field(default_factory=dict)
    endpoint_buckets: dict[str, str] = field(default_factory=dict)
    weights: dict[str, int] = field(default_factory=dict)
    rows_per_weight: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def weight_for(self, endpoint: str, *, override: int | None = None) -> int:
        """Weight charged for one call of ``endpoint``.

        An explicit ``override`` always wins: the table holds the cost of the form we
        usually call, but several Binance endpoints are priced by their arguments
        (``premiumIndex`` is 10 for the market and 1 for one symbol, ``klines`` is tiered
        by ``limit``), and only the caller knows which form it is sending.

        Venues that bill requests rather than weight return 1. Unknown Hyperliquid info
        types fall back to :data:`HL_DEFAULT_WEIGHT`.
        """
        if override is not None:
            return override
        if endpoint in self.weights:
            return self.weights[endpoint]
        if self.main.unit == "weight":
            return HL_DEFAULT_WEIGHT if self.name == "hyperliquid" else 1
        return 1

    def bucket_for(self, endpoint: str) -> BucketSpec | None:
        """Extra per-endpoint bucket, if this endpoint has one."""
        named = self.endpoint_buckets.get(endpoint)
        if named:
            return self.buckets[named]
        return self.buckets.get(endpoint)

    @property
    def rest_bases(self) -> list[str]:
        return list(self.raw.get("rest_bases") or [])

    @property
    def source(self) -> str:
        return self.main.source


def _bucket(name: str, d: dict[str, Any], *, fallback_source: str) -> BucketSpec:
    return BucketSpec(
        name=name,
        unit=str(d.get("unit", "request")),
        limit=int(d["limit"]),
        window_s=float(d.get("window_s", 60)),
        budget=int(d["budget"]),
        source=str(d.get("source", fallback_source)),
    )


def find_config(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Locate ``config/venues.yaml``: explicit arg, then ``$HLENS_VENUES``, then repo root."""
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("HLENS_VENUES")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / DEFAULT_CONFIG_NAME
        if candidate.is_file():
            return candidate
    raise VenueConfigError(
        f"could not locate {DEFAULT_CONFIG_NAME}; set $HLENS_VENUES to point at it"
    )


def parse_venues(doc: dict[str, Any]) -> dict[str, VenueSpec]:
    defaults = doc.get("defaults") or {}
    out: dict[str, VenueSpec] = {}
    for name, v in (doc.get("venues") or {}).items():
        src = str(v.get("source", "unverified"))
        main = _bucket(
            name,
            {
                "unit": v.get("unit", "request"),
                "limit": v["limit"],
                "window_s": v.get("window_s", 60),
                "budget": v["budget"],
                "source": src,
            },
            fallback_source=src,
        )
        extra = {
            k: _bucket(k, d, fallback_source=src) for k, d in (v.get("buckets") or {}).items()
        }
        out[name] = VenueSpec(
            name=name,
            main=main,
            max_inflight=int(v.get("max_inflight", defaults.get("max_inflight", 8))),
            aimd_cut=float(v.get("aimd_cut", defaults.get("aimd_cut", 0.75))),
            aimd_freeze_s=float(v.get("aimd_freeze_s", defaults.get("aimd_freeze_s", 3600))),
            aimd_recover_per_window=float(
                v.get(
                    "aimd_recover_per_window", defaults.get("aimd_recover_per_window", 0.05)
                )
            ),
            buckets=extra,
            endpoint_buckets=dict(v.get("endpoint_buckets") or {}),
            weights={str(k): int(w) for k, w in (v.get("weights") or {}).items()},
            rows_per_weight={
                str(k): int(w) for k, w in (v.get("rows_per_weight") or {}).items()
            },
            raw=v,
        )
    if not out:
        raise VenueConfigError("venues.yaml has no venues")
    return out


@lru_cache(maxsize=8)
def load_venues(path: str | None = None) -> dict[str, VenueSpec]:
    p = find_config(path)
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    return parse_venues(doc)


def venue(name: str, path: str | None = None) -> VenueSpec:
    venues = load_venues(path)
    try:
        return venues[name]
    except KeyError:
        raise VenueConfigError(
            f"unknown venue {name!r}; known: {sorted(venues)}"
        ) from None


#: Convenience alias so callers can write ``VENUES["binance"]`` like a constants table.
class _VenueTable:
    def __getitem__(self, name: str) -> VenueSpec:
        return venue(name)

    def __iter__(self):
        return iter(load_venues())

    def __contains__(self, name: object) -> bool:
        return name in load_venues()

    def keys(self):
        return load_venues().keys()

    def items(self):
        return load_venues().items()


VENUES = _VenueTable()

#: Hyperliquid /info weights, from venues.yaml. Kept as a module constant because the
#: HL adapter and the budget both key off it.
def hl_info_weights() -> dict[str, int]:
    return dict(venue("hyperliquid").weights)


def hl_weight_for(info_type: str, rows: int | None = None) -> int:
    """Weight for one HL ``/info`` call.

    History-shaped types are charged per rows returned (``+1`` per 20 rows, per 60 for
    ``candleSnapshot``); pass ``rows`` at settle time to book the real cost. Without
    ``rows`` the per-page upper bound from the table is returned, which is what
    ``reserve()`` should use.
    """
    spec = venue("hyperliquid")
    per = spec.rows_per_weight.get(info_type)
    if rows is not None and per:
        return max(1, -(-rows // per))  # ceil
    return spec.weight_for(info_type)
