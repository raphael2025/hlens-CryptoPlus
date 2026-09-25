"""Truncation-invariance check (AGENTS.md §2.1).

Every signal function takes a bar frame (with `close_time`) and returns a frame with a
`visible_at` column: the first moment a row may be used. A function has no look-ahead
iff, for every cut time t, the rows visible by t are identical whether it was fed the
full history or only the bars closed by t.
"""

from collections.abc import Callable, Iterable
from datetime import datetime

import polars as pl

SignalFn = Callable[[pl.DataFrame], pl.DataFrame]


def truncation_diffs(fn: SignalFn, bars: pl.DataFrame, cuts: Iterable[datetime]) -> list[str]:
    full = fn(bars)
    problems = []
    for t in cuts:
        part = fn(bars.filter(pl.col("close_time") <= t))
        a = full.filter(pl.col("visible_at") <= t)
        b = part.filter(pl.col("visible_at") <= t)
        if a.height != b.height:
            problems.append(f"cut {t}: {a.height} visible rows on full data, {b.height} on truncated")
            continue
        if not a.equals(b, null_equal=True):
            for col in a.columns:
                if not a[col].equals(b[col], null_equal=True):
                    problems.append(f"cut {t}: column {col!r} differs")
    return problems


def assert_truncation_invariant(fn: SignalFn, bars: pl.DataFrame, cuts: Iterable[datetime]) -> None:
    problems = truncation_diffs(fn, bars, cuts)
    if problems:
        raise AssertionError("look-ahead detected:\n" + "\n".join(problems))


def evenly_spaced_cuts(bars: pl.DataFrame, n: int = 7) -> list[datetime]:
    times = bars["close_time"]
    step = max(1, times.len() // (n + 1))
    return [times[i] for i in range(step, times.len() - 1, step)][:n]
