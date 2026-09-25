"""T3 (research plan §9): rule B entries, rule A stops, dynamic exits. Development set only.

    uv run python scripts/run_swing_study.py
"""

import itertools

import numpy as np
import polars as pl

from v9 import stats
from v9.config import ROOT, load_config
from v9.data.store import load
from v9.entry_study import measure
from v9.swing import EXITS, STOPS, bar_flags, build_swing_frame, find_b_signals, random_entries, run_exits

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT", "LTCUSDT"]
CONFIGS = list(itertools.product(STOPS, EXITS))


def daily_series(daily: list[tuple[int, float]], days: np.ndarray) -> np.ndarray:
    """Equity at each 00:00 UTC in `days` (ms), forward-filled from the last observation."""
    eq = np.ones(days.size)
    if not daily:
        return eq
    t = np.array([x[0] for x in daily])
    v = np.array([x[1] for x in daily])
    order = np.argsort(t, kind="stable")
    t, v = t[order], v[order]
    idx = np.searchsorted(t, days, side="right") - 1
    return np.where(idx >= 0, v[np.clip(idx, 0, None)], 1.0)


def s1_rows(arr, bars_, dirs_, buffer):
    """(bar, dir, S1 risk in price, atr) with S1 = signal-bar extreme -/+ buffer x ATR(1H)."""
    o, h, lo, a1 = arr
    stop = np.where(dirs_ == 1, lo[bars_] - buffer * a1[bars_], h[bars_] + buffer * a1[bars_])
    nxt = o[np.minimum(bars_ + 1, o.size - 1)]
    return list(zip(bars_, dirs_, dirs_ * (nxt - stop), a1[bars_], strict=True))


