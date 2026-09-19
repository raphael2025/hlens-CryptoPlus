# hlens · 数据需求与数据源清单（06-DATA-SOURCES，v1.0，2026-09-12）

> **00 v4.0 说明（2026-09-19）**：第一切片只采 Binance + Hyperliquid；本文里 Bybit / OKX / Gate / Bitget 各节保留作"以后"（`00-PROJECT.md` §5.3）候选清单，不是现在要实现的范围。本文对 `03-DEVELOPMENT`、`05-DOCKER` 的引用已随这两份文档于 2026-09-19 从工作树删除（可在 git 历史 commit `c8e70fe` 找回），以 `00-PROJECT.md` §7 为准。Hyperliquid 大户 / 钱包相关小节（§2.7 等）从 **M5** 起才适用，此前 HL 适配器只实现行情方法。
>
> 回答三个问题：**我们要什么数据**（§2）、**从哪个官方接口拿**（§3）、**限速与预算怎么算**（§4）。字段名以 `packages/hlens-core` 的 contracts 为准（原先指向的 `api/openapi.yaml` 与 `03-DEVELOPMENT.md` 已于 2026-09-19 删除，可在 git 历史 commit `c8e70fe` 找回）。限速数字标注来源：`官方` = 官方文档原文（2026-09-12 核对）、`实测` = 我们的仓库或 Actions 探测记录、`未验证` = 文档未明写，按保守值记账。
>
> 三条硬规则：① 限速按**公网出口 IP** 记账，同宿主机所有进程共用一份；② 预算按官方上限的 **40%** 使用，HL 按 90% 使用但记账口径 1200；③ 每个来源在采集器启动时跑一次自检（§6），结果写 `source_health`，状态页公示。

## 1. 数据消费者一览

| 消费者 | 需要的数据 | 新鲜度预算（`00-PROJECT` §4.4） |
|---|---|---|
| 首页 `getInsights` / `listCoins` / `getBreadth` | 价格、24h、费率、OI、多空比、爆仓聚合、状态 | 价格与费率 ≤ 60 s；OI ≤ 2 min |
| 币页 `getPrism`（六条光谱） | 散户多头、大账户多头、主动买入、大户多头（HL 仓位）、费率 8h、OI 分位 | 多空比 ≤ 15 min；大户 ≤ 5 min |
| 市场环境 `getContext` | 1m/1h/1d K 线、EMA/ATR/ADX、量、OI 增量方向、驱动因子 | K 线收盘即算 |
| 关键位 `getLevels` | 爆仓价模型（OI 增量 × 杠杆档 × MMR）、近期爆仓密度、大户强平价 | ≤ 5 min |
| 大户 `getWhaleBoards` / `listWhaleEvents` / `getWallet` | 排行榜、账户状态、仓位、成交、权益曲线 | 热钱包 ≤ 5 min |
| 错过 `listEvents` / 回放 `getSnapshot` | `market_events`、`coin_prism_1m` 历史 | 分钟级归档 |
| 告警引擎（`AlertMetric` 13 项） | 费率、OI 变化、1h 爆仓、级联、大户事件与距强平、拥挤度、价格、状态变化 | 同上，逐分钟评估 |
| 记分板 / 校准 | 2 年日线 + 90 天小时线（参考所）、状态历史 | 每日 |
| 状态页 `getStatus` | `source_health_1m`、自检结果 | 1 min |

## 2. 数据需求清单（按数据类别）

每行：需要什么 → 谁用 → 落哪张表 → 首选来源 / 备选。**首选**按"一次请求覆盖最多币"和"字段最全"排序，不按交易所排名。

### 2.1 合约行情
| 数据 | 用途 | 表 | 首选 | 备选 |
|---|---|---|---|---|
| 标记价、指数价（全市场，秒级） | 价格、`ticks_1m`、级联判定的价格移动 | `ticks_1m`（CAGG） | Binance WS `!markPrice@arr@1s`；Bybit WS `tickers`；OKX WS `mark-price`；HL WS `allMids` | 各所 REST tickers 每 60 s |
| 24h 涨跌、24h 成交额 | `CoinHeader`、`listCoins` 排序 | `coin_prism_1m` | Bybit `/v5/market/tickers`（全）；OKX `/market/tickers`（全）；Binance `/fapi/v1/ticker/24hr`（全）；HL `metaAndAssetCtxs.dayNtlVlm` | Gate `/tickers`、Bitget `/tickers` |
| K 线 1m / 1h / 1d（参考所） | `getContext` 指标、状态引擎、事件回填 `ret_1h/4h/24h`、记分板 | `klines` | Binance `/fapi/v1/klines`（美区外）；历史批量 `data.binance.vision` | Bybit `/v5/market/kline`；OKX `/market/candles`；HL `candleSnapshot` |
| 主动买入量（taker buy volume） | `taker_buy_share` 的 K 线级替代、OI 增量拆多空 | `klines.taker_buy_v` | Binance K 线字段 `takerBuyBaseVolume` | 各所 `taker-volume` 统计端点 |

### 2.2 资金费率
| 数据 | 用途 | 表 | 首选 | 备选 |
|---|---|---|---|---|
| 当前费率 + 结算周期 + 下次结算时间 | `funding_8h`（按周期归一到 8h）、`funding_apr_pct`、`funding_spread`（所间价差） | `funding` | Binance `/fapi/v1/premiumIndex`（全）+ `/fapi/v1/fundingInfo`（非 8h 符号）；Bybit tickers 含 `fundingRate/nextFundingTime`，周期在 `instruments-info.fundingInterval`；OKX `/public/funding-rate?instId=ANY`；HL `metaAndAssetCtxs.funding`（每小时） | Gate `/tickers` + `/contracts`；Bitget `/tickers` + `/funding-time` |
| 预测费率 | `funding_pred` 卡片 | `funding_pred` | HL `predictedFundings`（含各所预测）；OKX `nextFundingRate`；Bybit 无预测（用当前） | – |
| 费率历史（2 年） | 费率分位 `pctl_30d`、记分板 | `funding` | 各所 history 端点分页；Binance 批量 `data.binance.vision/…/fundingRate` | HL `fundingHistory` |

