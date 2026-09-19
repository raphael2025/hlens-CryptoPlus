# hlens · the perp positioning prism

**One coin. Every angle.** A prism splits white light into a spectrum. hlens splits a perpetual-futures market into the people inside it — retail, top traders and on-chain whales — across Binance, Bybit, OKX and Hyperliquid. When they disagree, that is the signal.

Live site: `https://raphael2025.github.io/hlens-CryptoPlus/` · Free · Open source (MIT) · No API keys · Refreshed every 30 min by GitHub Actions · English / 中文

**Where this is going:** the project is being rebuilt (v4.0, 2026-09-19) into a multi-venue crypto intelligence & quant research system, keeping the prism metaphor. The first slice is a Python + PostgreSQL backend collecting Binance and Hyperliquid perpetuals; the site below (six venues, whale lens) keeps running unchanged until that work reaches it. Milestones M1–M6 are in [docs/00-PROJECT.md §8](docs/00-PROJECT.md).

## What it shows

- **The prism** — for each coin: retail long share, top-trader long share, taker buy share, Hyperliquid whale long share, OI-weighted 8h funding, total open interest, a transparent crowding score, and per-venue detail.
- **Hyperliquid whale lens** — live positions of the largest and best-performing wallets on the public leaderboard: where the money sits per coin, the largest single positions with entry / liquidation / leverage, and a wallet table with 1d / 7d / 30d PnL.
- **Crowd mood** — Fear & Greed with a plain-language reading of how crowds tend to behave in each regime, explicitly labelled as one lens, not a fact.

## Why another dashboard

Coinglass and friends show *what* the numbers are. hlens is built around three things they do not do:

1. **Decomposition, not aggregation.** One coin, all participants side by side. The gap between retail and whales is the headline, not a footnote.
2. **Evidence labels.** Every interpretive sentence on the site is tagged: verified / self-validated / heuristic / opinion / insufficient sample / model estimate. See [docs/00-PROJECT.md](docs/00-PROJECT.md).
3. **Machine-readable by design.** Everything the page renders is a static JSON file you can `fetch` from anywhere, and the same files will back an MCP server for AI agents.

## Run locally

```bash
python3 scripts/fetch.py        # ~3 min, writes data/latest.json + data/history.json
python3 -m http.server 8765     # then open http://localhost:8765
```

Edit `config.js` to set community links (Telegram / Discord / X) and the GitHub URL.

## Data (public JSON)

| File | Contents |
|---|---|
| `data/latest.json` | full snapshot: `coins[]` (prism facets + per-exchange rows), `whales` (wallets, by_coin, top_positions), `macro`, `sources` |
| `data/history.json` | last 7 days of `[price, funding_8h, oi_total_usd, retail_long_share, whale_long_share, crowding]` per coin, one point per refresh |

The static snapshot (`data/latest.json`) is the live site's own schema; whether and how it migrates to a future backend contract is undecided until M3, see [docs/00-PROJECT.md §7.4](docs/00-PROJECT.md). The living doc set is [docs/00-PROJECT.md](docs/00-PROJECT.md) (why, scope, principles, architecture, milestones), [docs/01-FEATURES.md](docs/01-FEATURES.md) (feature definitions for the current milestones), [docs/06-DATA-SOURCES.md](docs/06-DATA-SOURCES.md) (data requirements, sources, reachability, budgets), and [docs/PROGRESS.md](docs/PROGRESS.md) (task tracking). The adapter protocol lives in code — `packages/hlens-core/src/hlens_core/adapters/base.py` (protocol and capability declaration), `adapters/binance.py` (reference implementation) — and in [docs/02-ARCHITECTURE.md](docs/02-ARCHITECTURE.md) (modules, tables, build order). The superseded v3.1 plan (six-venue API contract, two-host Docker layout, 20-week roadmap) and its review records were removed from the tree on 2026-09-19 and are recoverable from git history at commit `c8e70fe`.

## Sources

Binance USDⓈ-M futures, OKX swap, Gate and Bitget USDT futures, Bybit linear (used when reachable from the runner), Hyperliquid info API + public leaderboard, alternative.me Fear & Greed. All key-free public endpoints; each source degrades independently.

## Disclaimer

hlens is a research tool. Nothing here is investment advice. Leveraged perpetuals can lose more than your deposit. Data comes from public APIs and can be delayed or wrong.

---

## 中文

**一个币，所有角度。** hlens 把一个合约市场分解成里面的人：散户、大账户、链上大户，横跨 Binance、Bybit、OKX 和 Hyperliquid。三方意见相左，就是信号。

**未来方向：** 项目正在重启（v4.0，2026-09-19），定位扩为"多所加密情报与量化研究系统"，棱镜隐喻不变。第一切片是用 Python + PostgreSQL 采集 Binance 与 Hyperliquid 永续；下方的线上静态站（六所、鲸鱼透镜）在此之前照常运行不变。里程碑 M1–M6 见 [docs/00-PROJECT.md §8](docs/00-PROJECT.md)。

免费、开源（MIT）、不需要 API key、GitHub Actions 每 30 分钟刷新、中英双语。本地运行见上方命令；社群入口在 `config.js` 里配置。现行文档集见 [`docs/00-PROJECT.md`](docs/00-PROJECT.md)、[`docs/01-FEATURES.md`](docs/01-FEATURES.md)、[`docs/06-DATA-SOURCES.md`](docs/06-DATA-SOURCES.md) 与 [`docs/PROGRESS.md`](docs/PROGRESS.md)；适配器协议见代码 `packages/hlens-core/src/hlens_core/adapters/base.py`（协议与能力声明）、`adapters/binance.py`（参考实现）与 [`docs/02-ARCHITECTURE.md`](docs/02-ARCHITECTURE.md)（模块、表结构、建设顺序）；v3.1 旧方案已于 2026-09-19 从工作树删除，可在 git 历史 commit `c8e70fe` 找回，仅供参考，不作为实现依据。

本站是研究工具，不构成投资建议。带杠杆的永续合约可能亏损超过本金。
