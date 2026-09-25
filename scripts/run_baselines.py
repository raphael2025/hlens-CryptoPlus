"""R2: baselines B0 / B1 on the development set (costs and funding on).

    uv run python scripts/run_baselines.py
"""

import json
from datetime import UTC, datetime

import numpy as np

from v9.baselines import b1_positions, daily_with_funding, evaluate, strategy_returns
from v9.config import ROOT, load_config
from v9.data.store import load

PERIODS = [
    ("dev", None, None),
    ("2020-09..2021", datetime(2020, 9, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)),
    ("2022", datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
    ("2023", datetime(2023, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
]


def main() -> None:
    cfg = load_config()
    daily = daily_with_funding(load("um_15m", cfg), load("funding", cfg))
    runs = {"B0 buy&hold": np.ones(daily.height)}
    for w in cfg.baselines.ma_windows:
        for ls in (False, True):
            name = f"B1 MA{w} {'long-short' if ls else 'long-only'}"
            runs[name] = b1_positions(daily, cfg, w, ls)["position"].to_numpy()

    out = {}
    head = ("strategy", "period", "days", "Sharpe", "CAGR", "MaxDD", "vol")
    print("{:26s} {:14s} {:>5s} {:>7s} {:>8s} {:>8s} {:>6s}".format(*head))
    for name, pos in runs.items():
        r = strategy_returns(daily, pos, cfg)
        for label, a, b in PERIODS:
            e = evaluate(daily, r, a or cfg.data.dev_start, b or cfg.data.holdout_start)
            print(f"{name:26s} {label:14s} {e['days']:5d} {e['sharpe']:7.2f} {e['cagr']:8.1%} "
                  f"{e['max_dd']:8.1%} {e['vol']:6.1%}")
            if label == "dev":
                out[name] = e["returns"].tolist()
    dest = ROOT / "data" / "results"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "baseline_daily_returns.json").write_text(json.dumps(out))


if __name__ == "__main__":
    main()
