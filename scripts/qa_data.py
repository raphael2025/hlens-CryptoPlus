"""R1: data quality check over data/processed (full range, holdout included, counts only).

    uv run python scripts/qa_data.py
"""

from datetime import timedelta

import polars as pl

from v9.data import binance as bn
from v9.data.store import PROCESSED, RAW


def bar_qa(name: str, step: timedelta) -> None:
    df = pl.read_parquet(PROCESSED / f"{name}.parquet").sort("open_time")
    first, last = df["open_time"][0], df["open_time"][-1]
    expected = int((last - first) / step) + 1
    gaps = df.select(d=pl.col("open_time").diff()).filter(pl.col("d") > step)["d"]
    bad_ohlc = df.filter(
        (pl.col("low") > pl.min_horizontal("open", "close"))
        | (pl.col("high") < pl.max_horizontal("open", "close"))
    ).height
    bad_taker = df.filter(pl.col("taker_buy_base") > pl.col("volume") * (1 + 1e-9)).height
    zero_vol = df.filter(pl.col("volume") == 0).height
    print(f"## {name}\nfirst {first}  last {last}  rows {df.height}  expected {expected}  "
          f"missing {expected - df.height}")
    print(f"gaps>{step}: {gaps.len()}  largest {gaps.max() if gaps.len() else '-'}  "
          f"bad OHLC {bad_ohlc}  taker>volume {bad_taker}  zero-volume {zero_vol}")
    if gaps.len():
        g = df.with_columns(d=pl.col("open_time").diff()).filter(pl.col("d") > step)
        top = g.sort("d", descending=True).select("open_time", "d").head(5).iter_rows()
        print("largest gaps:", [(str(t), str(d)) for t, d in top])


def funding_qa() -> None:
    df = pl.read_parquet(PROCESSED / "funding.parquet").sort("time")
    d = df.select(pl.col("time").diff().alias("d"))["d"].drop_nulls()
    print(f"## funding\nfirst {df['time'][0]}  last {df['time'][-1]}  rows {df.height}  "
          f"interval_hours {sorted(df['interval_hours'].unique().to_list())}")
    print(f"spacing counts: {d.value_counts().sort('count', descending=True).head(5).rows()}")
    off = df.filter(pl.col("time").dt.minute() != 0).height
    print(f"off-hour settlements {off}  rate min {df['rate'].min():.6f} max {df['rate'].max():.6f} "
          f"mean {df['rate'].mean():.6f}")


def metrics_qa() -> None:
    raw_rows = sum(bn.parse_metrics(p).height for p in sorted((RAW / "um/metrics").glob("*.zip")))
    df = pl.read_parquet(PROCESSED / "metrics.parquet").sort("time")
    step = timedelta(minutes=5)
    first, last = df["time"][0], df["time"][-1]
    expected = int((last - first) / step) + 1
    off_grid = df.filter((pl.col("time").dt.minute() % 5 != 0) | (pl.col("time").dt.second() != 0)).height
    gaps = df.select(d=pl.col("time").diff()).filter(pl.col("d") > step)["d"]
    print(f"## metrics\nfirst {first}  last {last}  rows {df.height} (raw {raw_rows}, "
          f"duplicates removed {raw_rows - df.height})  expected {expected}  missing {expected - df.height}")
    print(f"off 5-min grid {off_grid}  gaps>5m {gaps.len()}  largest {gaps.max()}")
    print("nulls:", {c: df[c].null_count() for c in df.columns if df[c].null_count()})
    by_year = df.group_by(pl.col("time").dt.year().alias("y")).agg(pl.len()).sort("y")
    print("rows by year:", by_year.rows())


if __name__ == "__main__":
    bar_qa("um_15m", timedelta(minutes=15))
    bar_qa("spot_15m", timedelta(minutes=15))
    bar_qa("premium_1h", timedelta(hours=1))
    funding_qa()
    metrics_qa()
