"""Bar construction. 1H/4H/1D bars are built from 15m bars; a bar exists only if complete."""

from datetime import timedelta

import polars as pl

BASE = timedelta(minutes=15)
EVERY = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1)}

BAR_COLUMNS = [
    "open_time", "close_time", "open", "high", "low", "close",
    "volume", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote",
]


def resample(bars15: pl.DataFrame, every: str) -> pl.DataFrame:
    span = EVERY[every]
    need = span // BASE
    out = (
        bars15.sort("open_time")
        .group_by_dynamic("open_time", every=every, closed="left", label="left")
        .agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
            pl.col("quote_volume").sum(),
            pl.col("trades").sum(),
            pl.col("taker_buy_base").sum(),
            pl.col("taker_buy_quote").sum(),
            pl.len().alias("_n"),
        )
        .filter(pl.col("_n") == need)
        .drop("_n")
        .with_columns((pl.col("open_time") + span).alias("close_time"))
    )
    return out.select(BAR_COLUMNS)


def signed_taker_volume(bars: pl.DataFrame) -> pl.Series:
    """Taker buy minus taker sell volume (base asset): the per-bar CVD increment."""
    return (2 * bars["taker_buy_base"] - bars["volume"]).alias("delta")
