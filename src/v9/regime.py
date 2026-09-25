"""4H market environment (research plan §2, D3).

Slow/fast momentum phases after Goulding, Harvey & Mazzoleni: S and F are the signs of the
log return over `slow_bars` and `fast_bars` 4H bars. Precedence:
  CHOP        Kaufman efficiency ratio below its trailing quantile        -> NO_TRADE
  TRANSITION  S changed sign within the last `transition_bars` (or S = 0) -> WAIT
  TREND       S == F                                                       -> trade in direction S
  PULLBACK    S != F                                                       -> trade in direction S
Rows during indicator warm-up get state WARMUP. Every row is visible at its 4H close.
"""

import polars as pl

from v9.bars import resample
from v9.config import Config

TRADABLE = ("TREND", "PULLBACK")


def regime_4h(bars15: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    p = cfg.regime
    h4 = resample(bars15, "4h")
    lc = pl.col("close").log()
    s = (lc - lc.shift(p.slow_bars)).sign()
    f = (lc - lc.shift(p.fast_bars)).sign()
    er = (pl.col("close") - pl.col("close").shift(p.er_bars)).abs() / (
        pl.col("close").diff().abs().rolling_sum(p.er_bars))
    out = h4.select(
        pl.col("close_time").alias("visible_at"),
        s.alias("S"), f.alias("F"), er.alias("er"),
    ).with_columns(
        pl.col("er").rolling_quantile(p.chop_quantile, window_size=p.chop_lookback_bars,
                                      min_samples=p.chop_min_bars).alias("er_threshold"),
        (pl.col("S") != pl.col("S").shift()).cast(pl.Int8).rolling_max(p.transition_bars).alias("_flip"),
    )
    state = (
        pl.when(pl.col("er_threshold").is_null() | pl.col("_flip").is_null() | pl.col("F").is_null())
        .then(pl.lit("WARMUP"))
        .when(pl.col("er") < pl.col("er_threshold")).then(pl.lit("CHOP"))
        .when((pl.col("_flip") > 0) | (pl.col("S") == 0)).then(pl.lit("TRANSITION"))
        .when(pl.col("S") == pl.col("F")).then(pl.lit("TREND"))
        .otherwise(pl.lit("PULLBACK"))
    )
    return out.with_columns(state.alias("state")).with_columns(
        pl.when(pl.col("state").is_in(TRADABLE)).then(pl.col("S")).otherwise(0).cast(pl.Int8).alias("direction")
    ).drop("_flip")
