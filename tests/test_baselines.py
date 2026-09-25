from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from tests.conftest import make_bars15
from v9.baselines import b1_positions, daily_with_funding, strategy_returns
from v9.config import load_config
from v9.lookahead import assert_truncation_invariant, evenly_spaced_cuts


def _funding(start, days, rate=0.0001):
    t = [start + timedelta(hours=8 * i) for i in range(1, 3 * days + 1)]
    return pl.DataFrame({"time": t, "interval_hours": [8] * len(t), "rate": [rate] * len(t)}).with_columns(
        pl.col("time").dt.cast_time_unit("ms"))


def test_funding_assigned_to_the_day_it_is_paid_in():
    start = datetime(2021, 1, 1, tzinfo=UTC)
    bars = make_bars15(np.full(96 * 3, 100.0), start)
    d = daily_with_funding(bars, _funding(start, 3))
    # settlements at 08:00, 16:00 and the next 00:00 all fall inside day 1's holding interval
    assert d["funding"].to_list() == pytest.approx([0.0003] * 3)


def test_long_pays_positive_funding_and_costs():
    cfg = load_config()
    start = datetime(2021, 1, 1, tzinfo=UTC)
    d = daily_with_funding(make_bars15(np.full(96 * 4, 100.0), start), _funding(start, 4))
    r = strategy_returns(d, np.ones(d.height), cfg)
    cost = cfg.costs.taker_fee + cfg.costs.slippage
    assert r[0] == 0 and r[1] == pytest.approx(-0.0003 - cost) and r[2] == pytest.approx(-0.0003)


def test_b1_long_only_never_short(random_walk_bars15):
    cfg = load_config()
    d = daily_with_funding(random_walk_bars15, _funding(random_walk_bars15["open_time"][0], 120))
    pos = b1_positions(d, cfg, 20, long_short=False)["position"]
    assert pos.min() >= 0 and pos.max() <= cfg.baselines.max_leverage


def test_b1_no_lookahead(random_walk_bars15):
    cfg = load_config()
    f = _funding(random_walk_bars15["open_time"][0], 120)

    def fn(bars):
        return b1_positions(daily_with_funding(bars, f), cfg, 20, long_short=True)

    assert_truncation_invariant(fn, random_walk_bars15, evenly_spaced_cuts(random_walk_bars15))
