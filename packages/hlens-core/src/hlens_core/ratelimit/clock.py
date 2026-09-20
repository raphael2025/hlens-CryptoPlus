"""The injected clock.

Everything in this package that has to know what time it is asks a
:class:`Clock`. Nothing in this package ever calls :func:`time.monotonic`,
:func:`time.time` or :func:`asyncio.sleep`.

Why this is not a detail
------------------------
The ledger's whole job is time-shaped: a one-minute rolling window, a
one-hour opportunistic freeze after a 429, an AIMD factor that recovers over
minutes, a five-minute WebSocket connection window, a 24-hour connection age,
and a `Retry-After` from a 418 that can be three days long. A limiter that
reads the process clock can only be tested by sleeping (slow) or by mocking the
standard library (flaky). With the clock injected, every one of those spans is
an integer addition, so the offline tests run the full state machine in
milliseconds and give the same answer every time.

Two readings, deliberately
--------------------------
``monotonic_ms`` measures spans: windows, freezes, backoffs. It never jumps.
``now_ms`` stamps events for the rest of the system and is UTC milliseconds,
the timestamp type seam ① requires. Wall time can step (NTP, a suspended
laptop); using it to measure a rolling window is how a limiter silently stops
limiting. The two must not be swapped.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "FakeClock", "SystemClock"]


@runtime_checkable
class Clock(Protocol):
    """The only source of time in this package."""

    def monotonic_ms(self) -> int:
        """Milliseconds from an arbitrary, never-decreasing origin."""

    def now_ms(self) -> int:
        """UTC milliseconds — the timestamp seam ① puts on records."""


class SystemClock:
    """The production clock. The only place the standard library is read."""

    __slots__ = ()

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000

    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000


class FakeClock:
    """A clock that only moves when a test moves it.

    Both readings advance together, so a test that advances an hour advances
    an hour for the rolling window and for the event timestamps alike, while
    keeping the two conceptually distinct: ``now_ms`` starts at a real UTC
    instant, ``monotonic_ms`` at an arbitrary origin that is deliberately not
    the same number, so code that confuses them fails the tests.
    """

    #: 2026-01-01T00:00:00Z, so event timestamps in test output are readable.
    DEFAULT_EPOCH_MS = 1_767_225_600_000

    #: Deliberately unrelated to the epoch: swapping the two readings breaks.
    DEFAULT_MONOTONIC_ORIGIN_MS = 5_000

    def __init__(
        self,
        *,
        epoch_ms: int = DEFAULT_EPOCH_MS,
        monotonic_origin_ms: int = DEFAULT_MONOTONIC_ORIGIN_MS,
    ) -> None:
        self._epoch_ms = epoch_ms
        self._monotonic_origin_ms = monotonic_origin_ms
        self._elapsed_ms = 0

    def monotonic_ms(self) -> int:
        return self._monotonic_origin_ms + self._elapsed_ms

    def now_ms(self) -> int:
        return self._epoch_ms + self._elapsed_ms

    def advance_ms(self, milliseconds: int) -> None:
        if milliseconds < 0:
            raise ValueError("a clock does not go backwards")
        self._elapsed_ms += milliseconds

    def advance_s(self, seconds: float) -> None:
        self.advance_ms(round(seconds * 1000))

    def advance_min(self, minutes: float) -> None:
        self.advance_s(minutes * 60)