### 2.3 持仓量（OI）
| 数据 | 用途 | 表 | 首选 | 备选 |
|---|---|---|---|---|
| 当前 OI（全市场，每分钟） | `oi_total_usd`、`oi_chg_pct`、OI 分位、`oi_to_mcap` | `open_interest` | Bybit tickers `openInterestValue`（全，一次）；OKX `/public/open-interest?instType=SWAP`（全，一次）；HL `metaAndAssetCtxs.openInterest`（全，一次）；Binance `/fapi/v1/openInterest`（**逐币**） | Gate `contract_stats.open_interest_usd`；Bitget `/open-interest` |
| OI 历史 | 30 天分位冷启动、回放 | `open_interest` | Binance `/futures/data/openInterestHist`（5m…1d，仅 30 天）；Bybit `/v5/market/open-interest`（5min…1d）；OKX `rubik…/open-interest-history` | 自采累积后不再依赖 |

### 2.4 多空比与主动买卖比
| 数据 | 用途 | 表 | 首选 | 备选 |
|---|---|---|---|---|
| 账户多空比（散户口径） | `retail_long_share` | `ls_ratio(kind=account)` | Binance `globalLongShortAccountRatio`；OKX `rubik…/long-short-account-ratio-contract`；Bybit `/v5/market/account-ratio`；Gate `contract_stats.lsr_account`；Bitget `account-long-short` | – |
| 大账户多空比（持仓口径） | `top_trader_long_share` | `ls_ratio(kind=top_position)` | Binance `topLongShortPositionRatio`；OKX `…-contract-top-trader`（持仓）；Gate `contract_stats.top_lsr_size` | Bitget `position-long-short` |
| 大账户多空比（账户口径） | `top_trader_long_share` 备选 | `ls_ratio(kind=top_account)` | Binance `topLongShortAccountRatio`；Gate `top_lsr_account` | – |
| 主动买卖比（taker） | `taker_buy_share` | `ls_ratio(kind=taker)` | Binance `takerlongshortRatio`；OKX `rubik…/taker-volume-contract`；Gate `contract_stats.lsr_taker`；Bitget `taker-buy-sell` | K 线 `taker_buy_v` 推算 |

> 各所口径不同（账户数 vs 持仓量 vs 名义额），**不做无权重平均**，分 kind 分所存，聚合时按 OI 加权并在 `sources` 里标出。

### 2.5 爆仓
| 数据 | 用途 | 表 | 首选 | 备选 |
|---|---|---|---|---|
| 逐笔爆仓流 | `liq_1h_usd`、`liq_24h_*`、级联检测、关键位密度 | `liquidations` | Binance WS `!forceOrder@arr`（**限流：每符号每秒最多 1 条**，是下界）；Bybit WS `allLiquidation.SYMBOL`；OKX WS `liquidation-orders`；Bitget WS；HL：`trades{coin}` **不带** `liquidation` 字段（实测，`reports/2026-09-12-hl-ws-trades.md`），逐笔强平只在 fill 对象上（`userFills` / `userFillsByTime` 的 `liquidation{liquidatedUser,markPx,method}`），逐钱包采样是 `lower_bound`（hub 实测见 `reports/2026-09-12-hl-liquidation-coverage.md`）；**全所级唯一真值源是节点数据 `hl-mainnet-node-data/node_fills_by_block`**（存档月更；实时需自跑非验证节点） | Gate `/liq_orders` 轮询（15 min） |
| 爆仓聚合（小时） | 冷启动、对账 | `liquidations` | Gate `contract_stats.long_liq_usd/short_liq_usd`（1h，唯一给聚合 USD 的公开端点） | OKX `/public/liquidation-orders` 历史 |

### 2.6 合约元数据
| 数据 | 用途 | 表 | 首选 |
|---|---|---|---|
| 符号表、合约乘数、tick、上下架 | `instruments`、符号归一化（`hlens` symbolmap） | `instruments` | Binance `exchangeInfo`；Bybit `instruments-info`；OKX `/public/instruments`（`ctVal/ctMult`）；Gate `/contracts`（`quanto_multiplier`）；Bitget `/contracts`；HL `meta`（`szDecimals/maxLeverage`） |
| 维持保证金档（MMR tiers） | 清算价模型 `getLevels` | `instruments.mmr_tiers` | OKX `/public/position-tiers`；Bybit `/v5/market/risk-limit`；Gate `/risk_limit_tiers`；HL ≈ 1/(2·maxLeverage)；**Binance `leverageBracket` 需签名 → 用 Bybit 档近似** |
| 费率结算周期 | 费率归一 | `instruments` | Binance `fundingInfo`；Bybit `fundingInterval`；Gate `funding_interval`；Bitget `funding-time`；HL 固定 1h |

### 2.7 Hyperliquid 大户
| 数据 | 用途 | 表 | 来源 | 频率 |
|---|---|---|---|---|
| 排行榜（全量 JSON） | 候选池种子 | `wallets` | `stats-data.hyperliquid.xyz/Mainnet/leaderboard`（非官方文档端点，前端同源，CloudFront 缓存，不吃 /info 权重） | 1 h |
| 账户状态与仓位（`clearinghouseState`） | `hl_wallet_state`、`hl_positions`、大户多头分面、`RektSoonBoard`、`whale_near_liq` | 同左 | `/info clearinghouseState`（权重 2） | hot 200 每 1 min；warm 800 每 10 min；cold 每 1 h |
| 成交流（实时） | `whale_events`（权威源）、`liquidated` | `hl_fills` | WS `userFills {user}`（不吃 /info 权重；每 IP 订阅上限见 §3.6） | 常驻 |
| 成交（补齐 / warm） | 断线补齐、warm 层 | `hl_fills` | `/info userFillsByTime`（old→new，复合游标） | warm 每 10 min，≤ 20 次/min |
| 成交历史（2023-02 →） | 记分板、钱包评分、回放 | `hl_fills` | **hub Parquet 只读导入**（`04` 决议 (a)），不再走 API 回补 | 全量一次 + 每日增量 |
| 资产上下文（费率、OI、标记价、溢价） | HL 列、`hl_asset_ctx` | `hl_asset_ctx` | `/info metaAndAssetCtxs`（权重 20，一次拉全） | 1 min |
| 大额成交发现 | 候选池补充（单笔 ≥ 100 万美元） | `wallets.discovered_by=trades` | WS `trades {coin}`（前 30 币） | 常驻 |
| 资金流水（入金出金） | 权益曲线拆分（v1.1） | – | `/info userNonFundingLedgerUpdates` | 按需 |

