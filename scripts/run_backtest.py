"""R5 / R6: V9-core and its core comparisons on the development set.

    uv run python scripts/run_backtest.py
"""

import json

import numpy as np
import polars as pl

from v9 import stats
from v9.backtest import Variant, build_frame, run
from v9.config import ROOT, load_config
from v9.data.store import load

VARIANTS = [Variant("core"), Variant("no_gate", use_gate=False), Variant("sweep", entry="sweep")]
YEARS = [("2020-09..2021", 2020, 2021), ("2022", 2022, 2022), ("2023", 2023, 2023)]


def fmt_pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def daily_returns(daily: pl.DataFrame) -> tuple[np.ndarray, pl.DataFrame]:
    d = daily.with_columns(pl.col("equity").pct_change().alias("ret")).drop_nulls("ret")
    return d["ret"].to_numpy(), d


def main() -> None:
    cfg = load_config()
    bars, funding = load("um_15m", cfg), load("funding", cfg)
    frame = build_frame(bars, cfg)
    base = json.loads((ROOT / "data" / "results" / "baseline_daily_returns.json").read_text())
    base_sr = [np.mean(r) / np.std(r, ddof=1) for r in base.values()]
    out_dir = ROOT / "data" / "results"
    results = {}
    for v in VARIANTS:
        trades, daily = run(frame, funding, cfg, v, trade_start=cfg.data.dev_start)
        trades.write_parquet(out_dir / f"trades_{v.name}.parquet")
        r = trades["r"].to_numpy()
        s = stats.summarize_r(r)
        ret, d = daily_returns(daily)
        eq = daily["equity"].to_numpy()
        years = ret.size / 365
        sr_daily = ret.mean() / ret.std(ddof=1)
        results[v.name] = sr_daily
        print(f"\n=== {v.name} (config {cfg.version}) ===")
        lo, hi = s["expectancy_ci"]
        print(f"trades n={s['n']}  expectancy {s['expectancy_r']:+.3f}R  95% CI [{lo:+.3f}, {hi:+.3f}]  "
              f"t={s['t']:+.2f}  total {s['total_r']:+.1f}R")
        print(f"win rate {fmt_pct(s['win_rate'])} CI {s['win_rate_ci']}  avg win {s['avg_win_r']:+.2f}R  "
              f"avg loss {s['avg_loss_r']:+.2f}R  skew {s['skew']:+.2f}  "
              f"top10% share of gross {fmt_pct(s['top10_share_of_gross'])}")
        print(f"longest losing streak {s['longest_losing_streak']}  "
              f"MC streak (same win rate, n) {stats.mc_losing_streak(float((r > 0).mean()), r.size, 20_000)}")
        print(f"MC drawdown in R {stats.mc_drawdown_r(r)}  max R  {r.max():+.1f}")
        print(f"daily: Sharpe {stats.sharpe(ret):.2f}  CAGR {eq[-1] ** (1 / years) - 1:+.1%}  "
              f"MaxDD {stats.max_drawdown(eq):.1%}  total {eq[-1] - 1:+.1%}")
        trials = base_sr + [sr_daily]
        print(f"PSR(0) {stats.probabilistic_sharpe(ret):.3f}  DSR vs {len(trials)} trials "
              f"{stats.deflated_sharpe(ret, trials):.3f}")
        print("exit reasons:", dict(trades.group_by("reason").len().iter_rows()))
        by_dir = trades.group_by("dir").agg(pl.len(), pl.col("r").mean().alias("mean_r"), pl.col("r").sum())
        print("by direction:", by_dir.sort("dir").rows())
        by_no = trades.group_by("entry_no").agg(pl.len(), pl.col("r").mean()).sort("entry_no")
        print("by entry_no:", by_no.rows())
        hold = (trades["exit_time"] - trades["entry_time"]).dt.total_minutes() / 60
        print(f"holding hours median {hold.median():.1f}  mean {hold.mean():.1f}")
        for label, y0, y1 in YEARS:
            ty = trades.filter(pl.col("entry_time").dt.year().is_between(y0, y1))
            dy = d.filter(pl.col("time").dt.year().is_between(y0, y1) & (pl.col("time").dt.ordinal_day() > 1)
                          | (pl.col("time").dt.year() == y1 + 1) & (pl.col("time").dt.ordinal_day() == 1))
            ry = ty["r"].to_numpy()
            print(f"  {label:14s} n={ry.size:4d}  mean {ry.mean() if ry.size else np.nan:+.3f}R  "
                  f"total {ry.sum():+.1f}R  daily Sharpe {stats.sharpe(dy['ret'].to_numpy()):.2f}")


if __name__ == "__main__":
    main()
