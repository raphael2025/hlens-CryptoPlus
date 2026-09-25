"""Entry quality, independent of exits (research plan §7). These metrics look forward by design:
they measure what happened after each entry, they are never inputs to a signal."""

import numpy as np
import polars as pl

BARS_PER_HOUR = 4


def forward_metrics(o, h, lo, c, entry_bar: int, d: int, risk: float, atr_1h: float,
                    horizons_h, passage_r, window_h: int) -> dict | None:
    """Entry at open[entry_bar]. `risk` is the price distance of the natural stop (1R)."""
    n_win = window_h * BARS_PER_HOUR
    end = entry_bar + n_win
    if end > c.size or risk <= 0:
        return None
    e = o[entry_bar]
    hh, ll = h[entry_bar:end], lo[entry_bar:end]
    fav = (hh - e) if d == 1 else (e - ll)
    adv = (e - ll) if d == 1 else (hh - e)
    out = {"mfe_r": float(fav.max() / risk), "mae_r": float(adv.max() / risk)}
    for hz in horizons_h:
        out[f"ret_{hz}h_atr"] = float(d * (c[entry_bar + hz * BARS_PER_HOUR - 1] - e) / atr_1h)
    stop_hit = np.flatnonzero(adv >= risk)
    first_stop = stop_hit[0] if stop_hit.size else np.inf
    for k in passage_r:
        tgt = np.flatnonzero(fav >= k * risk)
        first_tgt = tgt[0] if tgt.size else np.inf
        # a bar that touches both counts as the stop (conservative)
        out[f"win_{k}r"] = bool(first_tgt < first_stop)
        out[f"loss_{k}r"] = bool(first_stop <= first_tgt and np.isfinite(first_stop))
    return out


def diff_ci(p1: float, n1: int, p2: float, n2: int, z: float = 1.96):
    se = np.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    return p1 - p2, p1 - p2 - z * se, p1 - p2 + z * se


def measure(rows, o, h, lo, c, study) -> pl.DataFrame:
    """rows: (signal_bar, dir, risk_price, atr_1h); entry is the open after the signal bar."""
    out = []
    for bar, d, risk, a1 in rows:
        m = forward_metrics(o, h, lo, c, bar + 1, d, risk, a1,
                            study.horizons_h, study.passage_r, study.passage_window_h)
        if m is not None:
            out.append({"bar": bar, "dir": d, "risk_atr": risk / a1, **m})
    return pl.DataFrame(out)
