"""Structured adapter errors. Every one carries enough context to triage from a log line."""

from __future__ import annotations


class AdapterError(Exception):
    """Base class. ``retryable`` is the scheduler's contract, not a suggestion."""

    retryable = False

    def __init__(
        self,
        message: str,
        *,
        venue: str = "",
        endpoint: str = "",
        symbol: str | None = None,
        status: int | None = None,
        attempt: int = 0,
        request_id: str | None = None,
        body_excerpt: str | None = None,
    ) -> None:
        super().__init__(message)
        self.venue = venue
        self.endpoint = endpoint
        self.symbol = symbol
        self.status = status
        self.attempt = attempt
        self.request_id = request_id
        self.body_excerpt = body_excerpt

    def __str__(self) -> str:
        bits = [super().__str__()]
        for k in ("venue", "endpoint", "symbol", "status", "attempt"):
            v = getattr(self, k)
            if v:
                bits.append(f"{k}={v}")
        return " ".join(bits)


class TransportError(AdapterError):
    """Timeout, connection reset, DNS. Idempotent GETs may be retried."""

    retryable = True


class ServerError(AdapterError):
    """5xx. Short backoff, then circuit-break."""

    retryable = True


class RateLimited(AdapterError):
    """429/418/OKX 50011. Retry only after ``retry_after_s``; never tighten the loop."""

    retryable = True

    def __init__(self, *args, retry_after_s: float | None = None, kind: str = "weight", **kw):
        super().__init__(*args, **kw)
        self.retry_after_s = retry_after_s
        self.kind = kind


class Banned(RateLimited):
    """HTTP 418: the venue banned this egress IP for ``Retry-After`` (2 min .. 3 days).

    Not retryable, and deliberately a subclass of :class:`RateLimited` so existing
    ``except RateLimited`` handlers still see it -- but any retry loop must let it
    through. Sitting in a coroutine sleeping off three days is not a retry, and every
    other collector behind the same IP is banned too: the wait belongs to the shared
    :class:`~hlens_core.ratelimit.Budget`, which holds it as a hard block.
    """

    retryable = False


class GeoBlocked(AdapterError):
    """451/403 from a policy block. Switch host or drop the venue; do not hammer it."""


class InvalidSymbol(AdapterError):
    """The venue does not know this symbol. Mark the instrument as a delist candidate."""


class SchemaError(AdapterError):
    """The payload did not have the shape we parse. Keep the raw body; do not retry blind."""


class CircuitOpen(AdapterError):
    """Too many consecutive failures; this endpoint is parked until the breaker closes."""

    retryable = True
