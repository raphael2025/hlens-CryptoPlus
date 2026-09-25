from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from v9.bars import resample
from v9.config import load_config
from v9.data.store import restrict
from v9.lookahead import assert_truncation_invariant, evenly_spaced_cuts, truncation_diffs


def test_config_loads_and_is_versioned():
    cfg = load_config()
    assert cfg.version.startswith("9.0")
    assert cfg.data.dev_start < cfg.data.holdout_start


def test_resample_drops_incomplete_bars(random_walk_bars15):
    bars = random_walk_bars15.slice(0, 4 * 10 + 2)  # 10 full hours + 2 quarters
    h1 = resample(bars, "1h")
    assert h1.height == 10
    assert (h1["close_time"] - h1["open_time"]).unique().to_list() == [timedelta(hours=1)]
    first = bars.slice(0, 4)
    assert h1["high"][0] == first["high"].max() and h1["close"][0] == first["close"][3]


def _causal(bars: pl.DataFrame) -> pl.DataFrame:
    return bars.select(pl.col("close_time").alias("visible_at"), pl.col("close").rolling_mean(20).alias("ma"))


def _leaky(bars: pl.DataFrame) -> pl.DataFrame:
    return bars.select(pl.col("close_time").alias("visible_at"), pl.col("close").shift(-1).alias("next"))


def _leaky_normalisation(bars: pl.DataFrame) -> pl.DataFrame:
    return bars.select(pl.col("close_time").alias("visible_at"), (pl.col("close") / pl.col("close").last()))


def test_lookahead_harness_passes_causal(random_walk_bars15):
    assert_truncation_invariant(_causal, random_walk_bars15, evenly_spaced_cuts(random_walk_bars15))


@pytest.mark.parametrize("fn", [_leaky, _leaky_normalisation])
def test_lookahead_harness_catches_leaks(random_walk_bars15, fn):
    assert truncation_diffs(fn, random_walk_bars15, evenly_spaced_cuts(random_walk_bars15))


def test_holdout_hidden_by_default():
    cfg = load_config()
    h = cfg.data.holdout_start
    bars = pl.DataFrame({
        "open_time": [h - timedelta(minutes=15), h, h + timedelta(minutes=15)],
        "close_time": [h, h + timedelta(minutes=15), h + timedelta(minutes=30)],
    })
    assert restrict(bars, cfg, allow_holdout=False).height == 1
    assert restrict(bars, cfg, allow_holdout=True).height == 3
    events = pl.DataFrame({"time": [h - timedelta(hours=8), h]})
    assert restrict(events, cfg, allow_holdout=False).height == 1
    old = pl.DataFrame({"time": [datetime(2019, 12, 31, tzinfo=UTC)]})
    assert restrict(old, cfg, allow_holdout=True).height == 0
