import numpy as np
import polars as pl
import pytest

from v9.config import load_config
from v9.entry_study import forward_metrics
from v9.lookahead import assert_truncation_invariant, evenly_spaced_cuts
from v9.trader import build_trader_frame, find_signals, frame_1h, frame_4h


def sweep_frame(lows, closes, bos_up, d4=1, s1=1, level=100.0):
    n = len(lows)
    lows, closes = np.asarray(lows, float), np.asarray(closes, float)
    return pl.DataFrame({
        "low": lows, "high": np.maximum(lows, closes) + 1, "close": closes,
        "dir_4h": np.full(n, d4), "setup_1h": np.full(n, s1), "pullback_4h": np.full(n, True),
        "tunnel_bottom": np.full(n, level), "tunnel_top": np.full(n, level + 5),
        "swing_low": np.full(n, np.nan), "swing_high": np.full(n, np.nan), "atr_1h": np.full(n, 4.0),
        "bos_up": np.asarray(bos_up, bool), "bos_down": np.zeros(n, bool),
    })


def test_sweep_reclaim_then_bos_gives_one_signal():
    f = sweep_frame(lows=[101, 99, 99.5, 100.5, 101], closes=[102, 99.5, 100.5, 101, 102],
                    bos_up=[False, False, False, True, True])
    s = find_signals(f, load_config())
    assert s["bar"].to_list() == [3]
    assert s["stop"][0] == pytest.approx(99 - 0.25 * 4)


@pytest.mark.parametrize("lows,closes,bos,why", [
    ([101, 102, 103], [102, 103, 104], [False, False, True], "no sweep"),
    ([101, 99, 98, 97, 96, 99, 99], [102, 99, 98, 97, 96.5, 99.5, 101], [0, 0, 0, 0, 0, 0, 1],
     "held below the level for 4+ bars: acceptance, not a sweep"),
    ([101, 99, 99.5], [102, 99.5, 99.8], [False, True, False], "BOS before reclaim"),
])
def test_no_signal_cases(lows, closes, bos, why):
    assert find_signals(sweep_frame(lows, closes, bos), load_config()).height == 0, why


def test_no_signal_without_4h_direction_or_1h_setup():
    args = dict(lows=[101, 99, 99.5, 100.5], closes=[102, 99.5, 100.5, 101], bos_up=[0, 0, 0, 1])
    assert find_signals(sweep_frame(**args, d4=0), load_config()).height == 0
    assert find_signals(sweep_frame(**args, s1=0), load_config()).height == 0


def test_forward_metrics_first_passage():
    o = np.array([100.0, 100, 100, 100, 100, 100, 100, 100])
    h = np.array([100.5, 101, 102.1, 103.2, 103, 103, 103, 103])
    lo = np.array([99.5, 99.8, 100, 101, 101, 101, 101, 101])
    c = np.array([100.2, 100.8, 102, 103, 103, 103, 103, 103])
    m = forward_metrics(o, h, lo, c, 0, 1, 1.0, 1.0, horizons_h=[1], passage_r=[2, 3], window_h=2)
    assert m["win_2r"] and m["win_3r"] and not m["loss_2r"]
    assert m["mae_r"] == pytest.approx(0.5) and m["ret_1h_atr"] == pytest.approx(3.0)  # close[3]
    m = forward_metrics(o, h, lo, c, 0, -1, 1.0, 1.0, horizons_h=[1], passage_r=[2], window_h=2)
    assert m["loss_2r"] and not m["win_2r"]


def test_trader_frames_no_lookahead(random_walk_bars15):
    cfg = load_config()
    bars = random_walk_bars15
    cuts = evenly_spaced_cuts(bars)
    assert_truncation_invariant(lambda b: frame_4h(b, cfg), bars, cuts)
    assert_truncation_invariant(lambda b: frame_1h(b, cfg), bars, cuts)

    def signals(b):
        f = build_trader_frame(b, cfg)
        s = find_signals(f, cfg)
        return s.with_columns(f["close_time"].gather(s["bar"]).alias("visible_at"))

    assert_truncation_invariant(signals, bars, cuts)
