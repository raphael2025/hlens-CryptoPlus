"""V9 event loop on the 15m clock (research plan §2, D1, D2).

Order of events inside 15m bar j:
  1. a signal from bar j-1 fills at open[j] (slippage + fee); cancelled if the open is already past the stop
  2. the stop is checked against the bar's range; a gap through the stop fills at the open
  3. at close[j]: funding settles, the stop trails to newly confirmed 1H swings, the D2 reclaim
     countdown advances on 1H closes, and new entry signals are evaluated (flat only)
Every input is an as-of value visible at close[j]; nothing from bar j+1 is used.
"""

from dataclasses import dataclass, field

import numpy as np
import polars as pl

from v9.config import Config
from v9.execution import triggers_15m
from v9.regime import regime_4h
from v9.structure import structure_1h


@dataclass(frozen=True)
class Variant:
    name: str = "core"
    use_gate: bool = True
    entry: str = "bos"                       # "bos" | "sweep"
    veto: np.ndarray | None = field(default=None, compare=False)  # True blocks entries on that 15m bar


def build_frame(bars15: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    base = bars15.select("open_time", "close_time", "open", "high", "low", "close").sort("close_time")
    reg = regime_4h(bars15, cfg).select(
        pl.col("visible_at").alias("reg_visible_at"), pl.col("state").alias("regime"),
        pl.col("direction").alias("gate_dir"))
    st = structure_1h(bars15, cfg).rename({"visible_at": "h1_visible_at"})
    tr = triggers_15m(bars15, cfg).rename({"visible_at": "close_time"})
    f = base.join(tr, on="close_time", how="left")
    f = f.join_asof(reg, left_on="close_time", right_on="reg_visible_at", strategy="backward")
    f = f.join_asof(st, left_on="close_time", right_on="h1_visible_at", strategy="backward")
    return f.with_columns(
        pl.col("gate_dir").fill_null(0), pl.col("regime").fill_null("WARMUP"),
        pl.col("trend").fill_null(0), pl.col("setup").fill_null(0), pl.col("leg_id").fill_null(-1),
        pl.col("swing_low_idx").fill_null(-1), pl.col("swing_high_idx").fill_null(-1),
    )


def _ms(s: pl.Series) -> np.ndarray:
    return s.dt.epoch("ms").to_numpy()


CORE = Variant()


def run(frame: pl.DataFrame, funding: pl.DataFrame, cfg: Config, variant: Variant = CORE,
        trade_start=None) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Returns (trades, daily_equity). Entries are only taken on bars closing at/after trade_start."""
    c_ = cfg.costs
    fee, slip = c_.taker_fee, c_.slippage
    risk_pct, max_notional = cfg.risk.risk_per_trade, cfg.risk.max_notional_x_equity
    max_entries = cfg.risk.max_entries_per_thesis
    sbuf, smax = cfg.execution.stop_buffer_atr1h, cfg.execution.max_stop_atr1h
    tbuf, reclaim_n = cfg.exit.trail_buffer_atr1h, cfg.exit.reclaim_bars_1h

    o, h, lo, c = (frame[k].to_numpy() for k in ("open", "high", "low", "close"))
    t_open, t_close = _ms(frame["open_time"]), _ms(frame["close_time"])
    gate, trend, setup = (frame[k].to_numpy() for k in ("gate_dir", "trend", "setup"))
    leg_id, atr1 = frame["leg_id"].to_numpy(), frame["atr_1h"].to_numpy()
    leg_start = frame["leg_start"].dt.epoch("ms").fill_null(-1).to_numpy()
    sw_lo, sw_hi = frame["swing_low"].to_numpy(), frame["swing_high"].to_numpy()
    sw_lo_i, sw_hi_i = frame["swing_low_idx"].to_numpy(), frame["swing_high_idx"].to_numpy()
    h1_close = (frame["h1_visible_at"] == frame["close_time"]).fill_null(False).to_numpy()
    bos_up, bos_dn = frame["bos_up"].to_numpy(), frame["bos_down"].to_numpy()
    regime, stage, q, depth = (frame[k].to_list() for k in ("regime", "stage", "q", "depth"))
    veto = variant.veto if variant.veto is not None else np.zeros(len(frame), bool)
    f_t, f_r = _ms(funding["time"]), funding["rate"].to_numpy()
    start_ms = int(trade_start.timestamp() * 1000) if trade_start else t_close[0]

    equity = 1.0
    pos = 0
    trades, daily = [], []
    entries_on_leg: dict[int, int] = {}
    dead_legs: set[int] = set()
    pending = None
    fp = int(np.searchsorted(f_t, t_close[0], side="right"))
    sweep_leg, sweep_bar, reclaimed = -2, -10**9, False
    tr: dict = {}

    def close_trade(j: int, px: float, reason: str) -> None:
        nonlocal equity, pos
        exit_px = px * (1 - slip * pos)
        exit_fee = tr["qty"] * exit_px * fee
        pnl = tr["qty"] * (exit_px - tr["entry"]) * pos
        equity += pnl - exit_fee
        tr.update(exit_time=int(t_close[j] if reason != "STOP" else t_open[j]), exit=exit_px, reason=reason,
                  fees=tr["fees"] + exit_fee)
        tr["r"] = (pnl - tr["fees"] - tr["funding"]) / tr["risk"]
        if reason == "STOP":
            tr["reason"] = "STOP_LOSS" if tr["r"] < 0 else "STOP_PROFIT"
        if tr["reason"] == "THESIS":
            dead_legs.add(tr["leg"])
        trades.append(dict(tr))
        pos = 0

    for j in range(len(frame)):
        # 1. fill
        if pending is not None:
            d, stop, leg, meta = pending
            pending = None
            fill = o[j] * (1 + slip * d)
            dist = (fill - stop) * d
            risk_amt = equity * risk_pct
            if dist > 0:
                qty = risk_amt / dist
                if qty * fill <= max_notional * equity:
                    entry_fee = qty * fill * fee
                    equity -= entry_fee
                    pos = d
                    entries_on_leg[leg] = entries_on_leg.get(leg, 0) + 1
                    tr = dict(entry_time=int(t_open[j]), dir=d, entry=fill, stop_init=stop, stop=stop,
                              qty=qty, risk=risk_amt, fees=entry_fee, funding=0.0, leg=leg,
                              entry_no=entries_on_leg[leg],
                              trailed=False, mfe=0.0, mae=0.0, d2_level=np.nan, d2_count=-1,
                              last_swing=int((sw_lo_i if d == 1 else sw_hi_i)[j - 1]), **meta)
        # 2. stop inside the bar
        if pos:
            d = pos
            fav = (h[j] - tr["entry"]) if d == 1 else (tr["entry"] - lo[j])
            adv = (tr["entry"] - lo[j]) if d == 1 else (h[j] - tr["entry"])
            unit = abs(tr["entry"] - tr["stop_init"])
            tr["mfe"], tr["mae"] = max(tr["mfe"], fav / unit), max(tr["mae"], adv / unit)
            hit = lo[j] <= tr["stop"] if d == 1 else h[j] >= tr["stop"]
            if hit:
                gap = o[j] < tr["stop"] if d == 1 else o[j] > tr["stop"]
                close_trade(j, o[j] if gap else tr["stop"], "STOP")
        # 3. close of bar j
        while fp < f_t.size and f_t[fp] <= t_close[j]:
            if pos and f_t[fp] > tr["entry_time"]:
                pay = tr["qty"] * c[j] * f_r[fp] * pos
                equity -= pay
                tr["funding"] += pay
            fp += 1
        if pos:
            d = pos
            idx_now = int((sw_lo_i if d == 1 else sw_hi_i)[j])
            if idx_now != tr["last_swing"] and idx_now >= 0:
                tr["last_swing"] = idx_now
                level = (sw_lo if d == 1 else sw_hi)[j]
                new_stop = level - d * tbuf * atr1[j]
                if (new_stop - tr["stop"]) * d > 0:
                    tr["stop"], tr["trailed"] = new_stop, True
            if h1_close[j]:
                key = (sw_lo if d == 1 else sw_hi)[j]
                if tr["d2_count"] < 0:
                    if np.isfinite(key) and (c[j] - key) * d < 0:
                        tr["d2_level"], tr["d2_count"] = key, 0
                elif (c[j] - tr["d2_level"]) * d >= 0:
                    tr["d2_count"] = -1
                else:
                    tr["d2_count"] += 1
                    if tr["d2_count"] >= reclaim_n:
                        close_trade(j, c[j], "THESIS")
        # sweep bookkeeping (variant "sweep"): wick through the prior swing, close back within 4 bars
        if setup[j] != 0 and leg_id[j] != sweep_leg:
            sweep_leg, sweep_bar, reclaimed = leg_id[j], -10**9, False
        if setup[j] != 0:
            d = setup[j]
            level = sw_lo[j] if d == 1 else sw_hi[j]
            if (lo[j] < level) if d == 1 else (h[j] > level):
                sweep_bar = j
            if j - sweep_bar <= 4 and ((c[j] > level) if d == 1 else (c[j] < level)):
                reclaimed = True
        # entry signal
        if pos == 0 and t_close[j] >= start_ms and not veto[j]:
            d = gate[j] if variant.use_gate else trend[j]
            trig = bos_up[j] if d == 1 else bos_dn[j] if d == -1 else False
            leg = int(leg_id[j])
            if (d != 0 and trend[j] == d and setup[j] == d and trig and leg not in dead_legs
                    and entries_on_leg.get(leg, 0) < max_entries
                    and (variant.entry == "bos" or reclaimed) and np.isfinite(atr1[j])):
                k0 = int(np.searchsorted(t_open, leg_start[j]))
                extreme = lo[k0:j + 1].min() if d == 1 else h[k0:j + 1].max()
                stop = extreme - d * sbuf * atr1[j]
                dist = (c[j] - stop) * d
                if 0 < dist <= smax * atr1[j]:
                    pending = (d, stop, leg, dict(signal_time=int(t_close[j]), regime=regime[j],
                                                  stage=stage[j], q=q[j], depth=depth[j], atr_1h=atr1[j]))
        # daily mark-to-market at 00:00 UTC
        if t_close[j] % 86_400_000 == 0 and t_close[j] >= start_ms:
            mtm = equity + (tr["qty"] * (c[j] - tr["entry"]) * pos if pos else 0.0)
            daily.append((int(t_close[j]), mtm))

    if pos:
        close_trade(len(frame) - 1, c[-1], "END")
    cols = ["entry_time", "exit_time", "signal_time", "dir", "entry", "exit", "stop_init", "stop", "qty",
            "risk", "fees", "funding", "r", "reason", "mfe", "mae", "leg", "entry_no", "regime", "stage",
            "q", "depth", "atr_1h"]
    if not trades:
        tdf = pl.DataFrame(schema=cols)
    else:
        rows = [{k: t.get(k) for k in cols} for t in trades]
        tdf = pl.DataFrame(rows, schema_overrides={"q": pl.Float64, "depth": pl.Float64}).with_columns(
            pl.from_epoch(pl.col(k), time_unit="ms").dt.replace_time_zone("UTC")
            for k in ("entry_time", "exit_time", "signal_time"))
    ddf = pl.DataFrame(daily, schema=["time", "equity"], orient="row").with_columns(
        pl.from_epoch("time", time_unit="ms").dt.replace_time_zone("UTC"))
    return tdf, ddf
