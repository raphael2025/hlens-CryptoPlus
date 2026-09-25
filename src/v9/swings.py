"""Directional-change swing points and break of structure, computed bar by bar (no look-ahead).

A running extreme becomes a confirmed swing only when price retraces `mult * ATR%` from it;
the swing is usable from that confirmation bar on, never earlier.
"""

from dataclasses import dataclass

import numpy as np


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    prev = np.concatenate([[np.nan], close[:-1]])
    tr = np.nanmax(np.vstack([high - low, np.abs(high - prev), np.abs(low - prev)]), axis=0)
    out = np.full(close.size, np.nan)
    if close.size >= n:
        c = np.cumsum(tr)
        out[n - 1:] = (c[n - 1:] - np.concatenate([[0.0], c[:-n]])) / n
    return out


@dataclass
class Swings:
    mode: np.ndarray        # +1 tracking a high (up leg), -1 tracking a low (down leg)
    ext: np.ndarray         # running extreme of the current leg
    ext_idx: np.ndarray
    sh: np.ndarray          # last confirmed swing high price (nan before the first)
    sh_idx: np.ndarray      # bar index of that high's extreme
    sl: np.ndarray
    sl_idx: np.ndarray
    bos_up: np.ndarray      # close broke the last confirmed swing high on this bar
    bos_down: np.ndarray
    trend: np.ndarray       # direction of the latest BOS (0 before any)
    stage: np.ndarray       # consecutive same-direction BOS count; a CHOCH restarts at 1


def directional_change(high, low, close, atr_values, mult: float) -> Swings:
    n = close.size
    mode = np.zeros(n, np.int8)
    ext = np.full(n, np.nan)
    ext_idx = np.zeros(n, np.int64)
    sh = np.full(n, np.nan)
    sh_idx = np.full(n, -1, np.int64)
    sl = np.full(n, np.nan)
    sl_idx = np.full(n, -1, np.int64)
    bos_up = np.zeros(n, bool)
    bos_down = np.zeros(n, bool)
    trend = np.zeros(n, np.int8)
    stage = np.zeros(n, np.int32)

    m, eh, ehi, el, eli = 1, high[0], 0, low[0], 0
    csh, cshi, csl, csli = np.nan, -1, np.nan, -1
    broke_h = broke_l = True
    t = s = 0
    for i in range(n):
        th = mult * atr_values[i] / close[i] if np.isfinite(atr_values[i]) else np.inf
        if m == 1:
            if high[i] > eh:
                eh, ehi = high[i], i
            if low[i] <= eh * (1 - th):
                csh, cshi, broke_h = eh, ehi, False
                m, el, eli = -1, low[i], i
        else:
            if low[i] < el:
                el, eli = low[i], i
            if high[i] >= el * (1 + th):
                csl, csli, broke_l = el, eli, False
                m, eh, ehi = 1, high[i], i
        if not broke_h and close[i] > csh:
            broke_h, bos_up[i] = True, True
            s, t = (s + 1 if t == 1 else 1), 1
        if not broke_l and close[i] < csl:
            broke_l, bos_down[i] = True, True
            s, t = (s + 1 if t == -1 else 1), -1
        mode[i] = m
        ext[i], ext_idx[i] = (eh, ehi) if m == 1 else (el, eli)
        sh[i], sh_idx[i], sl[i], sl_idx[i] = csh, cshi, csl, csli
        trend[i], stage[i] = t, s
    return Swings(mode, ext, ext_idx, sh, sh_idx, sl, sl_idx, bos_up, bos_down, trend, stage)
