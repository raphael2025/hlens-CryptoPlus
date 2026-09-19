"""WebSocket accounting: connection rate, forced rotation, and seats.

Two different scarcities live here.

*Binance* meters **new connections**: <= 300 per IP per 5 minutes, and every
connection is force-closed at 24 h (04 §2, carried over from the pre-split
documentation and not re-verified since — accounted at those numbers because
they are the conservative reading). §6: "重连风暴要记账并退避" — a reconnect
storm that is not accounted is how a WebSocket outage turns into an IP-level
rate-limit incident that also kills the collector sharing this egress.

*Hyperliquid* meters **seats**: connections, subscriptions and distinct users
are zero-sum across the whole egress IP (04 §4), and the user seats in
particular cannot be divided — whatever the legacy collector holds, we do not.
How many it holds is unknown; ``config/egress-consumers.yaml`` says so with a
``null`` rather than a 0, and that unknown is carried through to preflight.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .buckets import DenyReason
from .config import WsSpec

__all__ = ["WsDecision", "WsLedger"]


@dataclass(frozen=True, slots=True)
class WsDecision:
    granted: bool
    reason: DenyReason | None = None
    retry_after_ms: int = 0

    def __bool__(self) -> bool:
        return self.granted


@dataclass(slots=True)
class _Connection:
    opened_ms: int
    streams: int = 1


@dataclass(slots=True)
class WsLedger:
    """One venue's WebSocket accounting. Opens no sockets."""

    spec: WsSpec
    _opened_at: deque[int] = field(default_factory=deque, repr=False)
    _connections: dict[str, _Connection] = field(default_factory=dict, repr=False)
    _subscriptions: int = 0
    _users: set[str] = field(default_factory=set, repr=False)

    # ----------------------------------------------------------------- #
    # Connections
    # ----------------------------------------------------------------- #
    @property
    def open_connections(self) -> int:
        return len(self._connections)

    @property
    def subscriptions(self) -> int:
        return self._subscriptions

    @property
    def distinct_users(self) -> int:
        return len(self._users)

    def _prune(self, now_ms: int) -> None:
        window_ms = (self.spec.window_s or 0) * 1000
        if not window_ms:
            self._opened_at.clear()
            return
        cutoff = now_ms - window_ms
        while self._opened_at and self._opened_at[0] <= cutoff:
            self._opened_at.popleft()

    def connections_in_window(self, now_ms: int) -> int:
        self._prune(now_ms)
        return len(self._opened_at)

    def open(self, connection_id: str, now_ms: int, *, streams: int = 1) -> WsDecision:
        if connection_id in self._connections:
            raise ValueError(f"connection {connection_id!r} is already open")
        if streams < 1:
            raise ValueError("a connection carries at least one stream")
        self._prune(now_ms)

        limit = self.spec.max_new_connections_per_window
        if limit is not None and len(self._opened_at) >= limit:
            window_ms = (self.spec.window_s or 0) * 1000
            retry = max(1, self._opened_at[0] + window_ms - now_ms + 1)
            return WsDecision(False, DenyReason.WS_CONNECTION_RATE, retry)

        if self.spec.max_connections is not None and (
            len(self._connections) >= self.spec.max_connections
        ):
            return WsDecision(False, DenyReason.WS_CONNECTION_SEATS)

        if self.spec.max_streams_per_connection is not None and (
            streams > self.spec.max_streams_per_connection
        ):
            return WsDecision(False, DenyReason.WS_STREAMS_PER_CONNECTION)

        if self.spec.max_subscriptions is not None and (
            self._subscriptions + streams > self.spec.max_subscriptions
        ):
            return WsDecision(False, DenyReason.WS_SUBSCRIPTION_SEATS)

        self._opened_at.append(now_ms)
        self._connections[connection_id] = _Connection(opened_ms=now_ms, streams=streams)
        self._subscriptions += streams
        return WsDecision(True)

    def close(self, connection_id: str) -> None:
        connection = self._connections.pop(connection_id, None)
        if connection is None:
            return
        self._subscriptions -= connection.streams

    def due_for_rotation(self, now_ms: int) -> tuple[str, ...]:
        """Connections at or past the venue's forced-disconnect age.

        Rotating one costs a slot in the connection-rate window, so the caller
        has to ask :meth:`open` for the replacement like any other connection —
        that is precisely the accounting a reconnect storm evades.
        """
        max_age_s = self.spec.max_connection_age_s
        if max_age_s is None:
            return ()
        cutoff = now_ms - max_age_s * 1000
        return tuple(
            connection_id
            for connection_id, connection in sorted(self._connections.items())
            if connection.opened_ms <= cutoff
        )

    # ----------------------------------------------------------------- #
    # Seats
    # ----------------------------------------------------------------- #
    def claim_user(self, user: str) -> WsDecision:
        """A distinct-user seat (M5 wallet work; the limit exists from day one)."""
        if user in self._users:
            return WsDecision(True)
        if self.spec.max_distinct_users is not None and (
            len(self._users) >= self.spec.max_distinct_users
        ):
            return WsDecision(False, DenyReason.WS_USER_SEATS)
        self._users.add(user)
        return WsDecision(True)

    def release_user(self, user: str) -> None:
        self._users.discard(user)
