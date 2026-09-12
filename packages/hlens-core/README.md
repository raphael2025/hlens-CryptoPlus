# hlens-core

Shared Python core for hlens collectors: normalized **contracts**, per-egress-IP
**ratelimit** accounting, collector **preflight**, and venue **adapters**.
Constants live in `config/venues.yaml` at the repo root, never in code.
```
contracts/   Instrument, MarkPrice, FundingRate, OpenInterest, LongShortRatio,
             Candle, Liquidation, Ticker24h -- Decimal, UTC ms, venue+symbol+ts+
             ingest_ts+source on every record
ratelimit/   Budget (sliding window, reserve/settle, max_inflight, AIMD) + venues.yaml loader
preflight/   egress identity, reachability, clock skew, coverage matrix -> SourceHealth
adapters/    base.py (protocol, capabilities, HTTP transport, symbol map) + binance.py
```

## Adding a venue adapter

Copy `src/hlens_core/adapters/binance.py` and work down this list:

1. **venues.yaml** -- add or check your venue's block: `limit` (official ceiling),
   `budget` (40% of it; HL is 90%), `unit`, `window_s`, `max_inflight`, `rest_bases`,
   `ws_base`, `probe_url`, and a `source:` of `official` / `measured` / `unverified`.
   Per-endpoint limits go in `buckets:`; per-endpoint weights in `weights:`.
2. **Class** -- `venue`, `product`, and a constructor taking `egress_ip`/`budget`/`http`
   so tests can inject. Use `VenueHttp`; do not build your own httpx client.
3. **`capabilities()`** -- declare all of `Capability`. `supported` / `mode` /
   `completeness` are three separate answers; never collapse them into a bool.
4. **`discover_instruments()`** -- canonical `symbol` via `split_multiplier`
   (`1000PEPE`/`kPEPE` -> `PEPE` + mult), plus raw `venue_symbol`, tick, qty step,
   real funding interval, listed_ts, status. Register every pair in `self.symbols`.
5. **Fetchers** -- prefer one all-market request over N per-symbol ones; pass the right
   `weight=` to every call; parse numbers with `Decimal`; leave unknowns `None`, never 0.
   Derived values (`oi_usd = oi x mark`) must set `semantic=derived`.
6. **Streams** -- reconnect with backoff, handle the venue's heartbeat and forced
   disconnect, and set `throttled_source=True` + `completeness=lower_bound` on any
   liquidation feed the venue rate-limits.
7. **Fixtures** -- record real responses once, trim to a few symbols, store under
   `tests/fixtures/<venue>/`, and write respx-mocked tests for every parser plus one
   `@pytest.mark.live` smoke test.
8. Export the class from `adapters/__init__.py`.

Check yourself against `VenueAdapter`: `isinstance(MyAdapter(...), VenueAdapter)` is
`True` only when every protocol method exists.

## Running

```bash
cd packages/hlens-core
uv sync
uv run pytest -q                 # offline; live tests are deselected by default
uv run pytest -q -m live         # hits real APIs; needs a non-US, non-CN egress
uv run ruff check src tests
uv run python -m hlens_core.preflight --venues binance      # table + JSON
uv run python -m hlens_core.preflight                       # all six venues
```

`HLENS_VENUES=/path/venues.yaml` overrides config discovery (default: walk up to the repo root).

**Rate-limit discipline:** never exceed 40% of a CEX limit in tests, and never bypass
`Budget` -- the ledger is shared per egress IP, and a 429 you cause is a 429 for everyone
else behind it.
