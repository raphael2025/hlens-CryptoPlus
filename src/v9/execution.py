"""15m execution triggers: break of structure of 15m directional-change swings."""

import polars as pl

from v9.config import Config
from v9.swings import atr, directional_change


def triggers_15m(bars15: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    hi, lo, cl = (bars15[c].to_numpy() for c in ("high", "low", "close"))
    sw = directional_change(hi, lo, cl, atr(hi, lo, cl, cfg.execution.atr_bars), cfg.execution.dc_atr_mult)
    return pl.DataFrame({"visible_at": bars15["close_time"], "bos_up": sw.bos_up, "bos_down": sw.bos_down})
