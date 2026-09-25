"""Baselines (research plan §3).

B0: buy and hold the perp (pays funding).
B1: daily price / moving-average trend, long-only and long-short, volatility-targeted.

A position decided at a daily close is held until the next daily close; it earns that
day's return, pays the funding settled in (close, next close], and pays costs on the
change in position at the close where it was decided.
"""

import math
from datetime import timedelta

import numpy as np
import polars as pl

from v9.bars import resample
from v9.config import Config


def daily_with_funding(bars15: pl.DataFrame, funding: pl.DataFrame) -> pl.DataFrame:
    d = resample(bars15, "1d").select("open_time", "close_time", "close")
    day = (pl.col("time") - timedelta(milliseconds=1)).dt.truncate("1d").alias("open_time")
    f = funding.with_columns(day).group_by("open_time").agg(pl.col("rate").sum().alias("funding"))
    return (d.join(f, on="open_time", how="left").with_columns(pl.col("funding").fill_null(0.0))
            .sort("open_time"))


def b1_positions(daily: pl.DataFrame, cfg: Config, window: int, long_short: bool) -> pl.DataFrame:
    """Target position at each daily close; visible at that close."""
    b = cfg.baselines
    logret = pl.col("close").log().diff()
    vol = logret.rolling_std(b.vol_window) * math.sqrt(365)
    ratio = pl.col("close") / pl.col("close").rolling_mean(window)
    sign = pl.when(ratio > 1).then(1.0).otherwise(-1.0 if long_short else 0.0)
    size = pl.min_horizontal(pl.lit(b.vol_target) / vol, pl.lit(b.max_leverage))
    return daily.select(
        pl.col("close_time").alias("visible_at"),
        pl.when(ratio.is_null() | vol.is_null()).then(0.0).otherwise(sign * size).alias("position"),
    )


def strategy_returns(daily: pl.DataFrame, position: np.ndarray, cfg: Config) -> np.ndarray:
    """Daily net returns; element i is earned over (close[i-1], close[i]]."""
    close = daily["close"].to_numpy()
    fund = daily["funding"].to_numpy()
    cost = cfg.costs.taker_fee + cfg.costs.slippage
    held = np.concatenate([[0.0], position[:-1]])            # position during day i
    prev_held = np.concatenate([[0.0], held[:-1]])
    ret = np.concatenate([[0.0], close[1:] / close[:-1] - 1])
    return held * ret - held * fund - cost * np.abs(held - prev_held)


def evaluate(daily: pl.DataFrame, returns: np.ndarray, start, end) -> dict:
    from v9 import stats

    t = daily["close_time"]
    mask = ((t > start) & (t <= end)).to_numpy()
    r = returns[mask]
    eq = np.cumprod(1 + r)
    years = r.size / 365
    return {
        "days": int(r.size),
        "sharpe": stats.sharpe(r),
        "cagr": float(eq[-1] ** (1 / years) - 1) if years > 0 and eq[-1] > 0 else math.nan,
        "max_dd": stats.max_drawdown(np.concatenate([[1.0], eq])),
        "vol": float(r.std(ddof=1) * math.sqrt(365)),
        "total": float(eq[-1] - 1),
        "returns": r,
    }