### 2.8 宏观与情绪
| 数据 | 用途 | 表 | 来源 | 频率 |
|---|---|---|---|---|
| 恐惧贪婪指数 | 首页情绪卡、`macro` | `macro` | alternative.me `/fng/` | 1 h |
| 总市值、BTC 占比、各币市值 | `oi_to_mcap`、`coin_meta` | `coin_meta`、`macro` | CoinGecko `/global`、`/coins/markets` | 10 min |
| 稳定币总供给 | 首页"场内弹药"（v1.1） | `macro` | DefiLlama `stablecoins.llama.fi/stablecoins` | 1 h |
| BTC/ETH 隐含波动率（DVOL）、期限结构 | `getContext` 波动环境（v1.1，`x-hlens-stage`） | `macro` | Deribit `get_volatility_index_data`、`get_book_summary_by_currency` | 5 min |
| 基差 | v1.1 `basis` | `macro` | Binance `/futures/data/basis`；自算（永续 − 交割） | 5 min |

### 2.9 OKX 带单（v1.1）
公开带单人榜与其当前仓位（`/copytrading/public-lead-traders`、`public-current-subpositions`），进 `whale_events(source=okx)`。S0 要核实是否需鉴权与限速（§7）。

### 2.10 不采集的
盘口深度（任何所）、逐笔成交全量（CEX）、现货、期权逐合约、链上数据。理由：服务器成本与页面无对应消费者。

## 3. 官方接口明细（按来源）

约定：`W` = Binance 权重；`限` = 单次最大条数；`史` = 官方保留多久；`公开` = 不需要 API key。所有 REST 均为 GET，HL 为 POST。

### 3.1 Binance USDⓈ-M（`https://fapi.binance.com`；美区出口用 `https://www.binance.com` 镜像同路径，实测 200）
文档：developers.binance.com/docs/derivatives/usds-margined-futures（2026-09-12 核对，`/futures/data/*` 亦在其中）。

| 端点 | 关键参数 | 我们用的字段 | W | 限 / 史 | 采集频率 |
|---|---|---|---|---|---|
| `/fapi/v1/premiumIndex` | 不传 symbol = 全市场 | `markPrice, indexPrice, lastFundingRate, nextFundingTime` | 1 单币 / **10 全市场** | 快照 | REST 60 s 兜底；主用 WS |
| `/fapi/v1/openInterest` | `symbol` 必填 | `openInterest` | 1 | 快照 | 逐币 1 min（300 币 = 300 W/min） |
| `/futures/data/openInterestHist` | `symbol, period(5m…1d), limit≤500` | `sumOpenInterestValue` | 0（实测响应无 `X-MBX-USED-WEIGHT` 头，仍受 IP 频率限制；账本按 1 记） | 500 / **30 天** | 冷启动一次 |
| `/futures/data/globalLongShortAccountRatio` | 同上 | `longAccount` | 0 | 500 / 30 天 | 5 min |
| `/futures/data/topLongShortAccountRatio` | 同上 | `longAccount` | 0 | 500 / 30 天 | 5 min |
| `/futures/data/topLongShortPositionRatio` | 同上 | `longAccount` | 0 | 500 / 30 天 | 5 min |
| `/futures/data/takerlongshortRatio` | 同上 | `buyVol, sellVol` | 0 | 500 / 30 天 | 5 min |
| `/futures/data/basis` | `pair, contractType(PERPETUAL/CURRENT_QUARTER/NEXT_QUARTER), period` | `basisRate, annualizedBasisRate` | 0 | 500 / 30 天 | v1.1，5 min |
| `/fapi/v1/fundingRate` | `symbol` 可选，`limit≤1000` | `fundingRate, fundingTime` | 独立限速 **500 次 / 5 min / IP** | 1000 / 全史 | 冷启动 + 每 8 h 对账 |
| `/fapi/v1/fundingInfo` | – | `fundingIntervalHours, adjustedFundingRateCap` | 同上独立限速 | 只列非默认周期的币 | 1 h |
| `/fapi/v1/klines` | `symbol, interval, limit≤1500` | OHLCV + `takerBuyBaseVolume` | limit<100→1，<500→2，≤1000→5，>1000→10 | 1500 / 全史 | 收盘补齐；批量走 data.binance.vision |
| `/fapi/v1/markPriceKlines` | 同上 | 标记价 OHLC | 同上 | 1500 | 断线补齐 |
| `/fapi/v1/ticker/24hr` | 不传 symbol = 全市场 | `priceChangePercent, quoteVolume` | 1 单币 / **40 全市场** | 快照 | 1 min 全市场 |
| `/fapi/v1/exchangeInfo` | – | `symbols[].filters, contractType, status` | 1 | – | 1 h；`contractType` 现有 `TRADIFI_PERPETUAL`（美股类永续），默认只取 `PERPETUAL` |
| `/fapi/v1/leverageBracket` | **需签名（USER_DATA）** | `brackets[].maintMarginRatio` | 1 | – | **不采**；MMR 用 Bybit `risk-limit` 近似 |

WebSocket `wss://fstream.binance.com/stream?streams=`：`!markPrice@arr@1s`（全市场标记价 + 费率，1 s）、`!forceOrder@arr`（爆仓，**每符号每秒只推最大一笔**，是下界，`throttled_source=true`）、`<symbol>@kline_1m`（前 50 币）、`!ticker@arr`（1 s，可替代 24hr REST）。连接规则：每连接 ≤ 1024 流；每 IP 每 5 min ≤ 300 次连接；24 h 强制断开（要主动轮换）；服务端每 3 min ping，10 min 内须 pong；入站 ≤ 5 条/s。

限速规则（官方）：`REQUEST_WEIGHT` 每 IP **2400 / min**，响应头 `X-MBX-USED-WEIGHT-1M` 可读；超限 429，继续打 → **418 封 IP**（`Retry-After`，2 min 到 3 天递增）。`fundingRate` / `fundingInfo` 另有 500 次 / 5 min 的独立桶。批量历史：`data.binance.vision/data/futures/um/{daily,monthly}/` 有 `klines, markPriceKlines, indexPriceKlines, premiumIndexKlines, fundingRate, metrics（OI + 多空比打包）, aggTrades, trades, bookDepth, bookTicker`，无鉴权无限速，本地实测可下。**2 年日线、90 天小时线、30 天以前的多空比历史一律走这里，不走 REST。**

