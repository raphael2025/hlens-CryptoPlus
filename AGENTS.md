# AGENTS.md — rules for any coding agent (Cursor, Codex, Claude) working in this repo

Read this before touching anything. Task tracking lives in `docs/PROGRESS.md`; take a task id from there (e.g. `S0-4`) and put it in your branch name and PR title.

## 1. What this project is
hlens-CryptoPlus shows the *state* of a crypto perpetual market (who is long, who is crowded, where liquidations sit) and never gives entry/exit advice. Read `docs/00-PROJECT.md` (why), `docs/06-DATA-SOURCES.md` (what data, which endpoints, rate limits), `docs/adapter/ADAPTER-RESEARCH.md` (adapter protocol), `docs/03-DEVELOPMENT.md` (layout, data model, services). Field names come from `api/openapi.yaml`.

## 2. Hard rules
1. **Do not touch the live static site**: `index.html`, `assets/`, `scripts/fetch.py`, `.github/workflows/`, `data/`, `config.js` — unless the task id explicitly names them.
2. **Rate limits** are accounted per public egress IP and per venue (`config/venues.yaml`). Tests never call live APIs by default; live tests are `@pytest.mark.live` and skipped. Use ≤ 40% of any documented limit when you do run them. Hyperliquid: ≤ 600 weight/min, ≤ 4 concurrent, stop on any 429 and record its body type (JSON `null` = weight, HTML = connection).
3. **No secrets** in code, tests, fixtures, or compose files. No hard-coded hosts outside `config/venues.yaml`.
4. **Normalized types only** cross module boundaries: adapters return `hlens_core.contracts` models, never raw exchange JSON. Prices and sizes are `Decimal`; timestamps are UTC milliseconds; every record carries `venue`, `symbol`, `ts`, `ingest_ts`, `source`.
5. **Every network function has an offline test** with a trimmed recorded fixture under `tests/fixtures/<venue>/`.
6. **Content rule** for anything user-visible: describe state, never advise; no "buy", "sell", "entry", "target", "guaranteed".
7. **Python**: 3.12+, uv, ruff, mypy, pytest, pydantic v2, httpx, websockets. **Frontend**: today vanilla HTML/JS; later Next.js 15 + TypeScript + Tailwind.
8. **Do not commit to `main`**. Branch `feat/<task-id>-<slug>`, open a PR, paste the exact commands you ran and their output (`uv run pytest -q`, `uv run ruff check`) in the PR body.
9. Do not edit `docs/PROGRESS.md`; the maintainer updates it on merge. Do not add new top-level docs; put reports in `docs/reports/<date>-<topic>.md` (≤ 80 lines).
10. When the docs and reality disagree (an endpoint moved, a limit differs), fix the code, and add a one-line note to the PR under "Doc corrections" so the maintainer can update `docs/06-DATA-SOURCES.md`.

## 3. How to add a venue adapter (task S0-4 pattern)
1. Copy `packages/hlens-core/src/hlens_core/adapters/binance.py` structure; declare `CapabilitySet` honestly (`supported` / `mode` / `completeness`).
2. Endpoints, fields, and limits are in `docs/06-DATA-SOURCES.md` §3.x for your venue; constants go in `config/venues.yaml` with a `source: official|measured|unverified` tag.
3. Record fixtures once from a non-US egress (this machine works), trim to ≤ 3 items per list, commit them.
4. Implement: `discover_instruments`, `fetch_mark_prices`, `fetch_funding_rates`, `fetch_open_interest`, `fetch_long_short_ratios`, `fetch_klines`, `fetch_ticker_24h`, `stream_mark_price`, `stream_liquidations` (mark `throttled_source` where the venue throttles), plus Hyperliquid-only wallet methods for HL.
5. Symbol mapping both ways (`BTCUSDT` / `BTC-USDT-SWAP` / `BTC_USDT` ↔ `BTC`).
6. `uv run pytest -q` and `uv run ruff check src tests` green; run `python -m hlens_core.preflight --venues <venue>` once and paste the table in the PR.

## 4. Definition of done
Offline tests green, ruff clean, no file outside your task's scope changed, PR body has commands + output + doc corrections, and the reviewer (Claude, `.claude/agents/reviewer.md`) has no open findings.
