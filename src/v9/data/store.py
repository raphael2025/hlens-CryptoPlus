"""Processed datasets under data/processed, loaded with the holdout hidden by default (AGENTS.md §2.3)."""

from pathlib import Path

import polars as pl

from v9.config import ROOT, Config

RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

DATASETS = ("um_15m", "spot_15m", "premium_1h", "funding", "metrics")


def path(name: str, base: Path = PROCESSED, symbol: str | None = None) -> Path:
    """The configured symbol keeps the plain file name; other symbols get a suffix."""
    if name not in DATASETS:
        raise KeyError(name)
    return base / (f"{name}.parquet" if symbol is None else f"{name}_{symbol}.parquet")


def restrict(df: pl.DataFrame, cfg: Config, *, allow_holdout: bool) -> pl.DataFrame:
    """Keep rows from warm-up start; drop everything in the holdout unless explicitly allowed.

    A bar belongs to the dev set if it closed at or before the holdout start; an event
    (funding settlement, metrics snapshot) if it happened strictly before it.
    """
    if "close_time" in df.columns:
        df = df.filter(pl.col("open_time") >= cfg.data.warmup_start)
        if not allow_holdout:
            df = df.filter(pl.col("close_time") <= cfg.data.holdout_start)
    else:
        df = df.filter(pl.col("time") >= cfg.data.warmup_start)
        if not allow_holdout:
            df = df.filter(pl.col("time") < cfg.data.holdout_start)
    return df


def load(name: str, cfg: Config, *, allow_holdout: bool = False, base: Path = PROCESSED,
         symbol: str | None = None) -> pl.DataFrame:
    p = path(name, base, None if symbol in (None, cfg.data.symbol) else symbol)
    if not p.exists():
        raise FileNotFoundError(f"{p} missing; run `uv run python scripts/fetch_data.py` first")
    return restrict(pl.read_parquet(p), cfg, allow_holdout=allow_holdout)