### 3.2 Bybit v5（`https://api.bybit.com`；美国出口 403，全部镜像域名亦 403，实测）
文档：bybit-exchange.github.io/docs/v5（核对）。

| 端点 | 关键参数 | 我们用的字段 | 限 / 史 | 采集频率 |
|---|---|---|---|---|
| `/v5/market/tickers` | `category=linear`，不传 symbol = 全市场 | `markPrice, indexPrice, fundingRate, nextFundingTime, openInterestValue, turnover24h, price24hPcnt` | 快照 | **1 次覆盖行情 + 费率 + OI**，1 min |
| `/v5/market/open-interest` | `symbol, intervalTime(5min…1d), limit≤200, cursor` | `openInterest` | 200 / 自上市起 | 冷启动 |
| `/v5/market/account-ratio` | `symbol, period(5min…1d), limit≤500` | `buyRatio` | 500 / 2020-07 起 | 5 min（逐币） |
| `/v5/market/funding/history` | `symbol, limit≤200` | `fundingRate, fundingRateTimestamp` | 200 / 未明 | 冷启动 |
| `/v5/market/kline` | `symbol, interval, limit≤1000` | OHLCV | 1000 | 备选参考所 |
| `/v5/market/mark-price-kline` | 同上 | 标记价 OHLC | 1000 | 断线补齐 |
| `/v5/market/instruments-info` | `category=linear, limit≤1000` | `fundingInterval(分钟), leverageFilter, priceFilter, status` | – | 1 h |
| `/v5/market/risk-limit` | `symbol` | `riskLimitValue, maintenanceMargin, maxLeverage` | – | **公开**，日更；是 MMR 档的主来源 |
| `/v5/market/recent-trade` | `symbol, limit≤1000` | – | – | 不采 |

WebSocket `wss://stream.bybit.com/v5/public/linear`：`tickers.{symbol}`（100 ms 快照 + 增量，含费率与 OI 值）、`allLiquidation.{symbol}`（500 ms，**全部爆仓**；旧 `liquidation.{symbol}` 已弃用且每秒每符号只 1 条）、`kline.1.{symbol}`、`publicTrade.{symbol}`。规则：`args` 总长 ≤ 21,000 字符；每 20 s ping；每域名每 5 min ≤ 500 次连接；每 IP 并发连接数未明写。

限速规则（官方）：IP 级 **600 请求 / 滚动 5 s**，超限 **403 "access too frequent"**，封禁 ≥ 10 min。没有按端点的权重表。未明：`funding/history` 保留期、并发连接上限。

### 3.3 OKX（`https://www.okx.com/api/v5`）
文档：www.okx.com/docs-v5/en（整页抓取后逐段核对；限速按端点各自规定，键为 IP 或 IP + 合约）。

| 端点 | 关键参数 | 我们用的字段 | 限速（官方） | 限 / 史 | 采集频率 |
|---|---|---|---|---|---|
| `/market/tickers?instType=SWAP` | – | `last, vol24h, volCcy24h, open24h` | 20 / 2 s，IP | 全市场一次 | 1 min |
| `/public/open-interest?instType=SWAP` | 不传 instId = 全部 | `oi, oiCcy, oiUsd` | 20 / 2 s，IP + 合约 | 全市场一次 | 1 min |
| `/public/funding-rate?instId=ANY` | `ANY` = 全部永续 | `fundingRate, nextFundingRate, fundingTime, nextFundingTime, premium` | 10 / 2 s，IP + 合约 | 全市场一次 | 1 min |
| `/public/funding-rate-history` | `instId, limit≤400` | 同上 | 10 / 2 s | 400 / **3 个月** | 冷启动 |
| `/market/candles` / `/market/history-candles` | `instId, bar, limit≤300` | OHLCV | 40 / 2 s；20 / 2 s | 300 / 最近 1440 条；history 数年 | 备选 |
| `/market/mark-price-candles` | 同上，`limit≤100` | 标记价 OHLC | 20 / 2 s | 100 | 断线补齐 |
| `/public/position-tiers?instType=SWAP&tdMode=cross&instId=` | ≤ 5 合约 / 次 | `tier, mmr, imr, maxLever, maxSz` | 未明写 | – | 日更，MMR 主来源之一 |
| `/public/instruments?instType=SWAP` | – | `ctVal, ctMult, ctValCcy, lever, tickSz, state` | 20 / 2 s | – | 1 h |
| `/rubik/stat/contracts/long-short-account-ratio-contract` | `instId, period(5m…), limit≤100` | `[ts, ratio]` | **5 / 2 s**，IP + 合约 | 100 / 最近 1440 条 | 5 min |
| `/rubik/stat/contracts/long-short-position-ratio-contract-top-trader` | 同上 | `[ts, ratio]`（前 5% OI 账户，持仓口径） | 5 / 2 s | 100 | 5 min |
| `/rubik/stat/contracts/long-short-account-ratio-contract-top-trader` | 同上 | 账户口径 | 5 / 2 s | 100（文档注明历史截至 2024-03-22） | 备选 |
| `/rubik/stat/taker-volume-contract` | `instId, period, unit=2(USDT)` | `[ts, sellVol, buyVol]` | 5 / 2 s | 100 / 1440 条 | 5 min |
| `/rubik/stat/contracts/open-interest-history` | `instId, period` | `[ts, oi, oiUsd]` | 10 / 2 s | 100；**历史冻结在 2024 年初，不滚动** | 不用 |
| `/rubik/stat/contracts/long-short-account-ratio`、`/rubik/stat/taker-volume` | `ccy, period` | 按币种（含全所） | 5 / 2 s，IP | 5m 2 天、1H 30 天、1D 180 天 | v0.1 在用，改用 `-contract` 版 |
| `/public/liquidation-orders?instType=SWAP&state=filled&uly=` | – | – | – | **当前文档已无此端点**，本机仍返回 200 → 视为已弃用，不依赖 | 用 WS |
| `/copytrading/public-lead-traders` | `instType=SWAP, sortType, limit≤20` | `ranks[]{uniqueCode, aum, pnl, pnlRatio, winRatio, copyTraderNum}` | 5 / 2 s，IP，**文档标 Public** | 20 / 页 | v1.1，1 h |
| `/copytrading/public-current-subpositions` | `uniqueCode, limit≤100` | `subPosId, instId, posSide, lever, upl, markPx, openAvgPx` | 5 / 2 s | 100 | v1.1，10 min |
| `/copytrading/public-subpositions-history` | `uniqueCode` | 已平仓 | 5 / 2 s | 3 个月 | v1.1 |

