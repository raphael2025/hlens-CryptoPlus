"""The two-venue reachability probe: written here, **not run by this task**.

``03`` §16 lists 两所可达 among preflight's eight items and ``04`` §7 第 2 项
says what it is: the cheapest probe per venue (Binance ``ping``, Hyperliquid
``meta``), recording the HTTP code, the latency and whether the refusal is a
geographic 451. ``04`` §6 is why it cannot be skipped — ``fapi.binance.com``
answers 451 from a US egress while the same paths under the mirror host answer
200, so which host the adapters may use is a fact about *this* egress that only
a live call settles.

Why nothing here ran while it was written
-----------------------------------------
This development machine leaves through the **same public egress IP** as the
production host (``03`` §8, confirmed by ``M1-B``), and that egress already
carries a collector that is not ours and that meters against the same per-IP
budget. ``04`` §12 states the consequence plainly: 在 hub 上做任何实测都会直接
消耗旧采集器正在用的预算 … 不能随手打. A single "let me just see what it
returns" would be spending someone else's quota to satisfy curiosity, and on
Binance the escalation from 429 to 418 bans the whole machine's IP — the older
collector included.

So the probe is **opt-in twice**: the CLI does not run it unless
``--live-probe`` is passed, and the plan prints its exact weight cost first, so
the person passing the flag knows what it will spend before it spends it. With
the flag absent the check reports ``未检出`` — never green, never red. Offline
tests drive :class:`HttpReachabilityProbe` through ``respx`` against
hand-built fixtures tagged ``source: documented`` (AGENTS §3.3: a real
recording is ``M1-G``'s job, from the production host's egress).
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

import httpx
import yaml

__all__ = [
    "GEO_REFUSAL_STATUS",
    "EndpointFamily",
    "HttpReachabilityProbe",
    "ProbeOutcome",
    "ReachabilityProbe",
    "probe_plan",
]

#: ``04`` §6: 451 is an **application-layer** geographic refusal, not a network
#: failure. The distinction decides the remedy — a 451 means "use the mirror
#: host", a timeout means "the link is down" — so it never collapses into a
#: generic "not reachable".
GEO_REFUSAL_STATUS: Final = 451

#: The cheapest liveness path on Binance USDⓈ-M futures (``04`` §2 weight
#: table: W=1). It is not in ``adapters/binance/endpoints.py`` because no
#: adapter method uses it; preflight is its only caller.
BINANCE_PING_PATH: Final = "/fapi/v1/ping"

#: Hyperliquid's cheapest useful read (``04`` §3: ``POST /info`` with
#: ``type=meta``, W=20). There is no ping; ``meta`` is the floor.
HYPERLIQUID_INFO_PATH: Final = "/info"

#: Four-part timeout, never a bare float (``03`` §3 测试规范).
TIMEOUT: Final = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


@dataclass(frozen=True, slots=True)
class EndpointFamily:
    """One host+path a region can accept or refuse, and what asking costs.

    ``04`` §6 reasons in *families* rather than URLs — ``fapi.binance.com``
    versus the mirror versus the data-dump host — because a region blocks a
    family, and the capability matrix's answer is per family too.
    """

    name: str
    venue: str
    url: str
    method: str
    bucket: str
    weight: int
    json_body: Mapping[str, Any] | None = None
    note: str = ""

    def describe(self) -> str:
        cost = f"{self.bucket} 记 {self.weight}" if self.weight else "不吃任何桶"
        return f"{self.name:<24} {self.method:<5} {self.url}  [{cost}]"


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """What one probe learned. ``status_code is None`` means it never answered."""

    family: EndpointFamily
    status_code: int | None
    latency_ms: int | None
    error_class: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300

    @property
    def geo_refused(self) -> bool:
        return self.status_code == GEO_REFUSAL_STATUS


class ReachabilityProbe(Protocol):
    """Anything that can answer "does this family respond from here"."""

    def probe(self, family: EndpointFamily) -> ProbeOutcome: ...


def probe_plan(venues_path: Path) -> tuple[EndpointFamily, ...]:
    """Build the probe list from ``config/venues.yaml``'s ``endpoints:`` block.

    No host is written here (AGENTS §2.3: 不许硬编码 `config/venues.yaml` 之外
    的主机). Preflight reads the same file the adapters read; it does not
    import them, and a host that is not in that file cannot be probed at all.
    """
    document = yaml.safe_load(venues_path.read_text(encoding="utf-8"))
    venues = document.get("venues") if isinstance(document, Mapping) else None
    if not isinstance(venues, Mapping):
        return ()

    families: list[EndpointFamily] = []
    binance = _endpoints(venues.get("binance"))
    if "rest" in binance:
        families.append(
            EndpointFamily(
                name="binance:fapi",
                venue="binance",
                url=binance["rest"] + BINANCE_PING_PATH,
                method="GET",
                bucket="binance:fapi_weight",
                weight=1,
                note="04 §6：美国出口下这一家返回 451，镜像返回 200",
            )
        )
    if "rest_mirror" in binance:
        families.append(
            EndpointFamily(
                name="binance:fapi_mirror",
                venue="binance",
                url=binance["rest_mirror"] + BINANCE_PING_PATH,
                method="GET",
                bucket="binance:fapi_weight",
                weight=1,
                note="04 §6：同路径镜像，两地都通；落美时唯一出路",
            )
        )
    hyperliquid = _endpoints(venues.get("hyperliquid"))
    if "rest" in hyperliquid:
        families.append(
            EndpointFamily(
                name="hyperliquid:info",
                venue="hyperliquid",
                url=hyperliquid["rest"] + HYPERLIQUID_INFO_PATH,
                method="POST",
                bucket="hyperliquid:info_weight",
                weight=20,
                json_body={"type": "meta"},
                note="04 §3：/info 没有 ping，meta 是最便宜的一次读",
            )
        )
    return tuple(families)


def _endpoints(node: Any) -> dict[str, str]:
    if not isinstance(node, Mapping):
        return {}
    endpoints = node.get("endpoints")
    if not isinstance(endpoints, Mapping):
        return {}
    out: dict[str, str] = {}
    for name, value in endpoints.items():
        if isinstance(name, str) and isinstance(value, Mapping):
            url = value.get("value")
            if isinstance(url, str):
                out[name] = url.rstrip("/")
    return out


def plan_cost(families: Sequence[EndpointFamily]) -> dict[str, int]:
    """What one full pass of the plan spends, per bucket. Printed before it runs."""
    cost: dict[str, int] = {}
    for family in families:
        cost[family.bucket] = cost.get(family.bucket, 0) + family.weight
    return cost


class HttpReachabilityProbe:
    """The real probe. Constructed only on the ``--live-probe`` path.

    No HTTP/2 (``03`` §3: 两所是否支持未验证，少一个不确定变量), the four-part
    timeout above, and one request per family — the point is the status code,
    not the payload, so nothing here parses a body.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=TIMEOUT, http2=False)

    def probe(self, family: EndpointFamily) -> ProbeOutcome:
        started = time.monotonic()
        try:
            response = self._client.request(
                family.method, family.url, json=family.json_body, timeout=TIMEOUT
            )
        except httpx.HTTPError as error:
            return ProbeOutcome(
                family=family,
                status_code=None,
                latency_ms=int((time.monotonic() - started) * 1000),
                error_class=type(error).__name__,
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        error_class = None if 200 <= response.status_code < 300 else f"http_{response.status_code}"
        return ProbeOutcome(
            family=family,
            status_code=response.status_code,
            latency_ms=latency_ms,
            error_class=error_class,
        )

    def close(self) -> None:
        self._client.close()
