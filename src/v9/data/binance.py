"""Binance public archive (data.binance.vision): URL building, checksum-verified download, parsing."""

import hashlib
import io
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

BASE_URL = "https://data.binance.vision/data"
MARKETS = {"um": "futures/um", "spot": "spot"}

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]


def kline_url(market: str, symbol: str, interval: str, month: str) -> str:
    return f"{BASE_URL}/{MARKETS[market]}/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{month}.zip"


def premium_url(symbol: str, interval: str, month: str) -> str:
    return (f"{BASE_URL}/futures/um/monthly/premiumIndexKlines/{symbol}/{interval}/"
            f"{symbol}-{interval}-{month}.zip")


def funding_url(symbol: str, month: str) -> str:
    return f"{BASE_URL}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"


def metrics_url(symbol: str, day: str) -> str:
    return f"{BASE_URL}/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day}.zip"


def months(start: date, end_inclusive: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end_inclusive.year, end_inclusive.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _get(url: str, timeout: float = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def download(url: str, dest_dir: Path) -> Path | None:
    """Download `url` into `dest_dir`, verifying the archive's SHA-256 checksum.

    Returns the local path, or None if the archive has no such file (HTTP 404).
    """
    dest = dest_dir / url.rsplit("/", 1)[1]
    if dest.exists():
        return dest
    try:
        expected = _get(url + ".CHECKSUM").decode().split()[0]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    body = _get(url)
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected:
        raise ValueError(f"checksum mismatch for {url}: {actual} != {expected}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.rename(dest)
    return dest


def _csv_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(z.namelist()[0]).decode()


def _read_csv(text: str, columns: list[str]) -> pl.DataFrame:
    first = text.split("\n", 1)[0]
    has_header = not first[:1].isdigit() and not first[:1] == "-"
    return pl.read_csv(io.StringIO(text), has_header=has_header, new_columns=columns,
                       infer_schema_length=0)


def _to_utc_ms(col: pl.Expr) -> pl.Expr:
    """Binance spot switched to microseconds in 2025; normalise everything to UTC milliseconds."""
    v = col.cast(pl.Int64)
    ms = pl.when(v > 10**14).then(v // 1000).otherwise(v)
    return pl.from_epoch(ms, time_unit="ms").dt.replace_time_zone("UTC")


def parse_klines(path: Path) -> pl.DataFrame:
    df = _read_csv(_csv_text(path), KLINE_COLUMNS)
    return df.select(
        _to_utc_ms(pl.col("open_time")).alias("open_time"),
        *[pl.col(c).cast(pl.Float64) for c in ("open", "high", "low", "close", "volume", "quote_volume")],
        pl.col("trades").cast(pl.Int64),
        pl.col("taker_buy_base").cast(pl.Float64),
        pl.col("taker_buy_quote").cast(pl.Float64),
    )


def parse_funding(path: Path) -> pl.DataFrame:
    df = _read_csv(_csv_text(path), ["calc_time", "funding_interval_hours", "last_funding_rate"])
    # calc_time is occasionally 1 ms past the settlement instant; snap to the minute
    t = pl.col("calc_time").cast(pl.Int64)
    return df.select(
        pl.from_epoch((t + 30_000) // 60_000 * 60_000, time_unit="ms").dt.replace_time_zone("UTC").alias("time"),
        pl.col("funding_interval_hours").cast(pl.Int64).alias("interval_hours"),
        pl.col("last_funding_rate").cast(pl.Float64).alias("rate"),
    )


METRIC_COLUMNS = [
    "create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
    "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
    "count_long_short_ratio", "sum_taker_long_short_vol_ratio",
]


def parse_metrics(path: Path) -> pl.DataFrame:
    df = _read_csv(_csv_text(path), METRIC_COLUMNS)
    return df.select(
        pl.col("create_time").str.to_datetime("%Y-%m-%d %H:%M:%S", time_zone="UTC", time_unit="ms").alias("time"),
        pl.col("sum_open_interest").cast(pl.Float64).alias("oi"),
        pl.col("sum_open_interest_value").cast(pl.Float64).alias("oi_value"),
        pl.col("count_toptrader_long_short_ratio").cast(pl.Float64).alias("top_account_ratio"),
        pl.col("sum_toptrader_long_short_ratio").cast(pl.Float64).alias("top_position_ratio"),
        pl.col("count_long_short_ratio").cast(pl.Float64).alias("global_account_ratio"),
        pl.col("sum_taker_long_short_vol_ratio").cast(pl.Float64).alias("taker_ratio"),
    )


def day_strings(start: date, end_inclusive: date) -> list[str]:
    n = (end_inclusive - start).days
    return [date.fromordinal(start.toordinal() + i).isoformat() for i in range(n + 1)]
