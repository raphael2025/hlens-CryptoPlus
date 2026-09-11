# hlens · the perp positioning prism

**One coin. Every angle.** A prism splits white light into a spectrum. hlens splits a perpetual-futures market into the people inside it — retail, top traders and on-chain whales — across Binance, Bybit, OKX and Hyperliquid. When they disagree, that is the signal.

Live site: `https://raphael2025.github.io/hlens-CryptoPlus/` · Free · Open source (MIT) · No API keys · Refreshed every 30 min by GitHub Actions · English / 中文

## What it shows

- **The prism** — for each coin: retail long share, top-trader long share, taker buy share, Hyperliquid whale long share, OI-weighted 8h funding, total open interest, a transparent crowding score, and per-venue detail.
- **Hyperliquid whale lens** — live positions of the largest and best-performing wallets on the public leaderboard: where the money sits per coin, the largest single positions with entry / liquidation / leverage, and a wallet table with 1d / 7d / 30d PnL.
- **Crowd mood** — Fear & Greed with a plain-language reading of how crowds tend to behave in each regime, explicitly labelled as one lens, not a fact.

## Why another dashboard

Coinglass and friends show *what* the numbers are. hlens is built around three things they do not do:

1. **Decomposition, not aggregation.** One coin, all participants side by side. The gap between retail and whales is the headline, not a footnote.
2. **Evidence labels.** Every interpretive sentence on the site is tagged: verified on data / heuristic / opinion. See [docs/SPEC.md](docs/SPEC.md).
3. **Machine-readable by design.** Everything the page renders is a static JSON file you can `fetch` from anywhere, and the same files will back an MCP server for AI agents.

## Run locally

```bash
python3 scripts/fetch.py        # ~3 min, writes data/latest.json + data/history.json
python3 -m http.server 8765     # then open http://localhost:8765
```

Edit `config.js` to set community links (Telegram / Discord / X / WeChat QR) and the GitHub URL.

## Data (public JSON)

| File | Contents |
|---|---|
| `data/latest.json` | full snapshot: `coins[]` (prism facets + per-exchange rows), `whales` (wallets, by_coin, top_positions), `macro`, `sources` |
| `data/history.json` | last 7 days of `[price, funding_8h, oi_usd, retail_long_share, whale_long_share, crowding]` per coin, one point per refresh |

Schema of the static snapshot is a subset of the production contract. The project is documented as a four-part set: [docs/00-PROJECT.md](docs/00-PROJECT.md) (why, scope, roadmap, community ops), [docs/01-FEATURES.md](docs/01-FEATURES.md) (pages, components, sentence engine, state dictionary, visual identity), [docs/02-API.md](docs/02-API.md) + [api/openapi.yaml](api/openapi.yaml) (the single source of truth for frontend/backend types), [docs/03-DEVELOPMENT.md](docs/03-DEVELOPMENT.md) (repo layout, data model, services, tests, CI/CD, deployment). Older drafts live in `docs/archive/`.

## Sources

Binance USDⓈ-M futures (through the `www.binance.com` mirror, which is reachable from US-based GitHub runners where `fapi.binance.com` answers 451), OKX swap, Gate and Bitget USDT futures, Bybit linear (geo-blocked from US runners, used when reachable), Hyperliquid info API + public leaderboard, alternative.me Fear & Greed. All key-free public endpoints.

## Disclaimer

hlens is a research tool. Nothing here is investment advice. Leveraged perpetuals can lose more than your deposit. Data comes from public APIs and can be delayed or wrong.

---

## 中文

**一个币，所有角度。** hlens 把一个合约市场分解成里面的人：散户、大账户、链上大户，横跨 Binance、Bybit、OKX 和 Hyperliquid。三方意见相左，就是信号。

免费、开源（MIT）、不需要 API key、GitHub Actions 每 30 分钟刷新、中英双语。本地运行见上方命令；社群入口在 `config.js` 里配置。产品方案与页面细节见 [docs/SPEC.md](docs/SPEC.md)。

本站是研究工具，不构成投资建议。带杠杆的永续合约可能亏损超过本金。
