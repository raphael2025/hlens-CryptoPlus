"""raphael's actual entry method (framework §25, research plan §7). Long described; short is symmetric.

4H  direction: EMA21 > EMA55, close > EMA200, 4H DC structure trend up; chop if |EMA21-EMA55| < k*ATR4
    pullback : some of the last N 4H lows <= EMA21 while the close holds EMA55
1H  Vegas    : EMA144 > EMA169 and, within the last N 1H bars, a low reached the tunnel top + k*ATR1
               while the close held the tunnel bottom - k*ATR1
15m sweep    : a low pierces a key level (1H tunnel bottom, last confirmed 1H swing low), a close
               reclaims it within `sweep_reclaim_bars`, then a 15m BOS up within `choch_window_bars`
"""

import numpy as np
import polars as pl

from v9.bars import resample
from v9.config import Config
from v9.execution import triggers_15m
from v9.swings import atr, directional_change


def _ema(col: str, n: int) -> pl.Expr:
    return pl.col(col).ewm_mean(span=n, adjust=False)


def frame_4h(bars15: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    t = cfg.trader
    h4 = resample(bars15, "4h")
    hi, lo, cl = (h4[c].to_numpy() for c in ("high", "low", "close"))
    a4 = atr(hi, lo, cl, t.atr_bars)
    sw = directional_change(hi, lo, cl, a4, cfg.structure.dc_atr_mult)
    e9, e21, e55, e200 = t.ema_4h
    df = h4.select("close_time", "high", "low", "close").with_columns(
        _ema("close", e9).alias("ema9"), _ema("close", e21).alias("ema21"),
        _ema("close", e55).alias("ema55"), _ema("close", e200).alias("ema200"),
        pl.Series("atr_4h", a4), pl.Series("trend_4h", sw.trend),
    )
    n = t.pullback_4h_bars
    e21, e55, e200, close = pl.col("ema21"), pl.col("ema55"), pl.col("ema200"), pl.col("close")
    up = (e21 > e55) & (close > e200) & (pl.col("trend_4h") == 1)
    dn = (e21 < e55) & (close < e200) & (pl.col("trend_4h") == -1)
    chop = (pl.col("ema21") - pl.col("ema55")).abs() < t.chop_ema_gap_atr4h * pl.col("atr_4h")
    touched_up = (pl.col("low") <= pl.col("ema21")).cast(pl.Int8).rolling_max(n) == 1
    touched_dn = (pl.col("high") >= pl.col("ema21")).cast(pl.Int8).rolling_max(n) == 1
    direction = (pl.when(chop | pl.col("atr_4h").is_nan()).then(0)
                 .when(up).then(1).when(dn).then(-1).otherwise(0).cast(pl.Int8))
    pullback = (pl.when(direction == 1).then(touched_up & (pl.col("close") >= pl.col("ema55")))
                .when(direction == -1).then(touched_dn & (pl.col("close") <= pl.col("ema55")))
                .otherwise(False))
    return df.select(
        pl.col("close_time").alias("visible_at"),
        direction.alias("dir_4h"), pullback.fill_null(False).alias("pullback_4h"),
        chop.fill_null(True).alias("chop_4h"),
    )


def frame_1h(bars15: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    t = cfg.trader
    h1 = resample(bars15, "1h")
    hi, lo, cl = (h1[c].to_numpy() for c in ("high", "low", "close"))
    a1 = atr(hi, lo, cl, t.atr_bars)
    sw = directional_change(hi, lo, cl, a1, cfg.structure.dc_atr_mult)
    fa, fb = t.vegas_a
    k, n = t.tunnel_touch_atr1h, t.pullback_1h_bars
    df = h1.select("close_time", "high", "low", "close").with_columns(
        _ema("close", t.vegas_filter).alias("ema12"), _ema("close", fa).alias("ema_a1"),
        _ema("close", fb).alias("ema_a2"), pl.Series("atr_1h", a1),
        pl.Series("swing_low", sw.sl), pl.Series("swing_high", sw.sh),
    ).with_columns(
        pl.max_horizontal("ema_a1", "ema_a2").alias("tunnel_top"),
        pl.min_horizontal("ema_a1", "ema_a2").alias("tunnel_bottom"),
    )
    a = pl.col("atr_1h")
    touch_up = ((pl.col("low") <= pl.col("tunnel_top") + k * a).cast(pl.Int8).rolling_max(n) == 1) & (
        pl.col("close") >= pl.col("tunnel_bottom") - k * a)
    touch_dn = ((pl.col("high") >= pl.col("tunnel_bottom") - k * a).cast(pl.Int8).rolling_max(n) == 1) & (
        pl.col("close") <= pl.col("tunnel_top") + k * a)
    setup = (pl.when((pl.col("ema_a1") > pl.col("ema_a2")) & touch_up).then(1)
             .when((pl.col("ema_a1") < pl.col("ema_a2")) & touch_dn).then(-1).otherwise(0))
    return df.select(
        pl.col("close_time").alias("visible_at"), setup.fill_null(0).cast(pl.Int8).alias("setup_1h"),
        "tunnel_top", "tunnel_bottom", "swing_low", "swing_high", "atr_1h", "ema12",
    )


def build_trader_frame(bars15: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    base = bars15.select("open_time", "close_time", "open", "high", "low", "close").sort("close_time")
    tr = triggers_15m(bars15, cfg).rename({"visible_at": "close_time"})
    f = base.join(tr, on="close_time", how="left")
    f = f.join_asof(frame_4h(bars15, cfg).rename({"visible_at": "h4_at"}), left_on="close_time",
                    right_on="h4_at", strategy="backward")
    f = f.join_asof(frame_1h(bars15, cfg).rename({"visible_at": "h1_at"}), left_on="close_time",
                    right_on="h1_at", strategy="backward")
    return f.with_columns(pl.col("dir_4h").fill_null(0), pl.col("setup_1h").fill_null(0),
                          pl.col("pullback_4h").fill_null(False))


def find_signals(f: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    """One signal per sweep episode, at the close of the reversal (15m BOS) bar; entry is the next open."""
    t = cfg.trader
    lo, hi, c = (f[k].to_numpy() for k in ("low", "high", "close"))
    d4, s1 = f["dir_4h"].to_numpy(), f["setup_1h"].to_numpy()
    pb4 = f["pullback_4h"].to_numpy()
    tb, tt = f["tunnel_bottom"].to_numpy(), f["tunnel_top"].to_numpy()
    swl, swh = f["swing_low"].to_numpy(), f["swing_high"].to_numpy()
    a1 = f["atr_1h"].to_numpy()
    bu, bd = f["bos_up"].to_numpy(), f["bos_down"].to_numpy()
    out = []
    ep_dir, sweep_bar, level, extreme, reclaimed = 0, -1, np.nan, np.nan, False
    for j in range(len(f)):
        d = int(d4[j])
        if d == 0 or s1[j] != d or d != ep_dir:
            ep_dir, sweep_bar, reclaimed = d, -1, False
            if d == 0 or s1[j] != d:
                continue
        levels = [tb[j], swl[j]] if d == 1 else [tt[j], swh[j]]
        pierced = [x for x in levels if np.isfinite(x) and ((lo[j] < x) if d == 1 else (hi[j] > x))]
        if pierced:
            new_level = max(pierced) if d == 1 else min(pierced)
            # a sweep starts only when price comes from the right side of the level; staying beyond it
            # for longer than the reclaim window is acceptance (a breakdown), not a stop hunt
            from_above = j > 0 and ((c[j - 1] > new_level) if d == 1 else (c[j - 1] < new_level))
            if (sweep_bar < 0 or reclaimed) and from_above:
                sweep_bar, level, extreme, reclaimed = j, new_level, (lo[j] if d == 1 else hi[j]), False
            elif sweep_bar >= 0 and not reclaimed:
                level = max(level, new_level) if d == 1 else min(level, new_level)
        if sweep_bar < 0:
            continue
        extreme = min(extreme, lo[j]) if d == 1 else max(extreme, hi[j])
        if not reclaimed:
            if j - sweep_bar >= t.sweep_reclaim_bars:
                sweep_bar = -1
                continue
            reclaimed = (c[j] > level) if d == 1 else (c[j] < level)
        if reclaimed and j - sweep_bar > t.choch_window_bars:
            sweep_bar, reclaimed = -1, False
            continue
        if reclaimed and (bu[j] if d == 1 else bd[j]) and np.isfinite(a1[j]):
            stop = extreme - d * t.stop_buffer_atr1h * a1[j]
            out.append((j, d, stop, a1[j], bool(pb4[j]), sweep_bar))
            sweep_bar, reclaimed = -1, False
    schema = {"bar": pl.Int64, "dir": pl.Int8, "stop": pl.Float64, "atr_1h": pl.Float64,
              "pullback_4h": pl.Boolean, "sweep_bar": pl.Int64}
    return pl.DataFrame(out, schema=schema, orient="row")
