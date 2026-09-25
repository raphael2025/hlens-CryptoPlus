"""1H trade state (research plan §2, D6).

Setup (long): 1H trend is up and price is in the correction after the latest confirmed swing
high (DC down leg). The thesis is identified by that swing high (`leg_id`); the correction
low so far is `ext`. Short is symmetric. `stage`, `q` and `depth` feed hypotheses H9 / H10
and are not used by the core.
"""

import numpy as np
import polars as pl

from v9.bars import resample
from v9.config import Config
from v9.swings import atr, directional_change


def structure_1h(bars15: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    h1 = resample(bars15, "1h")
    hi, lo, cl = (h1[c].to_numpy() for c in ("high", "low", "close"))
    a = atr(hi, lo, cl, cfg.structure.atr_bars)
    sw = directional_change(hi, lo, cl, a, cfg.structure.dc_atr_mult)
    idx = np.arange(cl.size)

    long_ok = (sw.trend == 1) & (sw.mode == -1) & (sw.sl_idx >= 0) & (sw.sl_idx < sw.sh_idx)
    short_ok = (sw.trend == -1) & (sw.mode == 1) & (sw.sh_idx >= 0) & (sw.sh_idx < sw.sl_idx)
    setup = np.where(long_ok, 1, np.where(short_ok, -1, 0)).astype(np.int8)

    impulse = sw.sh - sw.sl
    correction = np.where(setup == 1, sw.sh - sw.ext, np.where(setup == -1, sw.ext - sw.sl, np.nan))
    imp_bars = np.abs(sw.sh_idx - sw.sl_idx)
    anchor = np.where(setup == 1, sw.sh_idx, np.where(setup == -1, sw.sl_idx, -1))
    corr_bars = np.maximum(idx - anchor, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = (impulse / np.maximum(imp_bars, 1)) / (correction / corr_bars)
        depth = correction / impulse
    q = np.where(setup != 0, q, np.nan)
    depth = np.where(setup != 0, depth, np.nan)

    leg_start = h1["open_time"].gather(np.clip(anchor, 0, None)).alias("leg_start")
    return pl.DataFrame({
        "visible_at": h1["close_time"],
        "close_1h": cl,
        "atr_1h": a,
        "trend": sw.trend,
        "stage": sw.stage,
        "setup": setup,
        "leg_id": anchor,
        "leg_start": leg_start,
        "ext": sw.ext,
        "swing_high": sw.sh,
        "swing_low": sw.sl,
        "swing_high_idx": sw.sh_idx,
        "swing_low_idx": sw.sl_idx,
        "q": q,
        "depth": depth,
    }).with_columns(pl.when(pl.col("setup") != 0).then(pl.col("leg_start")).alias("leg_start"))
