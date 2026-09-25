"""R3: descriptive statistics of the 4H regime on the development set.

    uv run python scripts/run_regime_report.py
"""

import numpy as np
import polars as pl

from v9 import stats
from v9.config import load_config
from v9.data.store import load
from v9.regime import regime_4h

FWD = 6  # 4H bars = 1 day


def main() -> None:
    cfg = load_config()
    bars = load("um_15m", cfg)
    r = regime_4h(bars, cfg)
    closes = bars.select(pl.col("close_time").alias("visible_at"), "close")
    r = r.join(closes, on="visible_at", how="left").with_columns(
        (pl.col("close").shift(-FWD).log() - pl.col("close").log()).alias("fwd"))
    t = pl.col("visible_at")
    dev = r.filter((t > cfg.data.dev_start) & (t <= cfg.data.holdout_start))

    print("## share of 4H bars by year")
    year = (pl.col("visible_at") - pl.duration(hours=4)).dt.year().alias("year")  # year the bar opened in
    share = (dev.group_by(year, "state").agg(pl.len())
             .with_columns((pl.col("len") / pl.col("len").sum().over("year") * 100).round(1))
             .pivot("state", index="year", values="len").sort("year"))
    print(share)
    tot = dev.group_by("state").agg(pl.len()).with_columns((pl.col("len") / dev.height * 100).round(1))
    print(tot.sort("state"))

    print("## spell length (4H bars)")
    spells = dev.with_columns((pl.col("state") != pl.col("state").shift()).cum_sum().alias("spell"))
    lengths = spells.group_by("spell", "state").agg(pl.len().alias("bars"))
    print(lengths.group_by("state").agg(pl.len().alias("spells"), pl.col("bars").median().alias("median"),
                                        pl.col("bars").mean().round(1).alias("mean")).sort("state"))

    print(f"## next-{FWD}-bar log return x S, sampled every {FWD} bars (non-overlapping)")
    sampled = dev.gather_every(FWD).filter(pl.col("fwd").is_not_null() & (pl.col("S") != 0))
    for state in ("TREND", "PULLBACK", "TRANSITION", "CHOP"):
        x = sampled.filter(pl.col("state") == state)
        v = (x["fwd"] * x["S"]).to_numpy()
        m, lo, hi = stats.bootstrap_mean_ci(v)
        print(f"{state:10s} n={v.size:4d} mean={m * 100:+.3f}%  "
              f"95% CI [{lo * 100:+.3f}%, {hi * 100:+.3f}%]  t={stats.t_stat(v):+.2f}  "
              f"abs-move mean={np.abs(v).mean() * 100:.2f}%")


if __name__ == "__main__":
    main()
