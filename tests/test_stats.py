import math

import numpy as np
import pytest

from v9 import stats


def test_wilson_known_value():
    p, lo, hi = stats.wilson(5, 10)
    assert p == 0.5
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)


def test_bootstrap_ci_brackets_mean():
    x = np.random.default_rng(1).normal(0.2, 1, 500)
    m, lo, hi = stats.bootstrap_mean_ci(x)
    assert lo < m < hi and hi - lo < 0.25


def test_longest_losing_streak():
    assert stats.longest_losing_streak([1, -1, -1, 0, 2, -1]) == 3


def test_mc_streak_matches_reference_numbers():
    # docs/reports/2026-09-25-v9-layer-evidence.md §2: win 30%, 100 trades -> P50 10, P95 17
    q = stats.mc_losing_streak(0.30, 100, sims=50_000)
    assert q["p50"] == 10 and 16 <= q["p95"] <= 18


def test_max_drawdown():
    assert stats.max_drawdown([100, 120, 90, 130, 65]) == pytest.approx(-0.5)


def test_psr_orders_by_quality():
    rng = np.random.default_rng(3)
    good = rng.normal(0.002, 0.01, 1000)
    bad = rng.normal(0.0, 0.01, 1000)
    assert stats.probabilistic_sharpe(good) > 0.95 > stats.probabilistic_sharpe(bad)


def test_dsr_penalises_many_trials():
    rng = np.random.default_rng(4)
    r = rng.normal(0.001, 0.01, 1000)
    few = stats.deflated_sharpe(r, rng.normal(0, 0.01, 2))
    many = stats.deflated_sharpe(r, rng.normal(0, 0.03, 200))
    assert many < few


def test_summary_withholds_percentages_below_30():
    s = stats.summarize_r([2, -1, -1, 3, -1])
    assert s["n"] == 5 and s["win_rate"] is None and s["insufficient_sample"]
    s = stats.summarize_r([3, -1, -1] * 20)
    assert s["win_rate"] == pytest.approx(1 / 3) and not math.isnan(s["expectancy_r"])
