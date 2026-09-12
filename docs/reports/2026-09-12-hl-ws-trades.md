# HL WS `trades{coin}` vs `userFills` hot-200 — §7-1 verification

Scripts: `/tmp/.../scratchpad/hl-probe/{01_trades_60s,01b_keys_check,02_crosscheck,02b_inspect,03_capacity,04_rest_weight,04b_scan_keys,05_clearinghouse}.py`. All calls from this machine's non-US egress, `wss://api.hyperliquid.xyz/ws` and `POST /info`. Times are Unix ms from live responses (~2026-09-12).

## 1. `trades{coin}` schema (01_trades_60s.py, 60.4 s, coins BTC/ETH/SOL/HYPE/ZEC)

One sample (BTC): `{"coin","hash","px","side","sz","tid","time","users":[buyer,seller]}` — **8 keys total, confirmed identical across all 5 coins** (01b_keys_check.py, 547 trades scanned). `users` always has exactly 2 lowercase `0x…` addresses. No `liquidation` key or any liquidation-like field anywhere in the channel.

| coin | trades / 60.4s | trades / min | msg frames / min (bundled) |
|---|---|---|---|
| BTC | 107 | 106.3 | — |
| ETH | 175 | 173.9 | — |
| SOL | 78 | 77.5 | — |
| HYPE | 113 | 112.3 | — |
| ZEC | 74 | 73.5 | — |
| **all 5, combined** | 547 | 543.6 | 182 frames → 180.9/min |

Anomaly: 151/547 (27.6%) trades carry `hash = 0x00…00` (all-zero, 66 chars). Not a liquidation marker by itself — task 2 shows `hash` is copied verbatim from the fill and zero-hash also occurs on ordinary REST fills (e.g. TWAP-related fills, `twapId` non-null in some). Do not use zero-hash as a liquidation proxy.

## 2. Cross-check vs `userFillsByTime` (02_crosscheck.py, 02b_inspect.py)

Captured BTC trade `tid=652825693789509, hash=0x1dca4e…8ae6b`. Queried both `users` addresses via `userFillsByTime` (startTime = now−120s): **exact match found in both addresses' fill lists**, same `tid` and `hash`, confirming trades-channel and REST fills reference the identical event.

REST fill keys (verbatim, 02b_inspect.py sample): `cloid, closedPnl, coin, crossed, dir, fee, feeToken, hash, oid, px, side, startPosition, sz, tid, time, twapId`. Note: doc's 3.6 field list omits `cloid` and `twapId` — both present on every fill. `liquidation` and `builderFee` are **not** in this sample (optional/conditional).

Broader scan (04b_scan_keys.py, 6,000 fills across 3 addresses, last 24h): `liquidation` key present on **21/6000 (0.35%)** fills, shape confirmed `{liquidatedUser, markPx, method}` (sample: ZEC, `method:"market"`). `builderFee` key: **0/6000** — not observed, still presumed optional/rare.

## 3. WS capacity (03_capacity.py, 2 concurrent connections, 120.4 s each)

| conn | subscriptions | total msgs | breakdown | errors / close |
|---|---|---|---|---|
| A | 35 coins `trades` + `allMids` (36 subs) | 495 | `trades` 436, `allMids` 23, `subscriptionResponse` 36 | none |
| B | `userFills` × 3 addresses | 93 | `userFills` 90, `subscriptionResponse` 3 | none |

Rates: conn A ≈ 246.8 msg/min (trades frames alone 217.3/min for 35 coins); conn B ≈ 44.9 msg/min for 3 active whales. **Both connections ran concurrently with zero errors and zero close frames** — 2 connections / 39 subscriptions, both well inside the 8-conn/800-sub self-imposed budget (§4). Extrapolating conn A linearly, ~50 coins ≈ 310 msg/min, far under the documented 2000 msg/min/IP cap.

## 4. REST weight — `userFillsByTime` × 5 addresses, 2 s apart (04_rest_weight.py, 24h window)

| addr (short) | status | n_fills |
|---|---|---|
| 0xf5d81a…3ad53 | 200 | 2000 (capped) |
| 0xf5d889…e76f226 | 200 | 2000 (capped) |
| 0xbeccae…149562 | 200 | 2000 (capped) |
| 0x469e9a…6bf58a5 | 200 | 2000 (capped) |
| 0x622ba9…3cdbc3 | 200 | 2000 (capped) |

No 429 encountered (well under the 40%-of-1200/min budget). All 5 hit the **2000-row response cap** — confirms doc §3.6 footnote; high-frequency whales exhaust it within 24h, so `userFillsByTime` needs frequent polling (≤10 min) or the WS `userFills` channel to avoid gaps.

## 5. `clearinghouseState` × 3 addresses (05_clearinghouse.py)

Top-level keys (all 3, identical): `assetPositions, crossMaintenanceMarginUsed, crossMarginSummary, marginSummary, time, withdrawable`.
`assetPositions[0].position` keys (all 3, identical): `coin, cumFunding, entryPx, leverage, liquidationPx, marginUsed, maxLeverage, positionValue, returnOnEquity, szi, unrealizedPnl` — **all 8 requested fields present** (`liquidationPx, leverage, marginUsed, entryPx, positionValue, unrealizedPnl, maxLeverage, cumFunding`). `liquidationPx` is `null` when cross-margin account has no isolated liquidation price at that leverage (2 of 3 samples).

## Verdict

`trades{coin}` **can replace the WS-connection-count problem** (10-user cap) for the whale engine's fill stream: it is bound by coin count, not wallet count, delivers `users:[buyer,seller]` on every fill, and messages verified byte-identical (`tid`+`hash`) to `userFillsByTime` records — at ~35 coins the combined rate (≈250 msg/min) is far under the 2000/min cap, so scaling to the project's full core+broad coin list is safe. It also does **not** need any wallet allow-list, so it inherently exceeds "200 hot wallets" coverage. However it **cannot replace** `userFills`/`userFillsByTime` outright: (a) it carries no `liquidation`, `closedPnl`, `dir`, `fee`, `startPosition`, `oid`, or `cloid` — those require a REST/`userFillsByTime` join per trade, so liquidation detection must come from periodic REST polling, not the trades channel; (b) it only reports trades on **subscribed coins** — a hot wallet's fill on an un-tracked coin is invisible; (c) matching `users` addresses back to the watchlist still requires an in-memory set lookup per trade (cheap, but not zero design cost). Recommended design: subscribe `trades{coin}` for the tracked coin universe (not per-wallet) to detect *that* a hot wallet traded and *what* the fill looked like (px/sz/side/time), then join to periodic `userFillsByTime` (already budgeted, §4) or a smaller-cardinality `userFills` WS subscription (well under the 10-user cap) for the subset of fills needing `liquidation`/`closedPnl`/`dir` enrichment. `03-DEVELOPMENT` §4.1 should be rewritten to drop "WS `userFills` hot 200" and adopt coin-scoped `trades` + REST enrichment.
