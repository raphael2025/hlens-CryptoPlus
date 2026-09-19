"""The rate-limit ledger — accounted per public egress IP, per venue.

``docs/03-ARCHITECTURE.md`` §6 and §6.1. What this package is for, in one
paragraph: the production host shares one public egress IP with an older
collector that is still running, the exchanges meter per IP, and neither
consumer can see the other's usage. So the budget is split statically —
``official limit x share - sum(reserved)`` — the reservations live in
``config/egress-consumers.yaml``, and every lane asks this ledger before it
spends anything.

What it does
------------
* Two kinds of bucket that never share an account: **weight** (Binance
  ``/fapi/*``, Hyperliquid ``/info``) and **request count**
  (``/futures/data/*``, which charges no weight and has its own limit).
* **Three** priority tiers: fast lane > every other resident lane >
  opportunistic, with the fast lane's floor fenced off from the rest at all
  times.
* Burst shaping, so a ten-minute lane's 540 requests are spread across the
  window instead of fired in its first second.
* AIMD on a 429 (opportunistic stops for an hour, resident is slowed to 75 %),
  and on a 418 a full stop of every lane of that venue.
* WebSocket connection-rate, rotation-age and seat accounting.

What it does not do
-------------------
It sends no requests, writes no tables, and reads no clock of its own. It
answers "may I spend this" and is told afterwards what was spent and what came
back; the events it produces are turned into ``ingest_gap`` and ``ops_event``
rows by the collector. Seam ③: nothing here imports another module.
"""

from __future__ import annotations

from .buckets import WINDOW_MS, Bucket, DenyReason, Grant, Priority
from .clock import Clock, FakeClock, SystemClock
from .config import (
    AimdPolicy,
    BucketKey,
    BucketKind,
    BudgetProfile,
    CapacityModel,
    CoinHeadroom,
    ConfigError,
    Constant,
    EgressConsumers,
    Flag,
    LedgerConfig,
    Reservation,
    ResolvedBucket,
    SeatReservation,
    SourceTag,
    VenuesConfig,
    WsSpec,
)
from .events import LEDGER_CAUSES, EventKind, GapCause, HlBodyKind, LedgerEvent
from .ledger import (
    MAX_DOCUMENTED_BAN_S,
    BucketSnapshot,
    RateLimitLedger,
    classify_hl_429_body,
)
from .pacing import BurstShaper, PacedLane
from .ws import WsDecision, WsLedger

__all__ = [
    "LEDGER_CAUSES",
    "MAX_DOCUMENTED_BAN_S",
    "WINDOW_MS",
    "AimdPolicy",
    "Bucket",
    "BucketKey",
    "BucketKind",
    "BucketSnapshot",
    "BudgetProfile",
    "BurstShaper",
    "CapacityModel",
    "Clock",
    "CoinHeadroom",
    "ConfigError",
    "Constant",
    "DenyReason",
    "EgressConsumers",
    "EventKind",
    "FakeClock",
    "Flag",
    "GapCause",
    "Grant",
    "HlBodyKind",
    "LedgerConfig",
    "LedgerEvent",
    "PacedLane",
    "Priority",
    "RateLimitLedger",
    "Reservation",
    "ResolvedBucket",
    "SeatReservation",
    "SourceTag",
    "SystemClock",
    "VenuesConfig",
    "WsDecision",
    "WsLedger",
    "WsSpec",
    "classify_hl_429_body",
]
