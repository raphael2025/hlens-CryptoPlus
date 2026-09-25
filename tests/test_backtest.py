from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from tests.conftest import make_bars15
from v9.backtest import Variant, build_frame, run
from v9.config import load_config

T0 = datetime(2021, 1, 1, tzinfo=UTC)
NO_FUNDING = pl.DataFrame({"time": [T0 - timedelta(days=1)], "rate": [0.0]}).with_columns(
    pl.col("time").dt.cast_time_unit("ms"))


def manual_frame(closes, lows=None, highs=None, signal_bars=(), setup=1, gate=1):
    """A frame with a long setup on every bar and a 15m BOS at `signal_bars`."""
    n = len(closes)
    c = np.asarray(closes, float)
    o = np.concatenate([[c[0]], c[:-1]])
    lo = np.asarray(lows, float) if lows is not None else np.minimum(o, c) - 1
    hi = np.asarray(highs, float) if highs is not None else np.maximum(o, c) + 1
    t = [T0 + timedelta(minutes=15 * i) for i in range(n)]
    bos = np.zeros(n, bool)
    bos[list(signal_bars)] = True
    return pl.DataFrame({
        "open_time": t, "close_time": [x + timedelta(minutes=15) for x in t],
        "open": o, "high": hi, "low": lo, "close": c,
        "gate_dir": np.full(n, gate), "trend": np.full(n, setup), "setup": np.full(n, setup),
        "leg_id": np.full(n, 7), "atr_1h": np.full(n, 10.0), "leg_start": [t[0]] * n,
        "swing_low": np.full(n, 50.0), "swing_high": np.full(n, 200.0),
        "swing_low_idx": np.full(n, 1), "swing_high_idx": np.full(n, 2), "h1_visible_at": [None] * n,
        "bos_up": bos, "bos_down": np.zeros(n, bool), "regime": ["TREND"] * n,
        "stage": np.ones(n), "q": np.ones(n), "depth": np.full(n, 0.5),
    }).with_columns(pl.col("open_time", "close_time", "leg_start").dt.cast_time_unit("ms"),
                    pl.col("h1_visible_at").cast(pl.Datetime("ms", "UTC")))


@pytest.fixture
def cfg():
    c = load_config()
    return replace(c, costs=replace(c.costs, taker_fee=0.0, slippage=0.0))


def test_entry_at_next_open_and_initial_stop(cfg):
    # correction low 95 (bar 1 low 95 = 96-1); stop = 95 - 0.25*10 = 92.5
    closes = [100, 96, 98, 99, 101, 97, 94, 90, 90]
    f = manual_frame(closes, signal_bars=[3])
    trades, _ = run(f, NO_FUNDING, cfg)
    t = trades.row(0, named=True)
    assert t["entry"] == 99 and t["stop_init"] == pytest.approx(92.5)
    assert t["reason"] == "STOP_LOSS" and t["exit"] == pytest.approx(92.5)
    assert t["r"] == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_open(cfg):
    closes = [100, 96, 98, 99, 101, 80, 80]
    f = manual_frame(closes, signal_bars=[3])
    t = run(f, NO_FUNDING, cfg)[0].row(0, named=True)
    assert t["exit"] == pytest.approx(92.5)  # bar 5 opens at 101, above the stop: fills at the stop

    lows = [99, 95, 97, 98, 100, 79, 79]
    f = manual_frame(closes, lows=lows, signal_bars=[3])
    f = f.with_columns(pl.Series("open", [100, 96, 98, 99, 101, 85, 80], dtype=pl.Float64))
    t = run(f, NO_FUNDING, cfg)[0].row(0, named=True)
    assert t["exit"] == 85 and t["r"] == pytest.approx((85 - 101) / (101 - 92.5))  # filled at open 101


def test_costs_make_a_stop_worse_than_minus_one_r():
    c = load_config()
    closes = [100, 96, 98, 99, 101, 97, 94, 90, 90]
    t = run(manual_frame(closes, signal_bars=[3]), NO_FUNDING, c)[0].row(0, named=True)
    assert t["r"] < -1.0


def test_long_pays_funding(cfg):
    closes = [100, 96, 98, 99] + [100] * 40
    f = manual_frame(closes, signal_bars=[3])
    funding = pl.DataFrame({"time": [T0 + timedelta(hours=8)], "rate": [0.001]}).with_columns(
        pl.col("time").dt.cast_time_unit("ms"))
    t = run(f, funding, cfg)[0].row(0, named=True)
    assert t["reason"] == "END" and t["funding"] == pytest.approx(t["qty"] * 100 * 0.001)


def test_at_most_three_entries_per_thesis(cfg):
    closes, signals = [], []
    for _ in range(5):  # five dips to the stop, each preceded by a signal
        base = len(closes)
        closes += [100, 96, 98, 99, 101, 90]
        signals.append(base + 3)
    f = manual_frame(closes, lows=np.asarray(closes) - 1, signal_bars=signals)
    # same thesis (leg_id), but each dip measures its correction low from its own start
    starts = [f["open_time"][6 * (i // 6)] for i in range(len(closes))]
    f = f.with_columns(pl.Series("leg_start", starts))
    trades, _ = run(f, NO_FUNDING, cfg)
    assert trades.height == 3 and trades["entry_no"].to_list() == [1, 2, 3]


def test_no_entry_against_gate_or_without_gate(cfg):
    closes = [100, 96, 98, 99, 101, 97, 94, 90, 90]
    assert run(manual_frame(closes, signal_bars=[3], gate=-1), NO_FUNDING, cfg)[0].height == 0
    assert run(manual_frame(closes, signal_bars=[3], gate=0), NO_FUNDING, cfg)[0].height == 0
    assert run(manual_frame(closes, signal_bars=[3], gate=0), NO_FUNDING, cfg,
               Variant(use_gate=False))[0].height == 1


def test_veto_blocks_entry(cfg):
    closes = [100, 96, 98, 99, 101, 97, 94, 90, 90]
    veto = np.zeros(len(closes), bool)
    veto[3] = True
    assert run(manual_frame(closes, signal_bars=[3]), NO_FUNDING, cfg, Variant(veto=veto))[0].height == 0


def test_backtest_no_lookahead():
    """Trades closed before a cut are identical whether or not later bars exist."""
    c = load_config()
    c = replace(c, regime=replace(c.regime, slow_bars=30, fast_bars=6, er_bars=6, chop_lookback_bars=120,
                                  chop_min_bars=60, transition_bars=6))
    rng = np.random.default_rng(11)
    bars = make_bars15(30000 * np.exp(np.cumsum(rng.normal(0.00005, 0.004, 96 * 240))))
    funding = NO_FUNDING
    full, _ = run(build_frame(bars, c), funding, c)
    assert full.height > 5
    for cut_day in (120, 180):
        cut = bars["close_time"][96 * cut_day]
        part, _ = run(build_frame(bars.filter(pl.col("close_time") <= cut), c), funding, c)
        a = full.filter(pl.col("exit_time") < cut)
        b = part.filter(pl.col("exit_time") < cut)
        assert a.height > 0 and a.equals(b)
