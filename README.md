# hlens CryptoPlus

**Multi-venue crypto intelligence & quant research system.** A prism splits white light into a spectrum; hlens splits one market into its facets — price, order flow, open interest, funding, liquidity, participants. The disagreement between facets is the information. hlens reports state, historical distributions and reproducible research. It never tells you what to do.

First slice: **Binance (CEX) + Hyperliquid (DEX), perpetual futures.** One centralised venue and one decentralised venue are enough to show where the two definitions of the same number diverge.

**Status: M1 in progress — the collector core is being built one module at a time.** The normalized contracts, the rate-limit ledger and the adapter protocol are in; the venue adapters are not. This repository also holds the product, feature, architecture and data-source documents the build follows. Milestones and their acceptance criteria are in [`docs/01-PRODUCT.md`](docs/01-PRODUCT.md) §5.2.

## What will exist when it is built

- **A minute-level collector** on one self-owned server (which shares its public egress with an older collector that is still running), recording price, mark price, funding and open interest for every perpetual listed on both venues — kept forever, never downsampled.
- **A static site** showing cross-venue divergence: one card per coin with both venues side by side, every number carrying its 30-day historical percentile and its sample size `n`.
- **A daily Telegram digest** — the three most abnormal coins, the three largest divergences, one market line, the largest liquidations.
- **Reproducible research notes** — public formula, public data window, a notebook anyone can rerun. Published even when the finding is "no effect".

## Principles that make it different

1. **Decomposition, not aggregation.** Venues are shown side by side with their definitions stated. There is no cross-venue "whole market" total.
2. **Evidence labels.** Every interpretive sentence is tagged: verified / self-validated / heuristic / opinion / insufficient sample / model estimate. `n < 10` shows no value at all; `n < 30` shows no percentage.
3. **Machine-readable and versioned.** The site reads versioned JSON exported by the collector and contains no business logic of its own. Rule and threshold changes go through a pull request with a calibration report.
4. **Never trade advice.** No calls, no targets, no copy-trading, no signal selling, and it never touches user funds.
5. **Honest about gaps.** Liquidation numbers are rate-limited by the exchanges and are always labelled a lower bound; a failed source goes grey with its last success time rather than showing a zero.

## Documents

| Document | Contents |
|---|---|
| [`docs/01-PRODUCT.md`](docs/01-PRODUCT.md) | 定位、原则、范围、里程碑 |
| [`docs/02-FEATURES.md`](docs/02-FEATURES.md) | M1–M6 的功能定义（`F1`–`F31`） |
| [`docs/03-ARCHITECTURE.md`](docs/03-ARCHITECTURE.md) | 架构与方案设计 |
| [`docs/04-DATA-SOURCES.md`](docs/04-DATA-SOURCES.md) | 数据源、接口、限速 |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | 进度与决定记录 |
| [`AGENTS.md`](AGENTS.md) | 给编码 agent 的规则 |

MIT licensed. Every data source is a key-free public endpoint — no API keys are needed to build or run any of this.

## Disclaimer

hlens is a research tool. Nothing here is investment advice. Leveraged perpetuals can lose more than your deposit. Data comes from public APIs and can be delayed or wrong.

---

## 中文

**多所加密情报与量化研究系统。** 棱镜把白光分成光谱；hlens 把一个市场拆成价格、订单流、持仓、费率、流动性、参与者等多个面，**面与面之间的分歧就是信息**。只给状态、历史分布与可复现的研究结论，不给动作。

第一切片：**Binance（CEX）+ Hyperliquid（DEX），永续合约**。一个 CEX 加一个 DEX 已足以显示同一个数字在两种口径下的分歧。

**当前状态：文档阶段，还没有代码。** 仓库现在只有指导建造的文档，里程碑与验收标准见 [`docs/01-PRODUCT.md`](docs/01-PRODUCT.md) §5.2。

建成后会有：**分钟级采集器**（单台自有服务器，与一套仍在运行的旧采集器共用同一个公网出口；两所共同上架的全部永续，价格 / 标记价 / 费率 / 持仓量，永久保留、不降采样）· **静态站**（一币一卡、两所并排，每个数字都带 30 天历史分位与样本量 n）· **每日 Telegram 摘要**（最不正常的 3 个币、分歧最大的 3 个币、一行市况、最大爆仓）· **可复现研究笔记**（公式、数据窗口、可直接重跑的 notebook；结论是"没有效果"也照样发布）。

与别人不同的地方：分解而不加总，永不给跨所的"全市场"数字；每句解读都带证据标签，n < 10 不显数值、n < 30 不显百分比；前端只读版本化 JSON，规则改动走 PR 并附校准报告；只描述不建议，不碰用户资金；爆仓数字一律标"下界"，源故障置灰并显示最后成功时间，不补零。

文档索引见上表。MIT 协议，全部数据源都是免 key 的公开接口。

本项目是研究工具，不构成投资建议。带杠杆的永续合约可能亏损超过本金。数据来自公开接口，可能延迟或出错。
