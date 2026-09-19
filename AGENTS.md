# AGENTS.md — rules for any coding agent (Cursor, Codex, Claude) working in this repo

Read this before touching anything. Task tracking lives in `docs/PROGRESS.md`; take a task id from there (M1 ids are letters — `M1-A` … `M1-J`, matching `docs/03-ARCHITECTURE.md` build order) and put it in your branch name and PR title. **The repository currently contains documentation only — there is no code yet. M1 builds it from nothing.**

## 1. What this project is
hlens CryptoPlus reports the *state* of a crypto perpetual market (who is long, who is crowded, where liquidations sit) across Binance and Hyperliquid, and never gives entry/exit advice. Read, in this order:
- `docs/01-PRODUCT.md` — positioning, the non-negotiable principles (§4), scope and milestones (§5.2), hard constraints (§7).
- `docs/02-FEATURES.md` — the confirmed feature definitions `F1`–`F31`, covering M1–M6. Each carries a confidence tag and a data-availability note. **Changed only by raphael's decision, never by a coding agent.**
- `docs/03-ARCHITECTURE.md` — the five seams, technology stack, modules, data model, processes, export path, build order. Your task's design decisions were already made here; if you think one is wrong, say so in the PR instead of quietly doing something else.
- `docs/04-DATA-SOURCES.md` — what data we need, which endpoint provides it, weights, rate limits, what history can be backfilled.

## 2. Hard rules
1. **Architecture first.** No coding task is dispatched before `docs/03-ARCHITECTURE.md` is confirmed. If your task contradicts it, stop and report.
2. **Rate limits** are accounted per public egress IP and per venue, with constants read from `config/venues.yaml` (each carrying a `source: official|measured|unverified` tag, values from `docs/04-DATA-SOURCES.md`). Binance's `/futures/data/*` endpoints have their own separate limit and must be accounted separately. Tests never call live APIs by default; live tests are `@pytest.mark.live` and skipped. **The production host shares one public egress IP with an older collector that is still running, so 40 % of the documented limit is NOT yours to spend.** Your ceiling is `official limit × share − Σ reserved`, where the reservations live in `config/egress-consumers.yaml`; preflight prints the deduction at startup and refuses to start if that file is missing or disagrees with the running consumers. Never call a live endpoint without that ceiling in force — a 429 here escalates to a 418 that bans the whole machine's IP, killing the older collector along with ours. Hyperliquid: stop on any 429 and record its body type (JSON `null` = weight, HTML = connection).
3. **No secrets** in code, tests, fixtures or compose files. No hard-coded hosts outside `config/venues.yaml`. Tunnel tokens, bot tokens, Tailscale auth keys, mail-server and DKIM keys, the backup pull key and alert URLs live only in the machine's `.env`. **Never write a real IP, hostname, email address or absolute path of any live machine into this repository** — it is meant to be public, and the production host is one of raphael's own machines.
4. **Normalized types only** cross module boundaries: adapters return contract models, never raw exchange JSON. Prices and sizes are `Decimal`; timestamps are UTC milliseconds; every record carries `venue`, `symbol`, `ts`, `ingest_ts`, `source`.
5. **Modules talk through database tables and the contracts, never by importing each other** (seam ③). Living in the same process is a deployment choice, not a licence to share state.
6. **Every network function has an offline test** with a trimmed recorded fixture under `tests/fixtures/<venue>/`.
7. **Capabilities are declared honestly**: `supported` / `mode` / `completeness` are three separate answers. A venue-throttled liquidation feed is always `lower_bound`. Trades and order-book are declared on every adapter but marked `unsupported` until M4 — do not implement them early. Wallet data has its own protocol from M5 and must never be added to the market-data adapter.
8. **Content rule** for anything user-visible: describe state, never advise. Banned: 建议、可以考虑、适合、机会、目标价、止损设在 and their English equivalents. Every statistic shows its sample size `n`; `n < 10` shows no value, `n < 30` shows no percentage. No cross-venue "whole market" total — a liquidation total may only be written as "named venue + ≥".
9. **Python** 3.12+, uv, ruff, mypy, pytest, pydantic v2, httpx, websockets. **PostgreSQL 18.6+** (dev and prod run the same major version — a `pg_dump` from a newer server will not restore into an older one, which would make the restore drill fake; preflight asserts `server_version_num >= 180006`, the release that fixes the `pg_dump` overflow CVE-2026-19385). No ORM: numbered `.sql` migrations. Frontend: plain HTML/CSS/JS, no build step, no framework — a framework is a later decision, not yours.
10. **Do not commit to `main`.** Branch `feat/<task-id>-<slug>`, open a PR, and paste the exact commands you ran and their output (`uv run pytest -q`, `uv run ruff check`) in the PR body.
11. Do not edit `docs/PROGRESS.md` — the maintainer updates it on merge. Do not add new top-level documents; put reports in `docs/reports/<date>-<topic>.md`, ≤ 80 lines.
12. When the docs and reality disagree (an endpoint moved, a limit differs), fix the code and add a one-line note to the PR under "Doc corrections" so the maintainer can update `docs/04-DATA-SOURCES.md`. Never silently follow the doc into a wrong call.

## 3. How to add a venue adapter
1. Implement the adapter protocol defined in `docs/03-ARCHITECTURE.md`; declare the capability set honestly.
2. Endpoints, fields and limits for your venue are in `docs/04-DATA-SOURCES.md`; constants go into `config/venues.yaml` with their source tag.
3. Record fixtures once **from the production host's egress** (task `M1-G`), trim to ≤ 3 items per list, commit them, and tag each file `source: live-recorded` rather than `documented`. Fixtures recorded anywhere else do not count — the dev machine reaches Binance's WebSocket but receives no data from it.
4. Market-data methods: instrument discovery, mark prices, funding rates, open interest, long/short and taker ratios where the venue publishes them, klines, 24 h ticker, plus the mark-price and liquidation streams (marking `throttled_source` where the venue throttles). Hyperliquid publishes no long/short ratio and no usable public liquidation stream — declare both as unsupported rather than deriving a substitute.
5. Symbol mapping both ways, including the unit differences (`1000PEPE` ↔ `kPEPE`) and the venue's native funding interval alongside the normalized 8-hour value.
6. `uv run pytest -q` and `uv run ruff check src tests` green; run the preflight CLI once for your venue and paste its table in the PR.

## 4. Definition of done
Offline tests green, ruff clean, no file outside your task's scope changed, PR body has commands + output + doc corrections, and the reviewer (`.claude/agents/reviewer.md`) has no open findings.
