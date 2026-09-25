"""T1b: the T1 entry study on 8 USD-M perps with identical rules (research plan §8). Dev set only.

    uv run python scripts/fetch_data.py --klines-only \
        ETHUSDT BNBUSDT XRPUSDT ADAUSDT DOGEUSDT LINKUSDT LTCUSDT
    uv run python scripts/run_entry_study_multi.py
"""

import numpy as np
import polars as pl

from v9.config import ROOT, load_config
from v9.data.store import load
from v9.entry_study import measure
from v9.trader import build_trader_frame, find_signals

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT", "LTCUSDT"]


def one_symbol(sym: str, cfg, rng) -> tuple[pl.DataFrame, dict]:
    es = cfg.entry_study
    bars = load("um_15m", cfg, symbol=sym)
    f = build_trader_frame(bars, cfg)
    o, h, lo, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
    t = f["close_time"]
    dev = ((t >= cfg.data.dev_start) & (t < cfg.data.holdout_start)).to_numpy()
    expected = int((cfg.data.holdout_start - cfg.data.dev_start).total_seconds() // 900)
    d4, a1 = f["dir_4h"].to_numpy(), f["atr_1h"].to_numpy()

    sig = find_signals(f, cfg)
    sig = sig.filter(pl.Series(dev[sig["bar"].to_numpy()]))
    bar = sig["bar"].to_numpy()
    risk = sig["dir"].to_numpy() * (o[np.minimum(bar + 1, o.size - 1)] - sig["stop"].to_numpy())
    rows = list(zip(bar, sig["dir"], risk, sig["atr_1h"], strict=True))
    cost_r = 2 * (cfg.costs.taker_fee + cfg.costs.slippage) * o[np.minimum(bar + 1, o.size - 1)] / risk
    extra = sig.select("bar", "pullback_4h").with_columns(pl.Series("cost_r", cost_r))
    m = measure(rows, o, h, lo, c, es).join(extra, on="bar").with_columns(pl.lit(sym).alias("symbol"))

    pool = np.flatnonzero(dev & (d4 != 0) & np.isfinite(a1))
    pick = rng.choice(pool, size=es.random_samples, replace=True)
    med = float(np.median(m["risk_atr"])) if m.height else np.nan
    rnd = measure([(int(j), int(d4[j]), med * a1[j], a1[j]) for j in pick], o, h, lo, c, es)
    info = {"symbol": sym, "dev_bars": int(dev.sum()), "expected_bars": expected, "n": m.height,
            "p_rand_2r": float(rnd["win_2r"].mean()), "p_rand_3r": float(rnd["win_3r"].mean())}
    return m, info


def pooled(m: pl.DataFrame, info: pl.DataFrame, k: int) -> tuple[float, float, float]:
    """Excess of the signal hit rate over each symbol's own random rate, pooled, with a 95% CI."""
    j = m.group_by("symbol").agg(pl.len().alias("n"), pl.col(f"win_{k}r").sum().alias("w")).join(
        info.select("symbol", pl.col(f"p_rand_{k}r").alias("p")), on="symbol")
    n = j["n"].sum()
    excess = (j["w"].sum() - (j["n"] * j["p"]).sum()) / n
    se = float(np.sqrt((j["n"] * j["p"] * (1 - j["p"])).sum())) / n
    return excess, excess - 1.96 * se, excess + 1.96 * se


def main() -> None:
    cfg = load_config()
    rng = np.random.default_rng(cfg.entry_study.seed)
    parts, infos = [], []
    for sym in SYMBOLS:
        m, info = one_symbol(sym, cfg, rng)
        parts.append(m)
        infos.append(info)
    m, info = pl.concat(parts, how="diagonal_relaxed"), pl.DataFrame(infos)

    print("## per symbol (E-no4hpb), dev set, before costs")
    per = m.group_by("symbol").agg(
        pl.len().alias("n"), pl.col("win_2r").mean().alias("sig_2r"), pl.col("win_3r").mean().alias("sig_3r"),
        (pl.col("mae_r") >= 1).mean().alias("stop_touched"), (pl.col("mfe_r") >= 3).mean().alias("mfe3"),
        pl.col("cost_r").median().alias("cost_r"),
    ).join(info, on="symbol").with_columns((pl.col("sig_2r") - pl.col("p_rand_2r")).alias("excess_2r"))
    with pl.Config(tbl_rows=20, tbl_cols=20, float_precision=3):
        print(per.select("symbol", "dev_bars", "expected_bars", "n", "sig_2r", "p_rand_2r", "excess_2r",
                         "sig_3r", "p_rand_3r", "stop_touched", "mfe3", "cost_r").sort("symbol"))
    positive = int((per["excess_2r"] > 0).sum())
    print(f"symbols with positive +2R excess: {positive} / {per.height}")

    for label, sub in (("all 8", m), ("ex-BTC 7", m.filter(pl.col("symbol") != "BTCUSDT")),
                       ("all 8, E-full", m.filter(pl.col("pullback_4h")))):
        for k in cfg.entry_study.passage_r:
            e, lo_, hi_ = pooled(sub, info, k)
            print(f"pooled {label:14s} n={sub.height:4d}  +{k}R-first excess {e:+.1%}  "
                  f"95% CI [{lo_:+.1%}, {hi_:+.1%}]")

    print(f"\npooled: MAE>=1R {(m['mae_r'] >= 1).mean():.1%}  MFE>=3R {(m['mfe_r'] >= 3).mean():.1%}  "
          f"median cost {m['cost_r'].median():.2f}R")
    m.write_parquet(ROOT / "data" / "results" / "entry_study_multi.parquet")


if __name__ == "__main__":
    main()
