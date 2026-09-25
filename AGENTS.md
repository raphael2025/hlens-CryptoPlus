# AGENTS.md — rules for any coding agent (Cursor, Codex, Claude) working in this repo

Read this before touching anything. The repository was reset on 2026-09-25 and now holds a **BTC perpetual-futures trend-trading research project**. Take a task id (`R0` … `R9`) from `docs/02-RESEARCH-PLAN.md` §4 and put it in your branch name and PR title.

## 1. What this project is
A research framework, not a trading bot. It turns raphael's V9 framework into falsifiable rules, backtests them on historical data net of costs, and accepts or rejects each part with statistics. Read, in this order:
- `docs/01-FRAMEWORK-V9.md` — raphael's framework. **Its content changes only by raphael's decision.**
- `docs/02-RESEARCH-PLAN.md` — the decisions, the frozen v1 rules, baselines, task order, stop rules and pre-registered hypotheses. If you think a decision is wrong, say so in the PR instead of quietly doing something else.
- `docs/reports/` — evidence reviews and data feasibility. Check here before assuming a dataset exists.

## 2. Hard rules
1. **No look-ahead.** Features use closed bars only; a 1H or 4H bar is visible only after its close. Every signal function gets a truncation-invariance test: the signal at time t computed on data up to t must equal the signal at t computed on the full series.
2. **Frozen versions.** Rule parameters live in `config/v9.yaml` with a version string. Changing a parameter is a new version, made through a PR with a report. Never tune a parameter by looking at backtest results and keep the old version name.
3. **The holdout set (2024-01-01 onward) is touched once per version**, only in task `R8`, and every touch is recorded in a report. Development work uses 2020-09-01 → 2023-12-31 only.
4. **Costs are always on**: taker fee, slippage and actual funding. Results without costs may appear only next to the same result with costs.
5. **Every statistic reports its sample size `n`** and a confidence interval. `n < 30` shows no percentage; any conclusion with `n < 100` is labelled insufficient sample. A null result is still reported.
6. **Mechanism layers may only veto or delay an entry, never flip direction** (framework §11).
7. **No trading.** No order placement code, no exchange API keys, no account endpoints. Data comes only from the public sources listed in `docs/reports/2026-09-25-data-feasibility.md`.
8. **No secrets, and no real IP, hostname, email address or absolute path of any live machine** in the repository — it is meant to be public.
9. **Data is not committed.** Raw and processed data go under `data/` (gitignored). Tests use tiny fixtures under `tests/fixtures/` and never touch the network; live tests are `@pytest.mark.live` and skipped by default.
10. **Stack**: Python 3.12+, uv, ruff, pytest, polars, pyarrow, numpy. No database until a task needs one.
11. **Do not commit to `main`.** Branch `feat/<task-id>-<slug>`, open a PR, and paste the exact commands you ran and their output (`uv run pytest -q`, `uv run ruff check`) in the PR body.
12. Reports go in `docs/reports/<date>-<topic>.md`, ≤ 80 lines. Do not add new top-level documents.
13. When the docs and reality disagree (a dataset starts later, a field is missing), follow reality and note it in the PR under "Doc corrections".

## 3. Definition of done
Offline tests green, ruff clean, look-ahead tests present for every new signal, no file outside your task's scope changed, PR body has commands + output + doc corrections, and the reviewer (`.claude/agents/reviewer.md`) has no open findings.