def main() -> None:
    cfg = load_config()
    es, sw = cfg.entry_study, cfg.swing
    start_ms = int(cfg.data.dev_start.timestamp() * 1000)
    end_ms = int(cfg.data.holdout_start.timestamp() * 1000)
    days = np.arange(start_ms + 86_400_000, end_ms + 1, 86_400_000)
    rng = np.random.default_rng(es.seed)
    eq_rows, rnd_trades, b_trades, b_daily = [], [], [], {}
    for sym in SYMBOLS:
        bars = load("um_15m", cfg, symbol=sym)
        funding = load("funding", cfg, symbol=sym)
        f = build_swing_frame(bars, cfg)
        fl = bar_flags(f, cfg)
        o, h, lo, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
        t = f["close_time"].dt.epoch("ms").to_numpy()
        dev = (t >= start_ms) & (t < end_ms)
        a1, d4 = f["atr_1h"].to_numpy(), f["dir4"].to_numpy()
        sig = find_b_signals(f, cfg, fl)
        sig = sig.filter(pl.Series(dev[sig["bar"].to_numpy()]))

        # 1. entry quality (risk = S1 distance)
        arr = (o, h, lo, a1)
        sb, sd = sig["bar"].to_numpy(), sig["dir"].to_numpy().astype(int)
        m_sig = measure(s1_rows(arr, sb, sd, sw.stop_buffer_atr1h), o, h, lo, c, es)
        med = float(np.median(m_sig["risk_atr"])) if m_sig.height else 1.5
        pool = np.flatnonzero(dev & (d4 != 0) & np.isfinite(a1))
        pick = rng.choice(pool, size=es.random_samples, replace=True)
        m_rnd = measure([(int(j), int(d4[j]), med * a1[j], a1[j]) for j in pick], o, h, lo, c, es)
        groups = {"B": m_sig, "random": m_rnd}
        for name, mask in (("fast, no level", (fl.fast_drop != 0) & fl.wick & ~fl.at_level),
                           ("level, no fast", fl.at_level & fl.wick & (fl.fast_drop == 0))):
            cand = np.flatnonzero(dev & mask & (d4 != 0))
            if cand.size:
                cand = rng.choice(cand, size=min(cand.size, es.random_samples), replace=False)
                rows = s1_rows(arr, cand, d4[cand].astype(int), sw.stop_buffer_atr1h)
                groups[name] = measure(rows, o, h, lo, c, es)
        for name, g in groups.items():
            if g.height:
                eq_rows.append({"symbol": sym, "group": name, "n": g.height,
                                "win_2r": g["win_2r"].mean(), "win_3r": g["win_3r"].mean(),
                                "mfe3": (g["mfe_r"] >= 3).mean(), "stop_touched": (g["mae_r"] >= 1).mean()})

        # 2. exits on random entries
        prob = sig.height / max(pool.size, 1)
        for seed in range(sw.random_seeds):
            ent = random_entries(f, cfg, np.random.default_rng(10_000 + seed), prob, med, dev)
            for stop, ex in CONFIGS:
                trades, _ = run_exits(f, ent, funding, cfg, stop, ex, start_ms, end_ms)
                rnd_trades += [{**tr, "symbol": sym, "seed": seed, "cfg": f"{stop}/{ex}"} for tr in trades]
        # 3. full system on rule B entries
        for stop, ex in CONFIGS:
            trades, daily = run_exits(f, sig, funding, cfg, stop, ex, start_ms, end_ms)
            b_trades += [{**tr, "symbol": sym, "cfg": f"{stop}/{ex}"} for tr in trades]
            b_daily.setdefault(f"{stop}/{ex}", []).append(daily_series(daily, days))
        print(f"{sym}: {sig.height} rule-B signals on the dev set")

    eq = pl.DataFrame(eq_rows)
    print("\n## 1. entry quality: +2R-first rate, pooled over symbols (weights = n)")
    def weighted(col):
        return ((pl.col(col) * pl.col("n")).sum() / pl.col("n").sum()).alias(col)

    agg = eq.group_by("group").agg(pl.col("n").sum(), weighted("win_2r"), weighted("win_3r"),
                                   weighted("stop_touched"))
    print(agg.sort("group"))
    b = eq.filter(pl.col("group") == "B").join(
        eq.filter(pl.col("group") == "random").select("symbol", pl.col("win_2r").alias("p")), on="symbol")
    n = b["n"].sum()
    excess = ((b["win_2r"] - b["p"]) * b["n"]).sum() / n
    se = float(np.sqrt((b["n"] * b["p"] * (1 - b["p"])).sum())) / n
    print(f"rule B vs same-state random: excess {excess:+.1%}  95% CI [{excess - 1.96 * se:+.1%}, "
          f"{excess + 1.96 * se:+.1%}]  symbols positive {(b['win_2r'] > b['p']).sum()}/{b.height}  n={n}")
    with pl.Config(tbl_rows=40, float_precision=3):
        two = eq.filter(pl.col("group").is_in(["B", "random"]))
        print(two.pivot("group", index="symbol", values="win_2r"))

    rt = pl.DataFrame(rnd_trades)
    print("\n## 2. exits on random entries (after costs): expectancy per seed, pooled over symbols")
    per_seed = rt.group_by("cfg", "seed").agg(pl.col("r").mean().alias("exp"), pl.len().alias("n"))
    summary = per_seed.group_by("cfg").agg(
        pl.col("exp").mean().alias("mean_exp"), (pl.col("exp") > 0).mean().alias("share_pos"),
        pl.col("exp").quantile(0.05).alias("p05"), pl.col("exp").quantile(0.95).alias("p95"),
        pl.col("n").mean().alias("trades_per_seed"))
    with pl.Config(tbl_rows=20, float_precision=3):
        print(summary.sort("cfg"))

    bt = pl.DataFrame(b_trades)
    print("\n## 3. full system: rule B entries (after costs)")
    trial_sr = {}
    for k, series in b_daily.items():
        eqm = np.vstack(series)
        rets = (eqm[:, 1:] / eqm[:, :-1] - 1).mean(axis=0)
        trial_sr[k] = (rets, rets.mean() / rets.std(ddof=1) if rets.std(ddof=1) > 0 else 0.0)
    sr_list = [v[1] for v in trial_sr.values()]
    for k in sorted(trial_sr):
        r = bt.filter(pl.col("cfg") == k)["r"].to_numpy()
        s = stats.summarize_r(r)
        rets = trial_sr[k][0]
        port = np.cumprod(1 + rets)
        lo_, hi_ = s["expectancy_ci"]
        win = "n/a" if s["win_rate"] is None else f"{s['win_rate']:.1%}"
        print(f"{k}: n={s['n']:4d}  exp {s['expectancy_r']:+.3f}R [{lo_:+.3f}, {hi_:+.3f}]  win {win}"
              f"  avgW {s['avg_win_r']:+.2f} avgL {s['avg_loss_r']:+.2f}  skew {s['skew']:+.2f}  "
              f"Sharpe {stats.sharpe(rets):.2f}  MaxDD {stats.max_drawdown(port):.1%}  "
              f"DSR {stats.deflated_sharpe(rets, sr_list):.3f}")
    for k in ("S1/E2", "S2/E3"):
        reasons = bt.filter(pl.col("cfg") == k).group_by("reason").len()
        print(f"exit reasons {k}:", dict(reasons.iter_rows()))
    out = ROOT / "data" / "results"
    rt.write_parquet(out / "swing_random_trades.parquet")
    bt.write_parquet(out / "swing_b_trades.parquet")


if __name__ == "__main__":
    main()
