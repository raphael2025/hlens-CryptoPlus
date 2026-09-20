"""Burst shaping: spread a window's requests evenly across the window.

§6, last bullet: "10 分钟车道的 540 次请求必须匀速摊到窗口内（54 次/分），不许
在窗口首秒打完". Firing all 540 at the top of the window is inside the
per-minute budget only if you average over ten minutes; the exchange averages
over one, so the first minute would be 540 requests against a ceiling of 80 and
the reply is a 429 — on a shared egress IP, followed by a 418 that bans the
whole host.

The shaper is pure arithmetic over the injected clock: release ``k`` is due at
``k x window / total``, so the count available at any instant is an integer
division. No timers, no sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .clock import Clock
from .config import BucketKey

__all__ = ["BurstShaper", "PacedLane"]


@dataclass(frozen=True, slots=True)
class BurstShaper:
    """``total`` requests spread evenly over ``window_ms``."""

    total: int
    window_ms: int

    def __post_init__(self) -> None:
        if self.total <= 0 or self.window_ms <= 0:
            raise ValueError("a shaped lane needs a positive size and a positive window")

    def due_by(self, elapsed_ms: int) -> int:
        """How many releases are available ``elapsed_ms`` into the window.

        One is available immediately (``due_by(0) == 1``) — the lane starts on
        time, it just does not finish early.
        """
        if elapsed_ms < 0:
            return 0
        return min(self.total, elapsed_ms * self.total // self.window_ms + 1)

    def release_at_ms(self, index: int) -> int:
        """When release ``index`` (0-based) becomes available."""
        if not 0 <= index < self.total:
            raise IndexError(f"release {index} is outside 0..{self.total - 1}")
        return index * self.window_ms // self.total

    @property
    def per_min(self) -> Fraction:
        """The shaped rate — 540 over 10 minutes is 54/min."""
        return Fraction(self.total * 60_000, self.window_ms)


class PacedLane:
    """A repeating shaped window, e.g. the 10-minute long/short ratio lane."""

    __slots__ = ("_clock", "_released", "_started_ms", "key", "name", "shaper")

    def __init__(
        self, name: str, key: BucketKey, shaper: BurstShaper, *, clock: Clock
    ) -> None:
        self.name = name
        self.key = key
        self.shaper = shaper
        self._clock = clock
        self._started_ms: int | None = None
        self._released = 0

    @property
    def released_in_window(self) -> int:
        return self._released

    def _roll(self, now_ms: int) -> int:
        """Advance to the current window and return the elapsed time inside it."""
        if self._started_ms is None:
            self._started_ms = now_ms
        elapsed = now_ms - self._started_ms
        if elapsed >= self.shaper.window_ms:
            windows = elapsed // self.shaper.window_ms
            self._started_ms += windows * self.shaper.window_ms
            self._released = 0
            elapsed -= windows * self.shaper.window_ms
        return elapsed

    def ready(self, now_ms: int) -> bool:
        # Roll first, then read the counter: `self._released < due_by(_roll())`
        # evaluates the left operand before the roll resets it, so a lane whose
        # window had just turned over compared the OLD count against the new
        # window and refused the first request of every window after the first.
        elapsed = self._roll(now_ms)
        return self._released < self.shaper.due_by(elapsed)

    def wait_ms(self, now_ms: int) -> int:
        """How long until the next release. 0 when one is available now."""
        elapsed = self._roll(now_ms)
        if self._released < self.shaper.due_by(elapsed):
            return 0
        if self._released >= self.shaper.total:
            return self.shaper.window_ms - elapsed
        return max(1, self.shaper.release_at_ms(self._released) - elapsed)

    def take(self, now_ms: int) -> bool:
        if not self.ready(now_ms):
            return False
        self._released += 1
        return True
