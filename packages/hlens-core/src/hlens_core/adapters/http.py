"""Shared async HTTP transport: budget, retries, Retry-After, circuit breaker, host failover."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..ratelimit import Budget, RateLimitKind, classify_rate_limit
from .errors import (
    Banned,
    CircuitOpen,
    GeoBlocked,
    RateLimited,
    SchemaError,
    ServerError,
    TransportError,
)

#: Exponential backoff, capped. 1 -> 2 -> 4 ... -> 60 s.
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 60.0
DEFAULT_RETRIES = 4
#: Circuit breaker: N consecutive failures parks the key for COOLDOWN seconds.
BREAKER_FAILS = 5
BREAKER_COOLDOWN_S = 300.0
#: Geo/policy blocks. 451 is unambiguous; 403 is shared with Bybit's rate limiter and
#: is only treated as a geo block when the body does not look like a rate-limit message.
GEO_BLOCK_STATUS = (451, 403)


def backoff_delay(attempt: int) -> float:
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2**attempt))


@dataclass(slots=True)
class HttpResult:
    status: int
    json: Any
    headers: dict[str, str]
    latency_ms: float
    url: str
    base: str
    request_id: str
    body: str = ""


@dataclass(slots=True)
class _Breaker:
    fails: int = 0
    open_until: float = 0.0


@dataclass(slots=True)
class BreakerRegistry:
    """One breaker per (host, capability) -- never one per venue.

    A broken long/short-ratio endpoint must not take price and funding down with it
    (ADAPTER-RESEARCH §6.3).
    """

    cooldown_s: float = BREAKER_COOLDOWN_S
    threshold: int = BREAKER_FAILS
    _state: dict[str, _Breaker] = field(default_factory=dict)

    def check(self, key: str, now: float) -> float:
        b = self._state.get(key)
        if b and b.open_until > now:
            return b.open_until - now
        return 0.0

    def record_failure(self, key: str, now: float) -> None:
        b = self._state.setdefault(key, _Breaker())
        b.fails += 1
        if b.fails >= self.threshold:
            b.open_until = now + self.cooldown_s

    def record_success(self, key: str) -> None:
        self._state.pop(key, None)

    def open_keys(self, now: float) -> list[str]:
        return [k for k, b in self._state.items() if b.open_until > now]


class VenueHttp:
    """One venue's HTTP client.

    * ``bases`` is a failover list -- Binance's ``fapi.binance.com`` first, the
      ``www.binance.com`` mirror behind it, because the mirror is what answers from a
      US egress where the primary returns 451.
    * Every request goes through the venue :class:`Budget` before it is sent, and the
      response's rate-limit header is fed back into the ledger.
    """

    def __init__(
        self,
        venue: str,
        bases: list[str],
        *,
        budget: Budget | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        retries: int = DEFAULT_RETRIES,
        used_weight_header: str | None = None,
        breakers: BreakerRegistry | None = None,
        clock=time.monotonic,
        sleep=asyncio.sleep,
    ) -> None:
        if not bases:
            raise ValueError("at least one base URL is required")
        self.venue = venue
        self.bases = list(bases)
        self.budget = budget
        self.timeout_s = timeout_s
        self.retries = retries
        self.used_weight_header = used_weight_header
        self.breakers = breakers or BreakerRegistry()
        self._clock = clock
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"user-agent": "hlens-core/0.1 (+research)"}
        )
        self.blocked_bases: set[str] = set()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> VenueHttp:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- core ---------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        weight: int | None = None,
        endpoint: str | None = None,
        capability: str | None = None,
    ) -> HttpResult:
        """Send one request, retrying per the policy. Raises an :mod:`.errors` type."""
        endpoint = endpoint or path
        breaker_key = f"{self.venue}:{capability or endpoint}"
        now = self._clock()
        parked = self.breakers.check(breaker_key, now)
        if parked:
            raise CircuitOpen(
                f"circuit open for another {parked:.0f}s",
                venue=self.venue,
                endpoint=endpoint,
            )

        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            base = self._pick_base()
            try:
                result = await self._attempt(
                    method, base, path, params, json_body, weight, endpoint, attempt
                )
            except GeoBlocked as exc:
                self.blocked_bases.add(base)
                last_exc = exc
                if set(self.bases) <= self.blocked_bases:
                    self.breakers.record_failure(breaker_key, self._clock())
                    raise
                continue  # try the next host immediately; backoff would not help
            except Banned:
                # A ban is not a slow rate limit: retrying is what turns a 2-minute ban
                # into a 3-day one. The budget already holds the hard block.
                self.breakers.record_failure(breaker_key, self._clock())
                raise
            except RateLimited as exc:
                last_exc = exc
                if attempt >= self.retries:
                    break
                # Never sleep longer than the backoff ceiling on a single attempt: a
                # venue may hand back a Retry-After measured in hours, and a coroutine
                # parked for hours is a hung collector, not a polite one. The budget's
                # hard block is what actually keeps us off the wire until then.
                delay = exc.retry_after_s or backoff_delay(attempt)
                await self._sleep(min(delay, BACKOFF_MAX_S))
                continue
            except (TransportError, ServerError) as exc:
                last_exc = exc
                self.breakers.record_failure(breaker_key, self._clock())
                if attempt >= self.retries:
                    break
                await self._sleep(backoff_delay(attempt))
                continue
            self.breakers.record_success(breaker_key)
            return result
        self.breakers.record_failure(breaker_key, self._clock())
        assert last_exc is not None
        raise last_exc

    async def get(self, path: str, **kw: Any) -> Any:
        """GET and return the decoded JSON body."""
        return (await self.request("GET", path, **kw)).json

    async def post(self, path: str, json_body: Any, **kw: Any) -> Any:
        return (await self.request("POST", path, json_body=json_body, **kw)).json

    # -- internals ----------------------------------------------------------

    def _pick_base(self) -> str:
        for b in self.bases:
            if b not in self.blocked_bases:
                return b
        return self.bases[0]

    async def _attempt(
        self,
        method: str,
        base: str,
        path: str,
        params: dict[str, Any] | None,
        json_body: Any,
        weight: int | None,
        endpoint: str,
        attempt: int,
    ) -> HttpResult:
        url = base + path
        request_id = uuid.uuid4().hex[:12]
        started = self._clock()

        async def send() -> httpx.Response:
            return await self._client.request(
                method, url, params=params, json=json_body, timeout=self.timeout_s
            )

        if self.budget is not None:
            async with self.budget.call(endpoint, weight) as reservation:
                resp, err = await self._send(send, url, endpoint, attempt, request_id)
                if err is not None:
                    raise err
                assert resp is not None
                self._sync_budget(resp)
                _ = reservation  # settled at the reserved amount by the context manager
        else:
            resp, err = await self._send(send, url, endpoint, attempt, request_id)
            if err is not None:
                raise err
            assert resp is not None

        latency_ms = (self._clock() - started) * 1000
        self._raise_for_status(resp, endpoint, attempt, request_id)
        try:
            payload = resp.json() if resp.content else None
        except ValueError as exc:
            raise SchemaError(
                f"response was not JSON: {exc}",
                venue=self.venue,
                endpoint=endpoint,
                status=resp.status_code,
                request_id=request_id,
                body_excerpt=resp.text[:200],
            ) from exc
        return HttpResult(
            status=resp.status_code,
            json=payload,
            headers={k.lower(): v for k, v in resp.headers.items()},
            latency_ms=latency_ms,
            url=url,
            base=base,
            request_id=request_id,
            body=resp.text[:512],
        )

    async def _send(
        self, send, url: str, endpoint: str, attempt: int, request_id: str
    ) -> tuple[httpx.Response | None, Exception | None]:
        try:
            return await send(), None
        except httpx.TimeoutException:
            return None, TransportError(
                f"timeout after {self.timeout_s}s",
                venue=self.venue,
                endpoint=endpoint,
                attempt=attempt,
                request_id=request_id,
            )
        except httpx.HTTPError as exc:
            return None, TransportError(
                f"{type(exc).__name__}: {exc}",
                venue=self.venue,
                endpoint=endpoint,
                attempt=attempt,
                request_id=request_id,
            )

    def _sync_budget(self, resp: httpx.Response) -> None:
        if self.budget is None:
            return
        if self.used_weight_header:
            raw = resp.headers.get(self.used_weight_header)
            if raw:
                with contextlib.suppress(ValueError):
                    self.budget.sync_used(float(raw))
        remain = resp.headers.get("x-gate-ratelimit-requests-remain")
        limit = resp.headers.get("x-gate-ratelimit-limit")
        if remain and limit:
            with contextlib.suppress(ValueError):
                self.budget.sync_remaining(float(remain), float(limit))

    def _raise_for_status(
        self, resp: httpx.Response, endpoint: str, attempt: int, request_id: str
    ) -> None:
        status = resp.status_code
        if status < 400:
            return
        body = resp.text[:512]
        kind = classify_rate_limit(status, body, venue=self.venue)
        if status in (429, 418) or kind is RateLimitKind.weight:
            retry_after = _retry_after(resp)
            if self.budget is not None:
                self.budget.on_rate_limited(kind, retry_after_s=retry_after)
            error = Banned if kind is RateLimitKind.ban else RateLimited
            raise error(
                "IP banned" if kind is RateLimitKind.ban else f"rate limited ({kind.value})",
                venue=self.venue,
                endpoint=endpoint,
                status=status,
                attempt=attempt,
                request_id=request_id,
                retry_after_s=retry_after,
                kind=kind.value,
                body_excerpt=body,
            )
        if kind is RateLimitKind.connection and self.budget is not None:
            self.budget.on_rate_limited(kind)
        if status in GEO_BLOCK_STATUS:
            raise GeoBlocked(
                f"policy/geo block (HTTP {status})",
                venue=self.venue,
                endpoint=endpoint,
                status=status,
                attempt=attempt,
                request_id=request_id,
                body_excerpt=body,
            )
        if status >= 500:
            raise ServerError(
                f"HTTP {status}",
                venue=self.venue,
                endpoint=endpoint,
                status=status,
                attempt=attempt,
                request_id=request_id,
                body_excerpt=body,
            )
        raise SchemaError(
            f"HTTP {status}",
            venue=self.venue,
            endpoint=endpoint,
            status=status,
            attempt=attempt,
            request_id=request_id,
            body_excerpt=body,
        )


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