WebSocket：`wss://ws.okx.com:8443/ws/v5/public` 上 `tickers`、`trades`、`mark-price`、`liquidation-orders`；**K 线（`candle1m`、`mark-price-candle1m`）在 `wss://ws.okx.com:8443/ws/v5/business`**。连接：每 IP 3 次 / s；每连接每小时 ≤ 480 次订阅 / 退订 / 登录；单条订阅消息 ≤ 64 KB；30 s 无数据须 `ping`（文本）。超限错误码 `50011`。

### 3.4 Gate（`https://api.gateio.ws/api/v4/futures/usdt`）
文档：gate.com/docs/developers/apiv4（页面为前端渲染，字段由官方 `gateio/rest-v4` OpenAPI 交叉核对）。

| 端点 | 关键参数 | 我们用的字段 | 限 / 史 | 采集频率 |
|---|---|---|---|---|
| `/contract_stats` | `contract, interval(5m/1h/1d), limit, from` | `lsr_taker, lsr_account, top_lsr_account, top_lsr_size, long_liq_usd, short_liq_usd, long_liq_size, open_interest_usd, mark_price` | 默认 30，上限未明 | **一次请求给全部比率 + 爆仓聚合 + OI**：核心 1 min，浅层 5 min |
| `/tickers` | 不传 contract = 全部 | `mark_price, index_price, funding_rate, funding_next_apply?, volume_24h_quote, change_percentage` | 全市场一次 | 1 min |
| `/contracts`、`/contracts/{contract}` | – | `funding_interval, funding_next_apply, quanto_multiplier, order_price_round, maintenance_rate(通用)` | 列表默认 100 | 1 h |
| `/risk_limit_tiers` | `contract` | `tier, risk_limit, maintenance_rate, leverage_max` | 默认 100 | 日更，MMR 来源 |
| `/liq_orders` | `contract, from, to, limit≤1000（实测）` | `time, size, order_price, fill_price` | 保留期未明；触到 limit 必须收窄窗口再取（`hlens` 断言 `gate.paginate_on_limit_hit`） | 15 min 轮询兜底 |
| `/candlesticks`、`/premium_index` | `contract, interval, limit` | OHLCV / 溢价 OHLC | 默认 100，上限未明 | 备选 |
| `/funding_rate` | `contract, limit` | `t, r` | 默认 100 | 冷启动 |

WebSocket `wss://fx-ws.gateio.ws/v4/ws/usdt`：`futures.tickers`、`futures.trades`、`futures.candlesticks`、爆仓 **`futures.public_liquidates`**（1 s 聚合或攒满 20 条即推）。连接 ≤ 300 / IP。

限速（官方 vs 实测）：官方 `rest-v4` README 写公开端点 **300 次 / s / IP**；本机实测响应头 `x-gate-ratelimit-limit: 200`、`x-gate-ratelimit-requests-remain`、`x-gate-ratelimit-reset-timestamp`（窗口按 reset 时间戳推为 10 s）。**以响应头为准动态记账**，预算 40 / 10 s。

### 3.5 Bitget（`https://api.bitget.com/api/v2/mix/market`）
文档：bitget.com/api-doc（页面前端渲染，抓不到正文，以下大半来自索引摘要，**置信度最低**，S0 用自检实测校准）。

| 端点 | 关键参数 | 我们用的字段 | 限速 | 限 / 史 | 采集频率 |
|---|---|---|---|---|---|
| `/tickers?productType=USDT-FUTURES` | – | `markPrice, fundingRate, holdingAmount, usdtVolume, change24h` | 20 / s / IP | 全市场一次 | 1 min |
| `/open-interest` | `symbol` | `openInterestList[].size` | 20 / s | – | tickers 已含，不单独拉 |
| `/current-fund-rate`、`/funding-time` | `symbol` | `fundingRate, fundingRateInterval, nextFundingTime` | 未验证 | – | 1 h（周期） |
| `/history-fund-rate` | `symbol, pageSize≤100` | `fundingRate, fundingTime` | 20 / s | 100 | 冷启动 |
| `/account-long-short`、`/position-long-short`、`/taker-buy-sell`、`/long-short` | `symbol, period` | `longAccountRatio`；`longPositionRatio`；`buyVolume, sellVolume` | 未验证（一处写 1 / s） | 冷门币为空（v0.1 实测）；ZEC 有数据（2026-09-12） | 5 min |
| `/candles`、`/history-candles`、`/history-mark-candles` | `symbol, granularity, limit≤200` | OHLCV | 20 / s | 200；1m 约 1 个月、1H 约 83 天 | 备选 |
| `/contracts` | `productType` | `fundInterval, sizeMultiplier, minTradeNum` | 未验证 | – | 1 h |
| `/query-position-lever` | `symbol` | 档位、`maxLever`、MMR | 未验证 | – | 日更 |

WebSocket：当前公开地址 **`wss://ws.bitget.com/v2/ws/public`**（`hlens` 仓库里写的 `/v3/ws/public` 在现文档中查不到，S0 实测哪个能连）；频道 `ticker`、`candle1m`、`trade`；爆仓频道名未核到。心跳文本 `ping` / 30 s。订阅上限来源冲突（1000 频道 / 连接 + 240 次订阅 / h，或 100 连接 / IP）。整体上限另有"6000 次 / min / IP，超限锁 5 min"的说法，非主文档。

### 3.6 Hyperliquid（POST `https://api.hyperliquid.xyz/info`；WS `wss://api.hyperliquid.xyz/ws`）
文档：hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits（核对）。实测常数来自 `hlens`（`hyperliquid/budget.py`）与 `hlens-hub`（`hub/quota.py`）。

