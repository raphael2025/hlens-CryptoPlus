"""T3 (research plan §9): fast-drop-to-key-level entry (rule B), key-level stop (rule A), dynamic exits.

Long is described; short is symmetric. All context columns are as-of values visible at the
15m close; the engine fills at the next open.
"""

from dataclasses import dataclass

import numpy as np
import polars as pl

from v9.bars import resample
from v9.config import Config
from v9.swings import atr, directional_change

STOPS = ("S1", "S2")
EXITS = ("E1", "E2", "E3", "E4")


def _ema(col: str, n: int) -> pl.Expr:
    return pl.col(col).ewm_mean(span=n, adjust=False)


def context_4h(bars15: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    t, s = cfg.trader, cfg.swing
    h4 = resample(bars15, "4h")
    hi, lo, cl = (h4[c].to_numpy() for c in ("high", "low", "close"))
    a4 = atr(hi, lo, cl, t.atr_bars)
    sw = directional_change(hi, lo, cl, a4, cfg.structure.dc_atr_mult)
    e9, e21, e55, e200 = t.ema_4h
    df = h4.select("close_time", "close").with_columns(
        _ema("close", e9).alias("e9"), _ema("close", e21).alias("e21"),
        _ema("close", e55).alias("e55"), _ema("close", e200).alias("e200"), pl.Series("atr_4h", a4),
        pl.Series("sw_lo_4h", sw.sl), pl.Series("sw_hi_4h", sw.sh),
        pl.Series("sw_lo_4h_idx", sw.sl_idx), pl.Series("sw_hi_4h_idx", sw.sh_idx),
    )
    e9_, e21_, e55_, e200_, c = (pl.col(k) for k in ("e9", "e21", "e55", "e200", "close"))
    chop = ((e21_ - e55_).abs() < t.chop_ema_gap_atr4h * pl.col("atr_4h")) | pl.col("atr_4h").is_nan()
    direction = (pl.when(chop).then(0).when((e21_ > e55_) & (c > e200_)).then(1)
                 .when((e21_ < e55_) & (c < e200_)).then(-1).otherwise(0).cast(pl.Int8))
    spread = e21_ - e55_
    widening = spread.abs() > spread.shift(s.strong_spread_bars_4h).abs()
    strong = (pl.when(direction == 1).then((e9_ > e21_) & (e21_ > e55_) & widening)
              .when(direction == -1).then((e9_ < e21_) & (e21_ < e55_) & widening).otherwise(False))
    return df.select(
        pl.col("close_time").alias("h4_at"), direction.alias("dir4"),
        strong.fill_null(False).alias("strong4"),
        pl.col("e55").alias("ema55_4h"), "sw_lo_4h", "sw_hi_4h", "sw_lo_4h_idx", "sw_hi_4h_idx",
    )


def context_1h(bars15: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    t = cfg.trader
    h1 = resample(bars15, "1h")
    hi, lo, cl = (h1[c].to_numpy() for c in ("high", "low", "close"))
    a1 = atr(hi, lo, cl, t.atr_bars)
    sw = directional_change(hi, lo, cl, a1, cfg.structure.dc_atr_mult)
    fa, fb = t.vegas_a
    return h1.select("close_time", "close").with_columns(
        _ema("close", t.vegas_filter).alias("ema12"),
        _ema("close", fa).alias("va"), _ema("close", fb).alias("vb"),
        pl.Series("atr_1h", a1), pl.Series("sw_lo_1h", sw.sl), pl.Series("sw_hi_1h", sw.sh),
    ).select(
        pl.col("close_time").alias("h1_at"), "ema12", "atr_1h", "sw_lo_1h", "sw_hi_1h",
        pl.max_horizontal("va", "vb").alias("tunnel_top"),
        pl.min_horizontal("va", "vb").alias("tunnel_bottom"),
    )


def build_swing_frame(bars15: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    base = bars15.select("open_time", "close_time", "open", "high", "low", "close").sort("close_time")
    f = base.join_asof(context_4h(bars15, cfg), left_on="close_time", right_on="h4_at", strategy="backward")
    f = f.join_asof(context_1h(bars15, cfg), left_on="close_time", right_on="h1_at", strategy="backward")
    return f.with_columns(pl.col("dir4").fill_null(0), pl.col("strong4").fill_null(False),
                          (pl.col("h1_at") == pl.col("close_time")).fill_null(False).alias("h1_close"))


@dataclass
class BarFlags:
    fast_drop: np.ndarray   # direction of a qualifying fast move against the trend (+1: drop in an uptrend)
    at_level: np.ndarray    # touched a key level and closed back on the trend side
    wick: np.ndarray        # rejection wick on the trend side
    key: np.ndarray         # the touched key level (nan if none)


def bar_flags(f: pl.DataFrame, cfg: Config) -> BarFlags:
    s = cfg.swing
    o, h, lo, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
    d4, a1 = f["dir4"].to_numpy(), f["atr_1h"].to_numpy()
    w = s.fast_drop_bars
    hh = pl.Series(h).rolling_max(w).to_numpy()
    ll = pl.Series(lo).rolling_min(w).to_numpy()
    drop = (hh - lo) >= s.fast_drop_atr1h * a1
    rally = (h - ll) >= s.fast_drop_atr1h * a1
    rng = h - lo
    wick_up = (np.minimum(o, c) - lo) >= s.wick_frac * rng
    wick_dn = (h - np.maximum(o, c)) >= s.wick_frac * rng
    k = s.level_touch_atr1h * a1
    sup = np.vstack([f[x].to_numpy() for x in ("tunnel_top", "tunnel_bottom", "ema55_4h", "sw_lo_1h")])
    res = np.vstack([f[x].to_numpy() for x in ("tunnel_bottom", "tunnel_top", "ema55_4h", "sw_hi_1h")])
    with np.errstate(invalid="ignore"):
        touch_sup = (lo <= sup + k) & (c > sup)
        touch_res = (h >= res - k) & (c < res)
    key_long = np.where(touch_sup, sup, -np.inf).max(axis=0)       # nearest defended support
    key_short = np.where(touch_res, res, np.inf).min(axis=0)
    long_ok, short_ok = d4 == 1, d4 == -1
    fast = np.where(long_ok & drop, 1, np.where(short_ok & rally, -1, 0)).astype(np.int8)
    at = np.where(long_ok, np.isfinite(key_long), np.where(short_ok, np.isfinite(key_short), False))
    wick = np.where(long_ok, wick_up & (rng > 0), np.where(short_ok, wick_dn & (rng > 0), False))
    key = np.where(long_ok & np.isfinite(key_long), key_long, np.where(short_ok & np.isfinite(key_short),
                                                                        key_short, np.nan))
    valid = np.isfinite(a1)
    return BarFlags(np.where(valid, fast, 0).astype(np.int8), at & valid, wick & valid, key)


def find_b_signals(f: pl.DataFrame, cfg: Config, flags: BarFlags | None = None) -> pl.DataFrame:
    s = cfg.swing
    fl = flags or bar_flags(f, cfg)
    lo, h, a1 = f["low"].to_numpy(), f["high"].to_numpy(), f["atr_1h"].to_numpy()
    cand = np.flatnonzero((fl.fast_drop != 0) & fl.at_level & fl.wick)
    out, last = [], -10**9
    for j in cand:
        if j - last <= s.cooldown_bars:
            continue
        d = int(fl.fast_drop[j])
        stop = (lo[j] - s.stop_buffer_atr1h * a1[j]) if d == 1 else (h[j] + s.stop_buffer_atr1h * a1[j])
        out.append((int(j), d, float(stop), float(fl.key[j]), float(a1[j])))
        last = j
    schema = {"bar": pl.Int64, "dir": pl.Int8, "stop_s1": pl.Float64, "key": pl.Float64, "atr_1h": pl.Float64}
    return pl.DataFrame(out, schema=schema, orient="row")


def nearest_key(f: pl.DataFrame, bars: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    """Nearest support below (long) / resistance above (short) the close; 1 ATR(1H) away if none."""
    c, a1 = f["close"].to_numpy()[bars], f["atr_1h"].to_numpy()[bars]
    lv = np.vstack([f[x].to_numpy()[bars] for x in ("tunnel_top", "tunnel_bottom", "ema55_4h")]
                   + [np.where(dirs == 1, f["sw_lo_1h"].to_numpy()[bars], f["sw_hi_1h"].to_numpy()[bars])])
    with np.errstate(invalid="ignore"):
        below = np.where(lv < c, lv, -np.inf).max(axis=0)
        above = np.where(lv > c, lv, np.inf).min(axis=0)
    key = np.where(dirs == 1, below, above)
    return np.where(np.isfinite(key), key, c - dirs * a1)


def random_entries(f: pl.DataFrame, cfg: Config, rng: np.random.Generator, prob: float,
                   risk_atr: float, mask: np.ndarray) -> pl.DataFrame:
    """Random entry candidates on bars in `mask` where the 4H trend state allows a direction."""
    d4, a1, c = f["dir4"].to_numpy(), f["atr_1h"].to_numpy(), f["close"].to_numpy()
    pool = np.flatnonzero(mask & (d4 != 0) & np.isfinite(a1))
    bars = pool[rng.random(pool.size) < prob]
    dirs = d4[bars].astype(np.int8)
    return pl.DataFrame({"bar": bars.astype(np.int64), "dir": dirs,
                         "stop_s1": c[bars] - dirs * risk_atr * a1[bars],
                         "key": nearest_key(f, bars, dirs), "atr_1h": a1[bars]})


def run_exits(f: pl.DataFrame, entries: pl.DataFrame, funding: pl.DataFrame, cfg: Config, stop_mode: str,
              exit_mode: str, start_ms: int, end_ms: int) -> tuple[list[dict], list[tuple[int, float]]]:
    """Sequential engine. `entries`: bar, dir, stop_s1, key, atr_1h; the signal is at that bar's close."""
    s, c_ = cfg.swing, cfg.costs
    fee, slip = c_.taker_fee, c_.slippage
    o, h, lo, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
    t_open = f["open_time"].dt.epoch("ms").to_numpy()
    t_close = f["close_time"].dt.epoch("ms").to_numpy()
    h1c, ema12, a1 = f["h1_close"].to_numpy(), f["ema12"].to_numpy(), f["atr_1h"].to_numpy()
    strong = f["strong4"].to_numpy()
    sw4 = {1: (f["sw_lo_4h"].to_numpy(), f["sw_lo_4h_idx"].fill_null(-1).to_numpy()),
           -1: (f["sw_hi_4h"].to_numpy(), f["sw_hi_4h_idx"].fill_null(-1).to_numpy())}
    f_t, f_r = funding["time"].dt.epoch("ms").to_numpy(), funding["rate"].to_numpy()
    ebar = entries["bar"].to_numpy()
    t_sig = t_close[np.minimum(ebar, t_close.size - 1)]
    keep = (t_sig >= start_ms) & (t_sig < end_ms)
    ent = entries.filter(pl.Series(keep)).sort("bar")
    e_bar, e_dir = ent["bar"].to_numpy(), ent["dir"].to_numpy()
    e_s1, e_key = ent["stop_s1"].to_numpy(), ent["key"].to_numpy()
    max_bars = s.max_hold_days * 96
    equity, trades, daily = 1.0, [], []
    n = c.size
    ei = 0
    fp = 0
    while ei < e_bar.size:
        j0 = int(e_bar[ei]) + 1
        d = int(e_dir[ei])
        ei += 1
        if j0 >= n or t_close[j0] > end_ms:
            break
        a = a1[j0 - 1]
        fill = o[j0] * (1 + slip * d)
        if stop_mode == "S1":
            hard = e_s1[ei - 1]
            risk_px = (fill - hard) * d
            level = np.nan
        else:
            key = e_key[ei - 1]
            level = key - d * s.key_break_atr1h * a
            hard = key - d * s.catastrophe_atr1h * a
            risk_px = (fill - level) * d
        if not np.isfinite(risk_px) or risk_px <= 0:
            continue
        risk_amt = equity * cfg.risk.risk_per_trade
        qty = risk_amt / risk_px
        if qty * fill > cfg.risk.max_notional_x_equity * equity:
            continue
        cost = qty * fill * fee
        target = fill + d * s.fixed_target_r * risk_px if exit_mode == "E4" else np.nan
        mfe, countdown, fund_paid = 0.0, -1, 0.0
        swing_seen = int(sw4[d][1][j0 - 1])
        fp = int(np.searchsorted(f_t, t_open[j0], side="right"))
        exit_px, reason, j = np.nan, "", j0
        while j < n:
            # intrabar: hard stop first (conservative), then target
            hit_stop = lo[j] <= hard if d == 1 else h[j] >= hard
            if hit_stop:
                gap = (o[j] < hard) if d == 1 else (o[j] > hard)
                exit_px, reason = (o[j] if gap else hard), "STOP"
                break
            if exit_mode == "E4" and ((h[j] >= target) if d == 1 else (lo[j] <= target)):
                exit_px, reason = target, "TARGET"
                break
            mfe = max(mfe, ((h[j] - fill) if d == 1 else (fill - lo[j])) / risk_px)
            while fp < f_t.size and f_t[fp] <= t_close[j]:
                pay = qty * c[j] * f_r[fp] * d
                equity -= pay
                fund_paid += pay
                fp += 1
            if t_close[j] % 86_400_000 == 0:
                daily.append((int(t_close[j]), equity + qty * (c[j] - fill) * d - cost))
            use_trail = exit_mode == "E1" or (exit_mode == "E3" and strong[j])
            idx = int(sw4[d][1][j])
            if idx != swing_seen and idx >= 0:
                swing_seen = idx
                if use_trail:
                    new = sw4[d][0][j] - d * s.trail_buffer_atr1h * a1[j]
                    if (new - hard) * d > 0:
                        hard = new
            if h1c[j]:
                if stop_mode == "S2":
                    if countdown < 0:
                        if (c[j] - level) * d < 0:
                            countdown = 0
                    elif (c[j] - level) * d >= 0:
                        countdown = -1
                    else:
                        countdown += 1
                        if countdown >= s.key_reclaim_bars_1h:
                            exit_px, reason = c[j], "KEY_LOST"
                            break
                swing_exit = exit_mode == "E2" or (exit_mode == "E3" and not strong[j])
                if swing_exit and mfe >= s.swing_min_mfe_r and (c[j] - ema12[j]) * d < 0:
                    exit_px, reason = c[j], "PULLBACK"
                    break
            if j - j0 >= max_bars:
                exit_px, reason = c[j], "TIME"
                break
            j += 1
        if reason == "":
            exit_px, reason, j = c[n - 1], "END", n - 1
        exit_px = exit_px * (1 - slip * d)
        cost += qty * exit_px * fee
        pnl = qty * (exit_px - fill) * d
        equity += pnl - cost
        trades.append({"entry_time": int(t_open[j0]), "exit_time": int(t_close[j]), "dir": d,
                       "r": (pnl - cost - fund_paid) / risk_amt, "reason": reason, "mfe": mfe,
                       "bars": j - j0 + 1})
        daily.append((int(t_close[j]), equity))
        while ei < e_bar.size and e_bar[ei] < j:
            ei += 1
    return trades, daily
