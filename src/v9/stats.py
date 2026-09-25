"""Statistics used to accept or reject a rule (research plan §3)."""

import math
from statistics import NormalDist

import numpy as np

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def bootstrap_mean_ci(x, n_boot: int = 10_000, alpha: float = 0.05, seed: int = 0):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return math.nan, math.nan, math.nan
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n_boot, x.size), replace=True).mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(x.mean()), float(lo), float(hi)


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return math.nan, math.nan, math.nan
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, c - h, c + h


def t_stat(x) -> float:
    x = np.asarray(x, dtype=float)
    if x.size < 2 or x.std(ddof=1) == 0:
        return math.nan
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(x.size)))


def skewness(x) -> float:
    x = np.asarray(x, dtype=float)
    s = x.std()
    return float(((x - x.mean()) ** 3).mean() / s**3) if s > 0 else math.nan


def kurtosis(x) -> float:
    """Non-excess kurtosis (normal = 3)."""
    x = np.asarray(x, dtype=float)
    s = x.std()
    return float(((x - x.mean()) ** 4).mean() / s**4) if s > 0 else math.nan


def longest_losing_streak(r) -> int:
    best = cur = 0
    for v in r:
        cur = cur + 1 if v <= 0 else 0
        best = max(best, cur)
    return best


def mc_losing_streak(win_rate: float, n_trades: int, sims: int = 100_000, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    losses = rng.random((sims, n_trades)) >= win_rate
    longest = np.zeros(sims, dtype=int)
    cur = np.zeros(sims, dtype=int)
    for j in range(n_trades):
        cur = np.where(losses[:, j], cur + 1, 0)
        longest = np.maximum(longest, cur)
    q = np.quantile(longest, [0.5, 0.9, 0.95, 0.99])
    return {"p50": int(q[0]), "p90": int(q[1]), "p95": int(q[2]), "p99": int(q[3])}


def max_drawdown(equity) -> float:
    e = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(e)
    return float(((e - peak) / peak).min())


def mc_drawdown_r(r, sims: int = 20_000, seed: int = 0) -> dict:
    """Max drawdown in R when the trade order is resampled with replacement."""
    r = np.asarray(r, dtype=float)
    rng = np.random.default_rng(seed)
    paths = np.cumsum(rng.choice(r, size=(sims, r.size), replace=True), axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((sims, 1)), paths], axis=1), axis=1)[:, 1:]
    dd = (paths - peak).min(axis=1)
    q = np.quantile(dd, [0.5, 0.05, 0.01])
    return {"p50": float(q[0]), "p95": float(q[1]), "p99": float(q[2])}


def sharpe(returns, periods_per_year: int = 365) -> float:
    r = np.asarray(returns, dtype=float)
    if r.size < 2 or r.std(ddof=1) == 0:
        return math.nan
    return float(r.mean() / r.std(ddof=1) * math.sqrt(periods_per_year))


def probabilistic_sharpe(returns, sr_benchmark: float = 0.0) -> float:
    """PSR (Bailey & López de Prado 2012) with per-period Sharpe ratios."""
    r = np.asarray(returns, dtype=float)
    if r.size < 3 or r.std(ddof=1) == 0:
        return math.nan
    sr = r.mean() / r.std(ddof=1)
    g3, g4 = skewness(r), kurtosis(r)
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr * sr))
    return _N.cdf((sr - sr_benchmark) * math.sqrt(r.size - 1) / denom)


def expected_max_sharpe(sr_variance: float, n_trials: int) -> float:
    """Expected maximum per-period Sharpe among n independent trials with true SR 0 (DSR benchmark)."""
    if n_trials < 2:
        return 0.0
    return math.sqrt(sr_variance) * (
        (1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
        + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe(returns, trial_sharpes) -> float:
    """DSR (Bailey & López de Prado 2014): PSR against the best Sharpe expected by luck across trials."""
    trial_sharpes = np.asarray(trial_sharpes, dtype=float)
    bench = expected_max_sharpe(float(trial_sharpes.var(ddof=1)), trial_sharpes.size)
    return probabilistic_sharpe(returns, bench)


def summarize_r(r) -> dict:
    """Trade-level summary in R. Percentages are withheld (None) when n < 30."""
    r = np.asarray(r, dtype=float)
    n = int(r.size)
    wins = r[r > 0]
    mean, lo, hi = bootstrap_mean_ci(r)
    p, plo, phi = wilson(int(wins.size), n)
    gross = wins.sum()
    top = np.sort(r)[::-1][: max(1, math.ceil(n / 10))] if n else np.array([])
    show_pct = n >= 30
    return {
        "n": n,
        "expectancy_r": mean, "expectancy_ci": (lo, hi), "t": t_stat(r),
        "total_r": float(r.sum()),
        "win_rate": p if show_pct else None, "win_rate_ci": (plo, phi) if show_pct else None,
        "avg_win_r": float(wins.mean()) if wins.size else math.nan,
        "avg_loss_r": float(r[r <= 0].mean()) if (r <= 0).any() else math.nan,
        "skew": skewness(r) if n > 2 else math.nan,
        "top10_share_of_gross": float(top[top > 0].sum() / gross) if gross > 0 and show_pct else None,
        "longest_losing_streak": longest_losing_streak(r),
        "insufficient_sample": n < 100,
    }