| `type` | 请求字段 | 我们用的响应字段 | 权重 | 限 | 采集频率 |
|---|---|---|---|---|---|
| `meta` | – | `universe[].name, szDecimals, maxLeverage`, `marginTables` | 20 | – | 1 h |
| `metaAndAssetCtxs` | – | `funding（每小时）, openInterest, markPx, oraclePx, premium, dayNtlVlm, impactPxs` | 20 | 全市场一次 | 1 min |
| `allMids` | – | 币 → 中间价 | 2 | – | WS 替代 |
| `predictedFundings` | – | 各所预测费率 | 20 | – | 5 min |
| `fundingHistory` | `coin, startTime, endTime` | `fundingRate, premium, time` | 每 20 条 +1 | 官方未写上限 | 冷启动 |
| `candleSnapshot` | `req{coin, interval, startTime, endTime}` | `t,o,h,l,c,v,n` | 每 60 条 +1 | 5000（第三方文档，未验证） | 备选参考所 |
| `clearinghouseState` | `user` | `assetPositions[].position{coin, szi, entryPx, positionValue, unrealizedPnl, liquidationPx, leverage{type,value}, marginUsed, maxLeverage}`, `marginSummary.accountValue, totalNtlPos`, `withdrawable` | **2** | – | hot 200 每 1 min；warm 800 每 10 min |
| `userFills` | `user, aggregateByTime` | fill 对象（下） | 每 20 条 +1（上界 120） | 最近 2000（第三方，未验证） | **不用**（new→old，不与 ByTime 混用） |
| `userFillsByTime` | `user, startTime, endTime, aggregateByTime` | fill 对象 | 每 20 条 +1 | 每次 ≤ 2000；总保留约 10,000（未验证） | warm 每 10 min；断线补齐 |
| `userFunding` | `user, startTime, endTime` | `coin, fundingRate, szi, usdc` | 每 20 条 +1 | – | v1.1 |
| `userNonFundingLedgerUpdates` | `user, startTime, endTime` | 入金 / 出金 / 转账 | 每 20 条 +1 | – | v1.1（权益曲线拆分） |
| `l2Book`、`recentTrades`、`liquidatable` | – | – | 2 / 每 20 条 / **实测 ≈ 16，记 20** | – | 不采；`liquidatable` 与 `recentTrades` 官方页面未列，**未验证** |
| `portfolio`、`userRateLimit`、`perpsAtOpenInterestCap` | `user` | – | 20 | – | 按需 |

fill 对象（2026-09-12 实测）：`cloid, closedPnl, coin, crossed, dir, fee, feeToken, hash, oid, px, side, startPosition, sz, tid, time, twapId`，另有 `liquidation{liquidatedUser, markPx, method}`（6000 条中 21 条带）、`builderFee`（偶现）。约 28% 的成交 `hash` 为全零，不是爆仓标记。`clearinghouseState.assetPositions[].position` 键：`coin, cumFunding, entryPx, leverage, liquidationPx, marginUsed, maxLeverage, positionValue, returnOnEquity, szi, unrealizedPnl`。陷阱：`tid` 不唯一（hub 实测 207,635 条 `tid=0`）；`"liquidation": null` 键存在。

排行榜 `GET https://stats-data.hyperliquid.xyz/Mainnet/leaderboard`：官方前端同源、**无文档**、CloudFront 静态 JSON、不吃 `/info` 权重；`leaderboardRows[]{ethAddress, accountValue, windowPerformances[[day|week|month|allTime, {pnl, roi, vlm}]]}`。`accountValue` 含现货，不可作永续分层依据。

WebSocket 订阅：`allMids`、`trades{coin}`、`candle{coin,interval}`、`userFills{user}`、`userEvents`、`userFundings`、`activeAssetCtx{coin}`、`bbo{coin}`、`clearinghouseState`。官方 WS 限制：**每 IP ≤ 10 连接、每分钟 ≤ 30 次新连接、≤ 1000 订阅、≤ 10 个不同 user、≤ 2000 消息/min、≤ 100 个在飞 post**。**注意"每 IP ≤ 10 个不同 user"**：这条与 `03-DEVELOPMENT`（已删除，见 git 历史 `c8e70fe`）§4.1"WS `userFills` 订阅 hot 200 钱包"冲突，见 §7。WS 不消耗 `/info` 权重（文档结构隐含，非原文）。

限速规则（官方 + 实测）：`/info` 每 IP **1200 权重 / min**；权重 2 组 = `l2Book, allMids, clearinghouseState, orderStatus, spotClearinghouseState, exchangeStatus`；`userRole` 60；其余 20；历史类每 20 条 +1（`candleSnapshot` 每 60 条 +1）。响应头**没有任何限速字段**。实测：① 单出口容量约 2350–2990 权重/min，比文档宽，**记账仍按 1200**；② 两种 429：响应体 JSON `null` = 权重限速 → 退避权重；nginx HTML 页 = 连接速率限速 → 降并发，`max_inflight` 硬顶 10，超过吞吐反降；③ 串行只能用掉 25% 预算，8 线程刚好打满；④ AIMD：撞 429 砍到 75% 冻结 1 h。地址级限速只针对下单，读接口没有。

### 3.7 宏观与其它

| 来源 | 端点 | 字段 | 鉴权 | 限速（官方） | 频率 |
|---|---|---|---|---|---|
| alternative.me | `GET https://api.alternative.me/fng/?limit=N&format=json` | `value, value_classification, timestamp`；`limit=0` 全史 | 无 | 未公布 | 1 h |
| CoinGecko | `GET https://api.coingecko.com/api/v3/global` | `total_market_cap, market_cap_percentage` | Demo key（`x-cg-demo-api-key`）建议带 | Demo **100 次/min**（官方常见错误页）；月度上限未在官方页核到（社区说 10,000）；无 key 走共享 IP 桶 | 10 min |
| CoinGecko | `/coins/markets?vs_currency=usd&per_page=250&page=1..2` | `market_cap, circulating_supply` | 同上 | 同上 | 10 min，2 次 |
| DefiLlama | `GET https://stablecoins.llama.fi/stablecoins?includePrices=true` | `peggedAssets[].circulating.peggedUSD, circulatingPrevDay/Week/Month` | 无 | 未公布 | 1 h |
| Deribit | `GET https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=3600&start_timestamp&end_timestamp` | `data[[t,o,h,l,c]]`（DVOL） | 无 | 信用桶：每请求 500 信用，池 50,000，回填 10,000/s ≈ 20 req/s；`get_instruments` 每请求 10,000 信用；超限 `too_many_requests`(10028) 并断会话 | v1.1，5 min |
| Deribit | `get_book_summary_by_currency?currency=BTC&kind=future` | `instrument_name, mark_price, open_interest, funding_8h` | 无 | 同上 | v1.1，5 min |
| Binance 批量 | `https://data.binance.vision/data/futures/um/daily/{klines,metrics,fundingRate,markPriceKlines}/{SYMBOL}/…zip` | 见 3.1 | 无 | 无 | 冷启动 + 每日 |

