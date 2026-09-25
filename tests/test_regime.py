from dataclasses import replace

import numpy as np
import pytest

from tests.conftest import make_bars15
from v9.config import load_config
from v9.lookahead import assert_truncation_invariant, evenly_spaced_cuts
from v9.regime import regime_4h

QUARTERS_PER_4H = 16


@pytest.fixture
def small_cfg():
    cfg = load_config()
    return replace(cfg, regime=replace(cfg.regime, slow_bars=30, fast_bars=6, er_bars=6,
                                       chop_lookback_bars=120, chop_min_bars=60, transition_bars=6))


def _path(segments):
    """Piecewise-linear log path; each segment = (4H bars, total log move)."""
    q = QUARTERS_PER_4H
    steps = np.concatenate([np.full(n * q, move / (n * q)) for n, move in segments])
    rng = np.random.default_rng(0)
    return 30000 * np.exp(np.cumsum(steps + rng.normal(0, 0.0008, steps.size)))


def test_steady_uptrend_is_trend_long(small_cfg):
    r = regime_4h(make_bars15(_path([(400, 1.5)])), small_cfg)
    tail = r.tail(100)
    assert (tail["state"] == "TREND").mean() > 0.6
    assert set(tail.filter(tail["state"] == "TREND")["direction"].to_list()) == {1}


def test_pullback_inside_uptrend_keeps_long_direction(small_cfg):
    r = regime_4h(make_bars15(_path([(200, 1.0), (20, -0.06), (40, 0.2)])), small_cfg)
    pull = r.filter(r["state"] == "PULLBACK")
    assert pull.height > 0 and set(pull["direction"].to_list()) == {1}


def test_reversal_is_transition_before_short(small_cfg):
    r = regime_4h(make_bars15(_path([(200, 1.0), (200, -1.0)])), small_cfg)
    states = r["state"].to_list()
    first_short = next(i for i, d in enumerate(r["direction"].to_list()) if d == -1)
    assert "TRANSITION" in states[first_short - small_cfg.regime.transition_bars:first_short]


def test_warmup_is_not_tradable(small_cfg):
    r = regime_4h(make_bars15(_path([(100, 0.5)])), small_cfg)
    assert (r.head(small_cfg.regime.chop_min_bars - 1)["direction"] == 0).all()


def test_regime_no_lookahead(small_cfg, random_walk_bars15):
    bars = random_walk_bars15
    assert_truncation_invariant(lambda b: regime_4h(b, small_cfg), bars, evenly_spaced_cuts(bars))
