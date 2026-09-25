import numpy as np

from tests.conftest import make_bars15
from v9.config import load_config
from v9.lookahead import assert_truncation_invariant, evenly_spaced_cuts
from v9.structure import structure_1h
from v9.swings import atr, directional_change


def _dc(close, th=0.05):
    close = np.asarray(close, float)
    # threshold th = mult * atr / close with mult 1 and atr = th * close
    return directional_change(close, close, close, th * close, 1.0)


def test_swing_confirmed_only_after_threshold_retrace():
    sw = _dc([100, 105, 110, 108, 106, 104, 110, 120])
    # high 110 at index 2; 104 <= 110 * 0.95 = 104.5 first at index 5
    assert np.isnan(sw.sh[4]) and sw.sh[5] == 110 and sw.sh_idx[5] == 2
    assert sw.mode[4] == 1 and sw.mode[5] == -1


def test_bos_trend_and_stage():
    closes = [100, 110, 104, 103, 111, 105, 104, 115, 108, 107, 99, 98]
    sw = _dc(closes)
    ups, downs = np.flatnonzero(sw.bos_up), np.flatnonzero(sw.bos_down)
    assert ups.tolist() == [4, 7]          # breaks of the 110 and 111 highs
    assert downs.tolist() == [10]          # close 99 breaks the 103 low -> CHOCH
    assert sw.trend[7] == 1 and sw.stage[7] == 2
    assert sw.trend[10] == -1 and sw.stage[10] == 1


def test_atr_simple_mean_of_true_range():
    h = np.array([10.0, 12, 11, 13])
    lo = np.array([9.0, 10, 10, 11])
    c = np.array([9.5, 11, 10.5, 12])
    a = atr(h, lo, c, 2)
    assert np.isnan(a[0]) and a[1] == (1 + 2.5) / 2 and a[3] == (1 + 2.5) / 2


def _zigzag_bars15(cycles=12, up=0.08, down=0.035, up_h=40, down_h=20):
    q = 4
    steps = []
    for _ in range(cycles):
        steps.append(np.full(up_h * q, np.log(1 + up) / (up_h * q)))
        steps.append(np.full(down_h * q, np.log(1 - down) / (down_h * q)))
    return make_bars15(30000 * np.exp(np.cumsum(np.concatenate(steps))), spread=0.0005)


def test_uptrend_zigzag_produces_long_setups_in_corrections():
    s = structure_1h(_zigzag_bars15(), load_config())
    late = s.tail(400)
    assert (late["trend"] == 1).all()
    setups = late.filter(late["setup"] == 1)
    assert setups.height > 0 and (setups["setup"] == 1).all()
    assert (late["setup"] != -1).all()
    # during a setup the correction low is below the anchoring swing high
    assert (setups["ext"] < setups["swing_high"]).all()
    assert setups["q"].is_not_null().all() and (setups["depth"] > 0).all()


def test_structure_no_lookahead(random_walk_bars15):
    cfg = load_config()
    bars = random_walk_bars15
    assert_truncation_invariant(lambda b: structure_1h(b, cfg), bars, evenly_spaced_cuts(bars))
