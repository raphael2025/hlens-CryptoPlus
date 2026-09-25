from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from v9.config import load_config
from v9.lookahead import assert_truncation_invariant, evenly_spaced_cuts
from v9.swing import build_swing_frame, find_b_signals, run_exits

T0 = datetime(2021, 1, 1, tzinfo=UTC)
NO_FUNDING = pl.DataFrame({"time": [T0 - timedelta(days=1)], "rate": [0.0]}).with_columns(
    pl.col("time").dt.cast_time_unit("ms"))


@pytest.fixture
def cfg():
    c = load_config()
    return replace(c, costs=replace(c.costs, taker_fee=0.0, slippage=0.0))


def signal_frame(opens, highs, lows, closes, d4=1, atr=3.0, level=101.0):
    n = len(closes)
    return pl.DataFrame({
        "open": np.asarray(opens, float), "high": np.asarray(highs, float), "low": np.asarray(lows, float),
        "close": np.asarray(closes, float), "dir4": np.full(n, d4, dtype=np.int8), "atr_1h": np.full(n, atr),
        "tunnel_top": np.full(n, level + 20), "tunnel_bottom": np.full(n, level),
        "ema55_4h": np.full(n, 50.0),
        "sw_lo_1h": np.full(n, np.nan), "sw_hi_1h": np.full(n, np.nan),
    })


DROP = dict(opens=[110, 109, 108, 107, 106, 105, 104, 103],
            highs=[110, 109, 108, 107, 106, 105, 104, 103],
            lows=[109, 108, 107, 106, 105, 104, 103, 100],
            closes=[109, 108, 107, 106, 105, 104, 103, 102])


def test_fast_drop_to_key_level_with_wick_is_a_signal(cfg):
    s = find_b_signals(signal_frame(**DROP), cfg)
    assert s["bar"].to_list() == [7] and s["dir"][0] == 1
    assert s["key"][0] == 101 and s["stop_s1"][0] == pytest.approx(100 - 0.25 * 3)


def test_no_signal_without_trend_wick_or_fast_drop(cfg):
    assert find_b_signals(signal_frame(**DROP, d4=0), cfg).height == 0
    no_wick = {**DROP, "opens": DROP["opens"][:-1] + [102.5], "closes": DROP["closes"][:-1] + [101.5],
               "lows": DROP["lows"][:-1] + [100.9], "highs": DROP["highs"][:-1] + [103]}
    assert find_b_signals(signal_frame(**no_wick), cfg).height == 0   # lower wick 0.6 of a 2.1 range
    slow = {k: [102 + (x - 102) * 0.2 for x in v] for k, v in DROP.items()}  # same shape, 1/5 the drop
    assert find_b_signals(signal_frame(**slow, atr=3.0), cfg).height == 0


def engine_frame(closes, lows=None, highs=None, ema12=None, h1_every=1):
    n = len(closes)
    c = np.asarray(closes, float)
    o = np.concatenate([[c[0]], c[:-1]])
    lo = np.asarray(lows, float) if lows is not None else np.minimum(o, c) - 0.1
    hi = np.asarray(highs, float) if highs is not None else np.maximum(o, c) + 0.1
    t = [T0 + timedelta(minutes=15 * i) for i in range(n)]
    return pl.DataFrame({
        "open_time": t, "close_time": [x + timedelta(minutes=15) for x in t],
        "open": o, "high": hi, "low": lo, "close": c,
        "h1_close": np.arange(n) % h1_every == 0,
        "ema12": np.asarray(ema12 if ema12 is not None else c - 5, float),
        "atr_1h": np.full(n, 2.0), "strong4": np.zeros(n, bool),
        "sw_lo_4h": np.full(n, np.nan), "sw_lo_4h_idx": np.full(n, -1), "sw_hi_4h": np.full(n, np.nan),
        "sw_hi_4h_idx": np.full(n, -1),
    }).with_columns(pl.col("open_time", "close_time").dt.cast_time_unit("ms"))


def entry(bar=0, d=1, stop_s1=98.0, key=99.0):
    return pl.DataFrame({"bar": [bar], "dir": [d], "stop_s1": [stop_s1], "key": [key], "atr_1h": [2.0]},
                        schema_overrides={"dir": pl.Int8})


def _run(f, e, cfg, stop, ex):
    return run_exits(f, e, NO_FUNDING, cfg, stop, ex, 0, 2**62)[0]


def test_fixed_target(cfg):
    f = engine_frame([100, 100, 102, 104, 107, 107])
    t = _run(f, entry(), cfg, "S1", "E4")[0]
    assert t["reason"] == "TARGET" and t["r"] == pytest.approx(3.0)


def test_rule_a_exits_after_two_1h_closes_without_reclaim(cfg):
    # key 99, ATR 2 -> line 98; closes below it three times -> exit at the 2nd close after the first
    f = engine_frame([100, 100, 97.5, 97.6, 97.7, 97.8], lows=[99.9, 99.9, 97.4, 97.5, 97.6, 97.7])
    t = _run(f, entry(), cfg, "S2", "E1")[0]
    assert t["reason"] == "KEY_LOST" and t["r"] == pytest.approx((97.7 - 100) / 2)


def test_rule_a_reclaim_cancels_exit(cfg):
    f = engine_frame([100, 100, 97.5, 98.5, 97.9, 98.2, 99, 99])
    t = _run(f, entry(), cfg, "S2", "E1")[0]
    assert t["reason"] == "END"


def test_rule_a_catastrophe_stop_intrabar(cfg):
    f = engine_frame([100, 100, 99, 99], lows=[99.9, 99.9, 92.0, 98])  # key 99 - 3*2 = 93
    t = _run(f, entry(), cfg, "S2", "E1")[0]
    assert t["reason"] == "STOP" and t["r"] == pytest.approx((93 - 100) / 2)


def test_pullback_exit_after_one_r(cfg):
    closes = [100, 100, 101, 102.5, 101.8, 101]
    ema = [95, 95, 96, 97, 102, 102]          # bar 4 closes below EMA12 after MFE >= 1R (stop 98 -> R = 2)
    t = _run(engine_frame(closes, ema12=ema), entry(), cfg, "S1", "E2")[0]
    assert t["reason"] == "PULLBACK" and t["r"] == pytest.approx((101.8 - 100) / 2)


def test_no_pullback_exit_before_one_r(cfg):
    closes = [100, 100, 100.5, 100.2, 100.3, 100.4]
    ema = [95, 95, 101, 101, 101, 101]
    t = _run(engine_frame(closes, ema12=ema), entry(), cfg, "S1", "E2")[0]
    assert t["reason"] == "END"


def test_swing_frame_and_signals_no_lookahead(random_walk_bars15):
    cfg = load_config()
    bars = random_walk_bars15

    def fn(b):
        f = build_swing_frame(b, cfg)
        s = find_b_signals(f, cfg)
        return s.with_columns(f["close_time"].gather(s["bar"]).alias("visible_at"))

    def ctx(b):  # every 15m row of context is usable at that bar's close
        return build_swing_frame(b, cfg).rename({"close_time": "visible_at"})

    cuts = evenly_spaced_cuts(bars)
    assert_truncation_invariant(fn, bars, cuts)
    assert_truncation_invariant(ctx, bars, cuts)
