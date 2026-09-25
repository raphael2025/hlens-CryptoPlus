from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "v9.yaml"


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


@dataclass(frozen=True)
class Data:
    symbol: str
    warmup_start: datetime
    dev_start: datetime
    holdout_start: datetime


@dataclass(frozen=True)
class Regime:
    slow_bars: int
    fast_bars: int
    er_bars: int
    chop_quantile: float
    chop_lookback_bars: int
    chop_min_bars: int
    transition_bars: int


@dataclass(frozen=True)
class Structure:
    atr_bars: int
    dc_atr_mult: float


@dataclass(frozen=True)
class Execution:
    atr_bars: int
    dc_atr_mult: float
    stop_buffer_atr1h: float
    max_stop_atr1h: float


@dataclass(frozen=True)
class Risk:
    risk_per_trade: float
    max_notional_x_equity: float
    max_entries_per_thesis: int


@dataclass(frozen=True)
class Exit:
    reclaim_bars_1h: int
    trail_buffer_atr1h: float


@dataclass(frozen=True)
class Costs:
    taker_fee: float
    slippage: float


@dataclass(frozen=True)
class Hypotheses:
    h9_max_stage: int
    h10_q_min: float
    h10_depth_max: float


@dataclass(frozen=True)
class Baselines:
    ma_windows: list[int]
    vol_window: int
    vol_target: float
    max_leverage: float


@dataclass(frozen=True)
class Config:
    version: str
    data: Data
    regime: Regime
    structure: Structure
    execution: Execution
    risk: Risk
    exit: Exit
    costs: Costs
    hypotheses: Hypotheses
    baselines: Baselines


def _build(cls, raw: dict):
    names = {f.name for f in fields(cls)}
    if set(raw) != names:
        raise ValueError(f"{cls.__name__}: expected keys {sorted(names)}, got {sorted(raw)}")
    return cls(**raw)


def load_config(path: Path = DEFAULT_CONFIG) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    d = raw["data"]
    data = Data(
        symbol=d["symbol"],
        warmup_start=_ts(d["warmup_start"]),
        dev_start=_ts(d["dev_start"]),
        holdout_start=_ts(d["holdout_start"]),
    )
    return Config(
        version=raw["version"],
        data=data,
        regime=_build(Regime, raw["regime"]),
        structure=_build(Structure, raw["structure"]),
        execution=_build(Execution, raw["execution"]),
        risk=_build(Risk, raw["risk"]),
        exit=_build(Exit, raw["exit"]),
        costs=_build(Costs, raw["costs"]),
        hypotheses=_build(Hypotheses, raw["hypotheses"]),
        baselines=_build(Baselines, raw["baselines"]),
    )
