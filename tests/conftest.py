from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

T0 = datetime(2021, 1, 1, tzinfo=UTC)


def make_bars15(close: np.ndarray, start: datetime = T0, spread: float = 0.002) -> pl.DataFrame:
    """Synthetic 15m bars from a close path; high/low bracket open and close."""
    close = np.asarray(close, dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    n = len(close)
    t = [start + timedelta(minutes=15 * i) for i in range(n)]
    vol = np.full(n, 100.0)
    return pl.DataFrame({
        "open_time": t,
        "close_time": [x + timedelta(minutes=15) for x in t],
        "open": open_, "high": high, "low": low, "close": close,
        "volume": vol, "quote_volume": vol * close, "trades": np.full(n, 10, dtype=np.int64),
        "taker_buy_base": vol * 0.5, "taker_buy_quote": vol * 0.5 * close,
    }).with_columns(pl.col("open_time", "close_time").dt.cast_time_unit("ms"))


@pytest.fixture
def random_walk_bars15() -> pl.DataFrame:
    rng = np.random.default_rng(7)
    steps = rng.normal(0, 0.004, 4 * 24 * 120)  # 120 days
    return make_bars15(30000 * np.exp(np.cumsum(steps)))