## 4. 限速总表与东京预算（`venues.yaml` 的依据）

| 来源 | 官方上限（每出口 IP） | 记账口径 | 我们的预算 | 超限行为 | 响应头 |
|---|---|---|---|---|---|
| Binance | 2400 权重 / min；`fundingRate` 类另 500 次 / 5 min | 权重 | 960 / min（40%） | 429 → 继续打 418 封 IP 2 min–3 天 | `X-MBX-USED-WEIGHT-1M`（实测可读） |
| Bybit | 600 次 / 滚动 5 s | 请求数 | 120 / min（远低于上限，留给多机共出口） | 403 + 封 ≥ 10 min | 无 |
| OKX | 按端点 5–40 次 / 2 s（§3.3） | 请求数 / 端点 | 每端点 4 次 / 2 s | `50011` | 无 |
| Gate | 200 次 / 10 s（实测头 `x-gate-ratelimit-limit: 200`） | 请求数 | 40 / 10 s | 429 | `x-gate-ratelimit-requests-remain / reset-timestamp`（实测可读） |
| Bitget | 20 次 / s（§3.5，低置信） | 请求数 | 8 次 / s | 429 | 无 |
| Hyperliquid `/info` | 1200 权重 / min | 权重（历史类按条数） | 1080（90%，hub 定稿纪律）；`max_inflight` 10 | 429 两种（JSON null = 权重；HTML = 连接） | **无** |
| Hyperliquid WS | 10 连接 / IP，30 新连接 / min，1000 订阅，**10 个不同 user**，2000 消息 / min | 订阅数 | 8 连接、≤ 800 订阅、≤ 8 user | 断连 | – |
| CoinGecko | Demo 100 次 / min | 请求数 | 10 / min | 429 | – |
| Deribit | 500 信用 / 请求，池 50,000，回填 10,000 / s | 信用 | 5 次 / s | `10028` 断会话 | – |
| alternative.me、DefiLlama、data.binance.vision | 未公布 | 请求数 | 各 ≤ 1 次 / min | – | – |

**东京单出口的稳态用量（核心 5 币深层 + 200 币浅层，见 §8）：**

| 来源 | 项 | 用量 / min | 占预算 |
|---|---|---|---|
| Binance | 全市场 `premiumIndex` 10 + `ticker/24hr` 40（WS 正常时只作对账，每 5 min）≈ 10；OI 核心 5×1 + 浅层 200÷5 = 45；四类比率核心 5×4÷5 = 4 + 浅层 200×4÷15 ≈ 53（W=0 但计请求）；K 线补齐预留 100 | ≈ 210 权重 + 57 请求 | 22% |
| Bybit | `tickers` 全市场 1；`account-ratio` 核心 1 + 浅层 13 | ≈ 15 | 13% |
| OKX | `tickers` 1 + `open-interest` 1 + `funding-rate?instId=ANY` 1；rubik 核心 5×2÷5 = 2 + 浅层 200÷15 ≈ 13 | ≈ 18（分散在 5 个端点） | 低 |
| Gate | `contract_stats` 核心 5 + 浅层 40 | ≈ 45 | 19% |
| Bitget | `tickers` 1；两类比率核心 2 + 浅层 27 | ≈ 30 | 低 |
| HL `/info` | `metaAndAssetCtxs` 20；`clearinghouseState` hot 200×2 = 400 + warm 800÷10×2 = 160；`userFillsByTime` 补齐 ≤ 20 次 × ~25 = 500 上界 | ≤ 1080 | **100%，紧** |
| HL WS | `allMids` 1 + `trades` 35 币 + `candle` 5 币 + `userFills` ≤ 8 user | ≈ 50 订阅 | 5% |

HL 是唯一紧的来源，而且**官方 WS 限制每 IP 只能订阅 10 个不同 user**，`03-DEVELOPMENT`（已删除，见 git 历史 `c8e70fe`）§4.1 写的"WS `userFills` 订阅 hot 200"做不到。替代方案（S0 验证）：WS `trades{coin}` 的每条成交带 `users: [buyer, seller]` 两个地址，订阅核心币与前 30 币的 `trades` 就能零权重看到这些币上**所有钱包**的成交；hot 钱包再用 `clearinghouseState` 每分钟对账，仓位有变化才拉 `userFillsByTime`。这样 200 hot 钱包不需要 200 个 user 订阅。此方案若验证通过，`03` §4.1 与 `05` 的 whale-engine 描述要改。

## 5. 出口可达性矩阵（实测）

| 端点族 | GitHub Actions（美国） | 本机 WSL（196.188.x，2026-09-12） | hub 笔记本（196.190.222.243 / AS24757） | 东京 / 新加坡 VPS |
|---|---|---|---|---|
| `fapi.binance.com` | **451** | 200 | 200（hlens 实测） | S0 |
| `www.binance.com/fapi/*`、`/futures/data/*` | 200 | 200 | – | S0 |
| `data.binance.vision` | 200 | 200 | – | S0 |
| `api.bybit.com` 及 5 个镜像域 | **403 全部** | 200 | – | S0 |
| `www.okx.com/api/v5/*`（含 rubik、liquidation-orders） | 200 | 200 | 200 | S0 |
| `www.okx.com/api/v5/copytrading/public-*` | – | **超时（000）** | – | S0，可能需鉴权或按地区限制 |
| `api.gateio.ws`、`fx-api.gateio.ws` | 200 | 200 | 200 | S0 |
| `api.bitget.com` | 200 | 200 | 200 | S0 |
| `api.hyperliquid.xyz`、`stats-data.hyperliquid.xyz` | 200 | 200 | 200 | S0 |
| CoinGecko、DefiLlama、alternative.me、Deribit | 200 | 200 | – | S0 |

结论：本机可以完整开发和测试全部适配器的 **REST** 部分，不需要云端跑测试。**例外：Binance WS 从本机出口连得上、订阅有 ACK、但不推任何数据**（2026-09-12 实测，`/stream`、`/ws/<stream>`、SUBSCRIBE 三种方式都一样；同一分钟 HL、OKX、Gate、Bybit 的 WS 正常），Binance WS 的 fixtures 标 `source: documented`，要在东京出口重录。另：本机时钟比五所快约 1.35 s，超过 §6-5 的 1 s 门槛，是 WSL 的问题，东京要开 chrony。Actions 只能做快照兜底；东京、新加坡待 S0 核实（同时确认东京出口不在 hub 的 `exit_ip_budget` 表里）。

