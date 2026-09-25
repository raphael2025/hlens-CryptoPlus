"""R1: download Binance archives for BTCUSDT and build data/processed/*.parquet.

    uv run python scripts/fetch_data.py [--end 2026-08]

Downloads everything up to the last complete month (metrics: up to yesterday), including
the holdout period; loaders hide the holdout unless explicitly asked (src/v9/data/store.py).
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import polars as pl

from v9.config import load_config
from v9.data import binance as bn
from v9.data.store import PROCESSED, RAW


def fetch_all(urls: list[str], dest, workers: int = 8):
    with ThreadPoolExecutor(workers) as ex:
        paths = list(ex.map(lambda u: bn.download(u, dest), urls))
    missing = [u for u, p in zip(urls, paths, strict=True) if p is None]
    return [p for p in paths if p is not None], missing


def build_bars(paths, interval_minutes: int) -> pl.DataFrame:
    df = pl.concat([bn.parse_klines(p) for p in paths]).unique("open_time").sort("open_time")
    return df.with_columns((pl.col("open_time") + timedelta(minutes=interval_minutes)).alias("close_time"))


def fetch_extra_klines(symbols: list[str], end: str) -> None:
    cfg = load_config()
    y, m = map(int, end.split("-"))
    ms = bn.months(cfg.data.warmup_start.date(), date(y, m, 1))
    for sym in symbols:
        urls = [bn.kline_url("um", sym, "15m", mo) for mo in ms]
        paths, missing = fetch_all(urls, RAW / f"um/klines/15m/{sym}")
        build_bars(paths, 15).write_parquet(PROCESSED / f"um_15m_{sym}.parquet")
        fpaths, fmissing = fetch_all([bn.funding_url(sym, mo) for mo in ms], RAW / f"um/fundingRate/{sym}")
        pl.concat([bn.parse_funding(p) for p in fpaths]).unique("time").sort("time").write_parquet(
            PROCESSED / f"funding_{sym}.parquet")
        print(f"{sym}: klines {len(paths)} files ({len(missing)} missing), "
              f"funding {len(fpaths)} files ({len(fmissing)} missing); months before listing are expected")


def main() -> None:
    ap = argparse.ArgumentParser()
    now = datetime.now(UTC).date()
    last_month = (now.replace(day=1) - timedelta(days=1))
    ap.add_argument("--end", default=f"{last_month.year:04d}-{last_month.month:02d}")
    ap.add_argument("--klines-only", nargs="+", metavar="SYMBOL",
                    help="only fetch USD-M 15m klines and funding for these extra symbols (T1b, T3)")
    args = ap.parse_args()
    if args.klines_only:
        fetch_extra_klines(args.klines_only, args.end)
        return

    cfg = load_config()
    sym = cfg.data.symbol
    y, m = map(int, args.end.split("-"))
    end_month = date(y, m, 1)
    start = cfg.data.warmup_start.date()
    ms = bn.months(start, end_month)
    report = {}

    jobs = {
        "um_15m": ([bn.kline_url("um", sym, "15m", mo) for mo in ms], "um/klines/15m", 15),
        "spot_15m": ([bn.kline_url("spot", sym, "15m", mo) for mo in ms], "spot/klines/15m", 15),
        "premium_1h": ([bn.premium_url(sym, "1h", mo) for mo in ms], "um/premiumIndexKlines/1h", 60),
    }
    PROCESSED.mkdir(parents=True, exist_ok=True)
    for name, (urls, sub, minutes) in jobs.items():
        paths, missing = fetch_all(urls, RAW / sub)
        build_bars(paths, minutes).write_parquet(PROCESSED / f"{name}.parquet")
        report[name] = (len(paths), missing)

    paths, missing = fetch_all([bn.funding_url(sym, mo) for mo in ms], RAW / "um/fundingRate")
    pl.concat([bn.parse_funding(p) for p in paths]).unique("time").sort("time").write_parquet(
        PROCESSED / "funding.parquet")
    report["funding"] = (len(paths), missing)

    days = bn.day_strings(cfg.data.dev_start.date(), now - timedelta(days=1))
    paths, missing = fetch_all([bn.metrics_url(sym, d) for d in days], RAW / "um/metrics", workers=16)
    pl.concat([bn.parse_metrics(p) for p in paths]).unique("time").sort("time").write_parquet(
        PROCESSED / "metrics.parquet")
    report["metrics"] = (len(paths), missing)

    for name, (n, miss) in report.items():
        print(f"{name}: {n} files, {len(miss)} missing" + (f" e.g. {miss[:3]}" if miss else ""))


if __name__ == "__main__":
    main()
