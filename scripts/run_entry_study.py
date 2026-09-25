"""T1: entry quality of raphael's method vs matched random entries (development set only).

    uv run python scripts/run_entry_study.py
"""

import numpy as np
import polars as pl

from v9 import stats
from v9.config import ROOT, load_config
from v9.data.store import load
from v9.entry_study import diff_ci, forward_metrics
from v9.trader import build_trader_frame, find_signals


def measure(rows, o, h, lo, c, cfg):
    es = cfg.entry_study
    out = []
    for bar, d, risk, a1 in rows:
        m = forward_metrics(o, h, lo, c, bar + 1, d, risk, a1,
                            es.horizons_h, es.passage_r, es.passage_window_h)
        if m is not None:
            out.append({"bar": bar, "dir": d, "risk_atr": risk / a1, **m})
    return pl.DataFrame(out)


def summarize(name: str, m: pl.DataFrame, cfg) -> dict:
    n = m.height
    res = {"name": name, "n": n}
    line = [f"{name:28s} n={n:6d}"]
    for k in cfg.entry_study.passage_r:
        p, lo, hi = stats.wilson(int(m[f"win_{k}r"].sum()), n)
        res[f"win_{k}r"] = p
        line.append(f"+{k}R first {p:6.1%} [{lo:.1%},{hi:.1%}]")
    for hz in cfg.entry_study.horizons_h:
        mean, lo, hi = stats.bootstrap_mean_ci(m[f"ret_{hz}h_atr"].to_numpy(), n_boot=2000)
        line.append(f"{hz}h {mean:+.2f}ATR")
    line.append(f"MFE med {m['mfe_r'].median():.2f}R  MAE med {m['mae_r'].median():.2f}R")
    print("  ".join(line))
    return res


def main() -> None:
    cfg = load_config()
    es = cfg.entry_study
    bars = load("um_15m", cfg)
    f = build_trader_frame(bars, cfg)
    o, h, lo, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
    t = f["close_time"]
    dev = ((t >= cfg.data.dev_start) & (t < cfg.data.holdout_start)).to_numpy()
    d4, s1, a1 = f["dir_4h"].to_numpy(), f["setup_1h"].to_numpy(), f["atr_1h"].to_numpy()

    sig = find_signals(f, cfg)
    sig = sig.filter(pl.Series(dev[sig["bar"].to_numpy()]))
    entry_open = o[np.minimum(sig["bar"].to_numpy() + 1, o.size - 1)]
    sig = sig.with_columns(pl.Series("risk", sig["dir"].to_numpy() * (entry_open - sig["stop"].to_numpy())))
    print(f"dev 15m bars: {dev.sum()}  4H tradable {np.mean(d4[dev] != 0):.1%}  "
          f"4H tradable & 1H Vegas setup {np.mean((d4[dev] != 0) & (s1[dev] == d4[dev])):.1%}")
    print(f"signals E-no4hpb {sig.height}  E-full (with 4H pullback) {sig['pullback_4h'].sum()}")

    groups = {}
    for name, s in (("E-no4hpb", sig), ("E-full", sig.filter(pl.col("pullback_4h")))):
        rows = list(zip(s["bar"], s["dir"], s["risk"], s["atr_1h"], strict=True))
        groups[name] = measure(rows, o, h, lo, c, cfg)
    med_risk_atr = float(np.median(groups["E-no4hpb"]["risk_atr"]))
    cost_r = 2 * (cfg.costs.taker_fee + cfg.costs.slippage) * np.median(entry_open) / np.median(
        sig["risk"].to_numpy())
    print(f"median natural stop {med_risk_atr:.2f} x ATR(1H); "
          f"round-trip cost ~ {cost_r:.2f}R at the median stop")

    rng = np.random.default_rng(es.seed)
    pools = {
        "random: same 4H state": np.flatnonzero(dev & (d4 != 0) & np.isfinite(a1)),
        "random: 1H setup, no sweep": np.flatnonzero(dev & (d4 != 0) & (s1 == d4) & np.isfinite(a1)),
    }
    for name, pool in pools.items():
        pick = rng.choice(pool, size=es.random_samples, replace=True)
        rows = [(int(j), int(d4[j]), med_risk_atr * a1[j], a1[j]) for j in pick]
        groups[name] = measure(rows, o, h, lo, c, cfg)

    print("\n## entry quality (dev set, before costs)")
    res = {k: summarize(k, v, cfg) for k, v in groups.items()}
    print("\n## signal minus random (+R-first rate), 95% CI")
    for s_name in ("E-no4hpb", "E-full"):
        for r_name in pools:
            for k in es.passage_r:
                a, b = res[s_name], res[r_name]
                dlt, lo_, hi_ = diff_ci(a[f"win_{k}r"], a["n"], b[f"win_{k}r"], b["n"])
                print(f"{s_name:9s} vs {r_name:28s} +{k}R: {dlt:+.1%} [{lo_:+.1%}, {hi_:+.1%}]")

    m = groups["E-no4hpb"].join(sig.select("bar", "pullback_4h"), on="bar")
    m = m.with_columns(f["close_time"].gather(m["bar"]).dt.year().alias("year"))
    print("\n## E-no4hpb by direction and year")
    print(m.group_by("dir").agg(pl.len(), pl.col("win_2r").mean(), pl.col("win_3r").mean(),
                                pl.col("ret_24h_atr").mean()).sort("dir"))
    by_year = m.group_by("year").agg(pl.len(), pl.col("win_2r").mean(), pl.col("ret_24h_atr").mean())
    print(by_year.sort("year"))
    out = ROOT / "data" / "results"
    out.mkdir(parents=True, exist_ok=True)
    m.write_parquet(out / "entry_study_signals.parquet")


if __name__ == "__main__":
    main()