## 6. 采集器自检（preflight）要测什么

启动时跑一次、之后每小时一次，结果写 `source_health(venue, kind, ...)` 与 `source_health_1m`，状态页公示。来源：`hlens` 的 `node/preflight`、`egress`、`country` 三个模块，抽进 `hlens-core`。

1. **出口**：公网 IP、ASN、国家；与 hub `exit_ip_budget` 表比对，命中即拒绝启动（两套限速器互不知情）。
2. **可达**：每来源一个最便宜的探针（Binance `ping`、Bybit `time`、OKX `time`、Gate `contracts/BTC_USDT`、Bitget `time`、HL `meta`），记录 HTTP 码、延迟、是否 451/403 地域拒绝。
3. **覆盖**：对 `instruments` 中每个（所，币），探一次每类端点是否返回数据（比率类端点冷门币会空），生成"所 × 币 × 端点"覆盖矩阵，空的分面在 `Facet.tag` 标 `insufficient`。
4. **限速头**：Binance `X-MBX-USED-WEIGHT-1M`、Gate `x-gate-ratelimit-*` 读取并校准本地账本；HL 无头，只能按 429 类型反推。
5. **时钟**：与交易所 `serverTime` 偏差 < 1 s。
6. **WS**：每来源建一条连接收到首条消息的时延；HL 记录当前连接数与订阅数距上限的余量。
7. **存储**：DB 与 Redis 可写、磁盘余量 > 20%。

任一项失败 → 该来源 `ok=false`、熔断，其余来源照常，页面对应分面置灰。

## 7. 缺口与待决

| # | 问题 | 处置 |
|---|---|---|
| 1 | HL WS 每 IP 10 个 user 的限制与"hot 200 订阅"冲突 | **已验证（`docs/reports/2026-09-12-hl-ws-trades.md`）**：`trades{coin}` 每条带 `users:[buyer,seller]`，与 `userFillsByTime` 的 `tid`/`hash` 逐条一致；35 币约 250 msg/min，远低于 2000/min。但它不带 `dir/closedPnl/fee/startPosition/oid/liquidation`，且只覆盖已订阅币。定案：`trades` 发现 + 每分钟 `clearinghouseState` 对账 + 仓位有变才拉 `userFillsByTime` 补全字段；`userFills` WS 只给 ≤ 8 个关注度最高的钱包。`03` §4.1 待改 |
| 2 | HL `userFillsByTime` 单次 2000 条上限**已实测**（5 个高频地址 24h 窗口全部截断）；保留 10,000 条、`candleSnapshot` 5000 条、`liquidatable` 与 `recentTrades` 仍非官方原文 | 高频钱包窗口要小于 24h；记账按上界；`liquidatable` 不用 |
| 3 | Binance `leverageBracket` 需签名 | MMR 用 Bybit `risk-limit` + OKX `position-tiers` + Gate `risk_limit_tiers`，Binance 档位近似 Bybit，输出标 `model_estimated` |
| 4 | Binance 多空比只保留 30 天 | 30 天分位冷启动够用；更长历史靠 `data.binance.vision/metrics` 打包 + 自采累积 |
| 5 | Bybit 全域 403（美国出口） | 采集只在非美出口；Actions 快照不含 Bybit |
| 6 | OKX 带单公开端点本机超时 | S0 核实鉴权与地区；v1.1 才用 |
| 7 | CoinGecko 月度上限未核到 | 申请 Demo key，每 10 min 3 次 ≈ 13k/月，若超则改 30 min |
| 8 | 各所比率口径不同 | 分 kind 分所存，OI 加权聚合并标 `sources`，不做无权重平均 |
| 9 | hub 5.29 亿条成交只有一份 | 冷层：NAS（4 TB）作全量副本 + R2 作异地副本（只放 Parquet 与 pgbackrest，≈ 50 GB） |

## 8. 币种范围：核心深层 + 全市场浅层

限速不是决定币种数量的因素：全市场端点（Bybit `tickers`、OKX `tickers`/`open-interest`、Binance `premiumIndex`、HL `metaAndAssetCtxs`）一次请求覆盖所有币，价格、费率、OI 的成本与币数无关。随币数增长的只有逐币端点（多空比、Binance OI、K 线），按 §4 算 200 币也只用到预算两成。真正限制"精通"的是三样：分析质量（状态字典每币要 n ≥ 100 才能命名）、大户覆盖（HL 权重与 WS user 上限）、人力（每个核心币要写模板、校准、看结果）。

所以按两层设计，写进 `instruments.tier`：

| 层 | 币 | 采什么 | 页面 |
|---|---|---|---|
| **core** | BTC、ETH、SOL、HYPE、ZEC（五所 + HL 均有永续，比率端点五所均有数据，2026-09-12 实测） | 全部分面、五所 + HL、1 min；HL 大户仓位与成交；K 线 1m；关键位、市场环境、状态、记分板、告警全开 | 币页全部区块 |
| **broad** | 各所 OI 前 200 的并集 | 只采全市场端点给的价格、费率、OI，以及 5–15 min 的比率；不跑大户、关键位、状态 | 首页广度、`/find` 选币、热力图；币页只有头部与光谱，其余区块显示"未深度覆盖" |

扩币的顺序是 broad → core 提级，条件是该币 30 天数据齐、比率端点有数、HL 有永续；提级只改配置，不改代码。加服务器不是扩币的前提，是加 HL 出口 IP 的前提。

## 9. 与其它文档的关系

- `03-DEVELOPMENT`（已删除，见 git 历史 `c8e70fe`）§4.1 的限速预算段以本文 §4 为准；§4.1 "WS `userFills` hot 200" 待 §7-1 验证后改写。
- `05-DOCKER`（已删除，见 git 历史 `c8e70fe`）硬规则 2（出口 IP）与本文 §5、§6-1 一致；两机方案已被 `00-PROJECT.md` §7.1 的单机四容器取代。
- `00-PROJECT` §4.4 新鲜度预算是本文 §1 的输入。
- `venues.yaml`（S0 产出）直接从 §3、§4 抄常数，附来源列（官方 / 实测 / 未验证）。
