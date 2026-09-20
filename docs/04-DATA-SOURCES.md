# 数据源参考（Binance + Hyperliquid）

> 范围：M1–M3 只采 Binance USDⓈ-M 永续与 Hyperliquid 永续的分钟级行情；M4 起对白名单币增采逐笔、盘口、现货（§9）；M5 起增采 Hyperliquid 钱包链路（§10）。字段名以采集器 contracts 为准。
>
> 来源标签：`官方` = 官方文档原文 · `实测` = 本机探测记录 · `估算` = 由本文算式推导 · `未验证` = 官方未写明且未实测，按保守值记账。核对日期：**2026-09-12** 初核、**2026-09-19** 复核（复核方式为读官方文档页，**未发起任何交易所 API 调用**）。**表名一律以 `03-ARCHITECTURE.md §5` 为准**，本文不另起表名。
>
> 三条硬规则（**① 于 2026-09-19 因生产机变更而改写**）：① 限速按**公网出口 IP** 记账，同一 IP 下所有进程共享一份预算；**生产就是 hub 那台机器，出口与 hub 旧采集器、与开发机三者相同**，因此额度是**一份预算、两个消费者**，本项目可用量 = 官方额度 − hub 旧采集器实际占用（§4、§6）；② 预算按官方上限的 **40%** 使用（Hyperliquid 取官方值 1200 的 **90%**）——**这个比例是整个出口 IP 的合计上限，不是本项目单独可用量**；③ 每个来源启动时跑一次自检（§7），结果写 `source_health`。

## 1. 数据类别与依赖的功能

| 类别 | 用途 / 依赖功能 | 落库表（03 §5） | 首选端点 | 覆盖 |
|---|---|---|---|---|
| 标记价、指数价、24h 涨跌额 | 首页价格、跨所分歧 F7 | `market_1m`（`mark/index/premium/chg24h_pct/vol24h_usd`） | Binance WS `!markPrice@arr@1s`；HL WS `allMids` | 全市场一次 |
| K 线 1m/1h/1d · 主动买入量 | 市场环境、历史分位 F8、记分板、taker 买卖比替代 | 回补写入 `market_1m`（带 `semantic`/`grid_s`/`backfilled`），**无独立 K 线表**；`takerBuyBaseVolume` **03 §5 无对应列**（见 §12） | Binance `/fapi/v1/klines`（字段 `takerBuyBaseVolume`）；HL `candleSnapshot`（深度受限，见 §5） | 逐币 |
| 资金费率（当前 + 预测 + 历史） | 分歧榜 F7、历史分位 F8、每日摘要 F10 | `market_1m`（`funding_rate` 存该所原生周期原值 + `funding_interval_h` + `next_funding_ts`），**无 `funding_pred` 表** | Binance `/fapi/v1/premiumIndex` + `/fapi/v1/fundingInfo`；HL `metaAndAssetCtxs.funding` + `predictedFundings` | 全市场一次 |
| 持仓量（当前 + 历史） | 分歧榜 F7、历史分位 F8 | `market_1m`（`oi_base`/`oi_usd`） | HL `metaAndAssetCtxs.openInterest`（全市场一次）；Binance `/fapi/v1/openInterest`（**仅逐币**）+ `/futures/data/openInterestHist`（历史） | HL 全市场一次；Binance 逐币 |
| 多空比 / 主动买卖比（仅 Binance） | 币卡单所数字（F3：HL 该所不发布） | `ls_ratio`（`kind` 三类） | Binance `/futures/data/{globalLongShortAccountRatio,topLongShortPositionRatio,takerlongshortRatio}`（**三类，不是四类**——`topLongShortAccountRatio` 已由 `03 §15` 划掉，没有确认功能消费它） | 逐币 |
| 逐笔爆仓 / 爆仓聚合 | 爆仓事件流 F12（恒带 `下界`） | `liquidations`（`completeness=lower_bound`） | Binance WS `!forceOrder@arr`（下界）；HL 无公开全所级流，见 §8 | 下界采样 |
| 合约元数据（符号、乘数、MMR 档、结算周期） | 币种范围 F1、口径对齐 F3、关键位（M6） | `instruments` · `coin_universe` | Binance `exchangeInfo` + `fundingInfo`；HL `meta`（`szDecimals/maxLeverage`），MMR ≈ 1/(2·maxLeverage) | 全市场一次 |
| **（M4）** 逐笔→CVD · 盘口 · 现货 ／ **（M5）** 大户持仓 · 成交 · 资金费流水 | F20–F22 ／ F23–F27 | `trade_tick`（滚动 ≤7 天）→ 派生 `cvd_1m` · `book_l2_1m` · `hf_whitelist`（`cvd_1m` 与现货列 **03 §5 尚未列**，见 §12）／ hub 导入表 + 现采 | 见 §9 ／ §10 | 白名单 ≤10 币 ／ TOP N 地址 |

全市场端点（Binance `premiumIndex`/`ticker/24hr`、HL `metaAndAssetCtxs`）一次请求覆盖全部币，新增币不增加权重；逐币端点（Binance `openInterest`、多空比、K 线）才随币数线性增长，预算见 §4。

**采集范围按里程碑分段（本条 2026-09-19 改写）**：**M1–M3 不采**盘口深度、逐笔成交、现货；**M4 起**这三项**只对 `hf_whitelist` 内的币（上限 10）采集**，且逐笔为**滚动留存 ≤7 天**（决定 A1）、盘口只留**每分钟一张 ±0.5% 快照**、现货**只取价格，不取现货盘口与现货逐笔**（F19–F22），M4 门控未通过则三项一律不开；**始终不采**期权逐合约、链上数据、Binance 的账户级持仓（不公开）。

## 2. Binance USDⓈ-M 接口明细

`https://fapi.binance.com`；美区出口用镜像 `https://www.binance.com` 同路径（实测 200，见 §6）。文档：developers.binance.com/docs/derivatives/usds-margined-futures（2026-09-19 复核，含 `/futures/data/*`）。`W` = 权重。

| 端点 | 关键参数 | 字段 | W | 限 / 史 | 频率 |
|---|---|---|---|---|---|
| `/fapi/v1/premiumIndex` | 不传 symbol = 全市场 | `markPrice, indexPrice, lastFundingRate, nextFundingTime` | 1 单币 / 10 全市场 | 快照 | 60s 兜底，主用 WS |
| `/fapi/v1/openInterest` | `symbol` 必填 | `openInterest` | 1 | 快照 | 逐币 1 min（180 币 = 180 W/min） |
| `/futures/data/openInterestHist` | `symbol, period(5m…1d), limit≤500` | `sumOpenInterestValue` | 不吃权重（无 `X-MBX-USED-WEIGHT` 头），**记入 `futures_data` 请求桶** | **1000 次/5min**（`官方`）；500 条 / 30 天 | 冷启动一次 |
| `/futures/data/{globalLongShortAccountRatio, topLongShortPositionRatio}` | 同上 | `longAccount` | 同上 | 同上 | **10 min，带 `limit` 取回中间点**（§4） |
| `/futures/data/takerlongshortRatio` | 同上 | `buyVol, sellVol` | 同上 | 同上 | 同上 |
| `/fapi/v1/fundingRate` · `/fapi/v1/fundingInfo` | 前者 `symbol` 可选、`limit≤1000` | `fundingRate, fundingTime` / `fundingIntervalHours, adjustedFundingRateCap` | 共用独立请求桶 **500 次/5min/IP** | 1000 / 全史；后者只列非默认周期币 | 冷启动 + 每 8h 对账 · 1 h |

| `/fapi/v1/klines` · `/fapi/v1/markPriceKlines` | `symbol, interval, limit≤1500` | OHLCV + `takerBuyBaseVolume` / 标记价 OHLC | <100→1, <500→2, ≤1000→5, >1000→10 | 1500 / 全史 | 收盘补齐 · 断线补齐 |
| `/fapi/v1/ticker/24hr` | 不传 symbol = 全市场 | `priceChangePercent, quoteVolume` | 1 单币 / 40 全市场 | 快照 | 1 min 全市场 |
| `/fapi/v1/exchangeInfo` · `/fapi/v1/leverageBracket` | 后者需签名（USER_DATA） | `symbols[].filters, contractType, status` / `brackets[].maintMarginRatio` | 各 1 | – | 前者 1 h（`contractType` 只取 `PERPETUAL`）；**后者不采集，无替代近似来源** |
| **（M4）** `/fapi/v1/depth` | `symbol, limit` | `lastUpdateId, E, T, bids[], asks[]` | 5/10/20/50→**2**；100→**5**；500→**10**；1000→**20** | 快照 | 白名单币 1 min（§9.2） |
| **（M4）** `/fapi/v1/aggTrades` | `symbol, limit≤1000` | `a,p,q,f,l,T,m` | **20** | **只最近 48 小时** | 仅断线回补 |
| `/fapi/v1/trades` · `/fapi/v1/historicalTrades` | `symbol`，`limit≤1000` / `≤500` | 原始逐笔 | **5** / **200** | 后者只最近 1 个月且**必须带 `X-MBX-APIKEY`** | 均**不采集**（用 WS；03 §3 为无鉴权设计） |

**WebSocket —— legacy 地址已停用（`官方`，2026-04-23 生效；来源：developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice，下称「变更公告」）**。`wss://fstream.binance.com/stream` **已永久停止推送**，现按用途拆为三组：

- **`/market`** = `wss://fstream.binance.com/market` —— `!markPrice@arr@1s` · `!forceOrder@arr` · `<symbol>@kline_1m` · `!ticker@arr` · `<symbol>@aggTrade`（M1/M2 + M4）
- **`/public`** = `wss://fstream.binance.com/public` —— `<symbol>@depth` · `<symbol>@depth{5,10,20}` · `<symbol>@bookTicker`（M4）
- **`/private`**（需签名）—— 本项目不使用

**两条直接后果**：① **这不只影响 M4** —— M1/M2 的 `!markPrice@arr@1s`（全市场标记价+费率）与 `!forceOrder@arr`（爆仓）都在 `/market` 组，照旧地址连接会连到已停推的端点；② M4 的 `@depth` 属 `/public`、`@aggTrade` 属 `/market`，**分属两组 → 至少两条 WS 连接**，不能合并成一条。

`!forceOrder@arr` 的口径不变：**每符号每秒只推最大一笔，是下界**（`官方`）。连接规则（每连接 ≤1024 流；每 IP 每 5min ≤300 次连接；24h 强制断开需主动轮换；服务端 3min ping，10min 内须 pong；入站 ≤5 条/s）来自拆分前的文档，**拆分后是否照旧未复核** → `未验证`（§11 第 9 项），暂按这些数字保守记账。

限速（`官方`）：`REQUEST_WEIGHT` 每 IP **2400/min**，响应头 `X-MBX-USED-WEIGHT-1M` 可读；超限 429，继续打则 **418 封 IP**（`Retry-After` 2min–3 天递增）。`/futures/data/*` **1000 次/5min**（`官方`，…/market-data/rest-api/Long-Short-Ratio 与 Open Interest Statistics 两页均写明）；`fundingRate`/`fundingInfo` **500 次/5min**；两者是否同一个桶官方未说明 → `未验证`（§11 第 10 项）。批量历史 `data.binance.vision/data/futures/um/{daily,monthly}/`（klines、markPriceKlines、fundingRate、metrics 等）无鉴权无限速，实测可下；**2 年日线、90 天小时线、30 天以前的比率历史一律走这里**。

## 3. Hyperliquid 接口明细

POST `https://api.hyperliquid.xyz/info`；WS `wss://api.hyperliquid.xyz/ws`。文档：hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/（rate-limits-and-user-limits · info-endpoint · websocket/subscriptions，2026-09-19 复核）。

| `type` | 请求字段 | 字段 | 权重 | 限 / 史 | 频率 |
|---|---|---|---|---|---|
| `meta` · `metaAndAssetCtxs` | – | `universe[].name, szDecimals, maxLeverage, marginTables` / `funding（每小时）, openInterest, markPx, oraclePx, premium, dayNtlVlm, impactPxs` | 各 20 | 全市场一次 | 1 h / 1 min |
| `allMids` · `predictedFundings` | – | 币→中间价 / 各所预测费率（含 Binance 的预测值） | 2 / 20 | – | WS 替代 / 5 min |
| `fundingHistory` | `coin, startTime, endTime` | `fundingRate, premium, time` | 每 20 条 +1 | 带区间的响应**只返回 500 个元素** | 冷启动 |
| `candleSnapshot` | `req{coin, interval, startTime, endTime}` | `t,o,h,l,c,v,n` | 每 60 条 +1 | **只保留最近 5000 根 K 线**；且带区间的响应**每次只返回 500 个元素** | 见 §5，**不能当全历史用** |
| `l2Book` | `coin`（可选 `nSigFigs`∈{2,3,4,5,null}、`mantissa`∈{1,2,5}，仅 `nSigFigs=5` 时） | `coin, levels:[bids,asks], time` | **2** | **每侧至多 20 档** | M4，见 §9.4 |

MMR 近似：HL ≈ 1/(2·maxLeverage)（官方近似公式，非独立端点）。

WebSocket 官方频道：`allMids`、`trades{coin}`、`candle{coin,interval}`、`l2Book{coin}`、`userFills{user}`、`userEvents`、`userFundings`、`activeAssetCtx{coin}`、`bbo{coin}`、`clearinghouseState`。官方上限：每 IP **≤10 连接**、**≤30 次新连接/min**、**≤1000 订阅**、**≤10 个不同 user**、**≤2000 消息/min**、**≤100 个在飞 post**（`官方`，**2026-09-19 逐条复核，无变化**）。WS 不消耗 `/info` 权重。

限速（`官方`+`实测`）：`/info` 每 IP **1200 权重/min**；权重 2 组 = `l2Book, allMids, clearinghouseState, orderStatus, spotClearinghouseState, exchangeStatus`（**这是最便宜的一组，不是最贵**）；`userRole` 60；其余文档化请求一律 20；历史类每 20 条 +1（`candleSnapshot` 每 60 条 +1）。响应头**没有任何限速字段**。`实测`：① 单出口容量约 2350–2990 权重/min，比文档宽，**记账仍按 1200**；② 两种 429：响应体 JSON `null` = 权重限速 → 退避权重；nginx HTML 页 = 连接速率限速 → 降并发，`max_inflight` 硬顶 **10**，超过吞吐反降；③ 串行只能用掉 25% 预算，8 线程刚好打满；④ AIMD：撞 429 砍到 75%，冻结 1h。地址级限速只针对下单，读接口没有。

## 4. 限速总表与预算

**两个账本 + 一个独立主机账本**：权重桶（Binance 合约 `REQUEST_WEIGHT`、HL `/info`）· 请求桶（`/futures/data/*` 与 `fundingRate` 类不吃权重，受独立 IP 频率限制）· **Binance 现货是另一台主机 `api.binance.com` 的另一个权重桶**（§9.3）。

**一份预算、两个消费者**（2026-09-19 改写）：生产落在 hub 机器上，hub 的旧采集器会与新采集器**共存一段时间后退役**。下表第 3 列是 hub 旧采集器的占用，第 5 列才是本项目在过渡期真正能用的余量。

| 账本 | 官方上限（**整个出口 IP 共享**） | hub 旧采集器占用 | 本项目需求（M1+M2 / +M4 / +M5） | 过渡期余量判定 |
|---|---|---|---|---|
| Binance 合约权重 | 2400/min（40% 红线 = 960） | **`未验证`**：仓库内无任何 hub 打 Binance 的实测记录；`01-PRODUCT.md §6` 的 hub `funding`/`kline` 表标注「多所」，**提示 hub 很可能也打 Binance**，上机前必须实测 | 241 / **341** / 341 | 40% 红线下，本项目 341 之外**只剩 619/min 给 hub**；hub 实测若超过 619，两者之和即越线 |
| Binance 现货权重（**独立主机 `api.binance.com`**） | 6000/min（红线 2400） | `未验证`（同上，hub 是否打现货无记录） | 0 / **4** / 4 | 余量充裕，本项目占用可忽略 |
| Binance `futures_data` 请求桶 | **1000 次/5min = 200/min**（`官方`；红线 80） | `未验证`（hub 以 HL 为主，是否打此族无记录） | **54 次/min** / 54 / 54 | 本项目单独已占红线的 **67.5%**；hub 若也打此族，**只剩 26 次/min**，是最先会撞的桶 |
| Binance `fundingRate` 请求桶 | 500 次/5min = 100/min（红线 40） | `未验证` | <1 / <1 / <1 | 余量充裕 |
| **HL `/info` 权重** | 1200/min（90% 口径 = 1080，**含 hub**） | **`未验证`，且这是本次修订最要紧的一格**：`03 §6`/`§9` 写「hub 的 HL 预算已被钱包工作吃到接近 100%」，但该句引用的是「`04 §4`」，**而本文从未有过这个数字** —— 它是一条无实测支撑的循环引用。上机前**必须实测 hub 的实际权重占用** | 40 / **60–80** / **140–160** | **余量未知**。若「接近 100%」属实，过渡期 HL 可用量接近 **0**，M5 的 200 地址轮询（+80/min）尤其做不成；采集器必须支持把 HL 预算**下调到 ≤ 实测余量**，且回补（opportunistic）全额让路给常驻（决定 A7） |
| HL WS | 10 连接 · 30 新连接/min · 1000 订阅 · **10 个不同 user** · 2000 消息/min | **`未验证`**：hub 的钱包采集很可能已占用 user 席位；**10 个 user 是硬顶，不可分割** | 1 / **21 订阅** / 席位竞争 | 订阅数无压力；**user 席位是零和的**，hub 占几个本项目就少几个，M5 上线前必须与 hub 对账席位分配 |
| Binance WS | ≤1024 流/连接；≤300 次连接/5min | `未验证` | 1 连接 / **2 连接 21 流** / 同 | 余量充裕；但 `/market` 与 `/public` 必须各算一条连接 |

**共用出口的两个连带后果**：① **429/418 的伤害是共享的** —— 本项目在 Binance 撞 429 后继续打会升级成 **418 封整个出口 IP**，把 hub 正在跑的采集一起打死（反之亦然），故 418 处置必须是「停掉该所全部车道」而非只降速；② **HL 的 90%（1080）是出口合计上限，不是本项目可用量**，limiter 必须以「实测余量」为上界。

**多空比轮询改 10 分钟（决定 A8）的记账**：3 类 × 180 币 = 540 次 / 10 min = **54 次/min**（原 15 min 时为 36 次/min）。同时用 `limit` 参数把 `period=5m` 的中间点一并取回 → 落库网格从 15 分钟变 **5 分钟，密度 ×3**，请求次数只增 1.5 倍。这同时修掉了 15 分钟轮询在 `01-PRODUCT.md §4.5`「多空比 ≤15 分钟」上的新鲜度超标。连带：`ls_ratio` 行/天由 51,840 增至 155,520（**约 +7 GB/年**，`估算`），`03 §5` 的 `ls_ratio` 粒度与容量算式需同步改（§12）。

**两档预算比例（40% / 90%）为何不对称**：Binance 官方限速与实测基本吻合，按官方值的 40% 留边际，且 `X-MBX-USED-WEIGHT-1M`（合约与现货各一份）可直接校准账本；Hyperliquid 实测单出口容量（约 2350–2990 权重/min）明显宽于官方的 1200，但记账仍按官方保守值 1200、只用去它的 90%，且 HL 响应头不带任何限速字段，本地账本只能靠请求计数自估并按 429 类型（§3）反推。

**结论（按共用出口改写）**：**只看本项目自己**，M4 全开后没有一个桶超过官方上限的 27%（最紧的是 `futures_data`，54/200 = 27%，占其 40% 红线的 67.5%）。**但"还剩多少"现在取决于 hub 的实测占用，而那个数字一个都没有** —— 因此本节所有余量结论在 §11 第 12–14 项完成前一律是 `未验证`，不得当作已知量写进 `config/venues.yaml`。磁盘仍是 M4 的另一个瓶颈（§9.5），由 §11 第 1 项决定。

## 5. 历史回填能力（历史分位 F8 的输入）

| 数据 | Binance | Hyperliquid |
|---|---|---|
| 资金费率 | 按 8 小时结算网格；`fundingRate` 分页可到全史（独立请求桶 500 次/5min），批量走 `data.binance.vision` | 按 1 小时结算网格；`fundingHistory` 分页冷启动，**每次响应只返回 500 个元素**，官方未写保留上限 |
| 持仓量 | `/futures/data/openInterestHist`，5 分钟…1 天粒度，**仅最近 30 天** | **不可回填**（无历史端点），只能自采集起自建历史 |
| K 线 / 价格 | `/fapi/v1/klines`，全历史，批量走 `data.binance.vision` | **不是全历史**：`candleSnapshot` **只保留最近 5000 根**，且每次响应 ≤500 个元素 |

**HL K 线深度的实际含义（`官方`，本次修订纠正）**：5000 根上限按 interval 折算 —— `1m` ≈ **3.5 天**、`1h` ≈ 208 天、`1d` ≈ 13.7 年。因此：

- **HL 侧的 1 分钟历史无法回补到 30 天**，`F8` 的冷启动方案必须按此重写：跨所标记价差的 1 分钟分位**不能**靠两所 K 线回补到同一网格（Binance 可以，HL 只有 3.5 天），头 30 天一律走「数据积累中」；若要上线即有分位，只能降到 **1h 网格**（HL 208 天可回补）并在 `metric_coverage.grid_s` 里如实记 3600。HL 的 `market_1m` 回补行仍按 `semantic='candle_close'` + `backfilled=true` 标注，窗口最多 3.5 天。Hyperliquid OI 无法回填，是历史分位在满 30 天前必须显示"数据积累中"而非外推的直接原因；**HL 1 分钟 K 线只有 3.5 天是第二个同类原因**。

## 6. 出口可达性与生产出口的既成事实

**生产机 = hub 机器（raphael 2026-09-19 确认，覆盖原「新买一台非美 VPS」的写法）**。因此：**生产出口 = hub 出口 = 开发机出口**，三者同一个公网 IP。原先「采集器出口 IP 不得与 hub 任何 worker 相同、命中即拒绝启动」的硬规则**已不成立**，preflight 的出口项必须从「冲突即拒启」改为「记录并按共用预算分账」（§7、§12）。

既有的可达性实测（2026-09-12，**在一个非美出口上测得，未确认该出口就是 hub 的出口**）：

| 端点族 | 美国出口（GitHub Actions） | 非美出口 | hub 出口 |
|---|---|---|---|
| `fapi.binance.com` | **451** | 200 | **`未验证`** |
| `www.binance.com/fapi/*`、`/futures/data/*`（同路径镜像）· `data.binance.vision` | 200 | 200 | `未验证` |
| `api.hyperliquid.xyz`、`stats-data.hyperliquid.xyz` | 200 | 200 | `未验证`（hub 长期在采 HL，实践上可达，但未在本文留过记录） |
| `api.binance.com`（现货，M4 起） | `未验证` | `未验证` | `未验证` |

`fapi.binance.com` 与同路径的 `www.binance.com` 镜像在同一时间、同一出口下行为不同（前者 451、后者 200），判定为**应用层按地理位置拒绝**，不是网络层不可达；美区出口只能走 `www.binance.com` 镜像路径。**「必须非美出口」这条结论保留，但它的性质变了**：生产出口就是 hub 的出口，是否非美是**既成事实，只能在上机前实测确认，不再是采购时可以选择的条件**；若 hub 出口落在美国，唯一出路是全部 Binance 请求改走 `www.binance.com` 镜像路径。

**上机前必须实测的三件事**（全部在 hub 上做，见 §11 第 12–14 项）：① **hub 出口的国家 / ASN**（决定 `fapi.binance.com` 会不会 451，进而决定是否必须走镜像路径）；② **两所可达性**（Binance 合约 + 现货 + HL，逐条记 HTTP 码）；③ **hub 旧采集器的实际权重占用**（HL 与 Binance 两侧分别测，这是 §4 全部余量结论的唯一输入）。三件事没做完之前，limiter 的 live 预算按 **0** 处理（与决定 A15 的开发期口径一致），不得凭「大概还有余量」开采。

## 7. 时钟与预检（preflight）

启动时跑一次、之后每小时一次，结果写 `source_health`。检查项：

1. 出口：IP / ASN / 国家。**不再是「与 hub 相同即拒启」**（生产就在 hub 上，必然相同）——改为记录国家与出口哈希、确认与 `EXPECTED_EGRESS_HASH` 一致，并**读出 hub 旧采集器当前占用**写进 `source_health`，limiter 按「官方额度 − hub 占用」设本项目上界；读不到 hub 占用即判红、live 预算归 0。
2. 可达性：最便宜探针（Binance `ping`、HL `meta`），记录 HTTP 码、延迟、是否 451 地域拒绝；M4 起另探 `api.binance.com`。
3. 覆盖：对 `instruments` 中每个币探一次每类端点是否返回数据，空的记为覆盖不足。限速头：读取并校准本地账本（Binance 合约与现货**各读各自**的 `X-MBX-USED-WEIGHT-1M`；HL 无头，只能按 429 类型反推）。
4. 时钟：与交易所 `serverTime` 比对，**偏差需 <1 秒**（WSL 下实测约 1.35 秒，超门槛，需 NTP/chrony 校准）。WS：首条消息时延；**Binance 需分别探 `/market` 与 `/public` 两组**；HL 另记录连接数 / 订阅数 / **user 席位**距上限的余量（席位与 hub 共享，§4）。
5. 存储：DB 可写、磁盘余量 >20%；M4 起另报 `trade_tick` 最早一条记录的时间与占用（滚动删除是否在执行，F19 门控靠它）。

任一项失败 → 该来源 `ok=false` 熔断，其余来源照常，页面对应分面置灰。

## 8. 爆仓数据的两条实测结论

① **Hyperliquid WS `trades{coin}` 不带爆仓字段**（`实测`，2026-09-12；方法：逐条解析 WS 消息体核对字段存在性）。消息携带双方地址（`users:[buyer,seller]`），但没有 `liquidation` 字段；逐笔强平只出现在钱包级端点 `userFills`/`userFillsByTime` 返回的 fill 对象上（`liquidation{liquidatedUser,markPx,method}`，实测 6000 条 fill 中 21 条带该字段）。**因此在钱包级工作（M5）开展之前，没有可用的全所级公开强平流**；逐钱包采样口径为 `lower_bound`。同批实测还发现三点：约 28% 的成交 `hash` 为全零，不能当作爆仓标记；`tid` 不唯一（hub 实测 207,635 条 `tid=0`）；fill 对象里存在 `"liquidation": null` 这个键，必须按**键是否存在**判断是否强平，不能按值判断。

② **`hlens-hub` 机器的 HL 强平数据集是被追踪钱包成交的派生结果，不是账本**（`实测`，2026-09-12；方法：以追踪钱包集合的成交流覆盖率与已知强平事件集合比对）。实时窗口完整度按 USD 约 95%、按币种 51–93%；采集延迟中位数约 6 小时；历史捕获率仅 1.6–8%。**结论：可作为大户强平事件参考源，不可作为全所级总量**；全所级真值唯一出路是节点数据 `node_fills_by_block`。

## 9. M4 数据源（逐笔 · 盘口 · 现货，白名单 ≤10 币）

### 9.1 Binance 逐笔成交（F20）

| 项 | 值 | 标签 |
|---|---|---|
| WS 频道 / 地址 / 字段 | `<symbol>@aggTrade`，**更新 100 ms**（聚合成交，非原始逐笔）；base URL **`wss://fstream.binance.com/market`**；字段 `a` 聚合成交 ID · `p` 价 · `q` 量 · `f`/`l` 首末成交 ID · `T` 成交时刻 · `m` 买方是否挂单方 | `官方` |
| CVD 口径 | `m=false` → 主动买，`m=true` → 主动卖；**不需要盘口** | `官方`（字段语义） |
| 断线回补 / 历史 | `/fapi/v1/aggTrades`（W=20，`limit≤1000`，**只最近 48 小时**）；`historicalTrades` 要 API key 且只 1 个月 → 不用。**历史实质不可回补**：48h 窗口只够补断线，冷启动无历史 | `官方` |
| 单条消息大小 / 消息量 | 单条约 180–230 B JSON（`估算`）；**单币每秒消息量官方任何一页都不给** | `估算` + `未验证` → §11 第 1 项 |

WS 不消耗 REST 权重，所以 Binance 侧逐笔的**取数成本为 0**，成本全在磁盘。

### 9.2 Binance 盘口（F21）

增量 WS：`<symbol>@depth`（默认 **250 ms**）/ `@500ms` / `@100ms`；部分档 `<symbol>@depth{5,10,20}@{100ms,250ms,500ms}`；base URL **`wss://fstream.binance.com/public`**（`官方`）。增量字段：`U` 首更新 ID · `u` 末更新 ID · `pu` 上一条的 `u` · `b`/`a` 买卖档 · `T` 撮合时刻。

**维护本地订单簿的官方正确流程**（`官方`，…/websocket-market-streams/How-to-manage-a-local-order-book-correctly，9 步）：① 先连 `…/public/stream?streams=<symbol>@depth`；② **缓冲**收到的事件；③ 取 `/fapi/v1/depth?symbol=…&limit=1000` 快照；④ 丢弃所有 `u < lastUpdateId` 的事件；⑤ 第一条被处理的事件必须满足 `U ≤ lastUpdateId` **且** `u ≥ lastUpdateId`；⑥ 之后每条事件的 `pu` 必须等于上一条的 `u`，**不等就回到第 ③ 步重建**；⑦ 事件里的数量是该价位的**绝对量**，不是增量；⑧ 数量为 0 → **删除该价位**；⑨ 删到本地不存在的价位属正常，不报错。

**F21 只要「每分钟一张 ±0.5% 快照」，因此采用方案 A：定时拉 REST 快照** —— `limit=500` → 10 币 ×10 = **100 权重/min**（`limit=1000` 则 200 权重/min），无状态、无第 ⑥ 步重建风险；`limit` 取多大才覆盖 ±0.5% 逐币不同 → `未验证`（§11 第 4 项）。方案 B（订阅增量自维护）稳态 0 权重但要实现上面 9 步，`pu` 断裂频率未知，且**出错方式是静默算错深度而不是报错**。上面 9 步写进本文只为记录「将来若要做全量增量，官方规则在这里」。

### 9.3 现货（两所，F22）

**Binance 现货是独立主机 + 独立权重桶**（`官方`）：base URL `https://api.binance.com`（另有 `api-gcp` / `api1`–`api4`），**与 `fapi.binance.com` 不同主机**；`REQUEST_WEIGHT` = **6000/min/IP**（合约是 2400）。官方原文是「Weight is accumulated **per IP address** and is shared by all connections from that address」，两边各自文档化了不同上限与各自的 `X-MBX-USED-WEIGHT-*` 头 → 本文**按独立两桶记账**，但官方没有一句明说跨产品是否共享 IP 计数器 → `未验证`（§11 第 7 项）。

| 端点 | 权重 | 用途 |
|---|---|---|
| `/api/v3/ticker/price` | 不传 symbol 或传 `symbols` → **4**（一次覆盖全市场）；单 symbol → 2 | F22 主用，1 min |
| `/api/v3/ticker/24hr` · `/api/v3/klines` · `/api/v3/depth` | 24hr：1–20 symbols→2、21–100→40、不传或 ≥101→80；klines **2**（定值，非合约的 1–10 阶梯）；depth 1–100→5、101–500→25、501–1000→50 | 均不采集（F22 只取价格） |

**Hyperliquid 现货**（`官方`）：`spotMeta` / `spotMetaAndAssetCtxs` 权重**各 20**，字段 `tokens[]{name,szDecimals,weiDecimals,index,tokenId,isCanonical,evmContract,fullName}` 与 `universe[]{name,tokens[2],index,isCanonical}`，后者另加 `ctxs[]{midPx,markPx,prevDayPx,dayNtlVlm,circulatingSupply}`；`spotClearinghouseState` 权重 **2**，字段 `balances[]{coin,token,hold,total,entryNtl}`。

**永续币 ↔ 现货币的配对**：HL 现货有**独立符号体系** —— canonical 对写作 `TOKEN/USDC`（文档示例 `PURR/USDC`），非 canonical 对写作 `@<index>`（示例 `@1` = HFUN/USDC）；`universe[].tokens` 是指向 `tokens[]` 的下标对。**官方文档里没有任何一句说明现货 token 与同名永续之间的对应关系**（`官方`，全文核对，示例只含 USDC/PURR/HFUN）。因此配对表只能在生产机（hub）上 dump 一次 `spotMeta` 后**人工维护**（§11 第 6 项）。按 F22 的降级条款：白名单币在 HL 现货没有可信配对的，**F22 只做 Binance 现货一所**，并在 `/sources` 写明「现货来源只有一所、不做跨所现货比较」；**不允许用名字相近的现货对凑数**。

### 9.4 Hyperliquid 逐笔与盘口

| 项 | 值 | 标签 |
|---|---|---|
| 逐笔 WS / 盘口 WS | `{"type":"trades","coin":"<coin>"}` → `WsTrade[]`，字段 `coin,side,px,sz,time,hash,tid,users`；`{"type":"l2Book","coin":"<coin>"}`（可选 `nSigFigs`、`mantissa`、`fast`）→ `{coin,levels:[bids,asks],time}` | `官方` |
| 盘口 REST / 档位上限 | `type:"l2Book"`，**权重 2**（权重组 2 是最便宜的一组，HL 盘口不是预算瓶颈）；**每侧至多 20 档** —— 这才是 HL 盘口的真正限制 | `官方` |
| 10 币同时订阅 | `trades`×10 + `l2Book`×10 = **20 订阅 / 1000（2.0%）**，1 条连接内放得下 | `估算` |
| 逐笔字段陷阱 | `hash` 可能全零、`tid` 不唯一（见 §8），去重必须自建键 | `实测` |

`nSigFigs=null`（全精度）下 20 档在流动性好的币上可能覆盖不到 ±0.5%，需用 `nSigFigs` 聚合把 20 档摊到更宽价格区间；能否覆盖逐币不同 → `未验证`（§11 第 5 项）。

### 9.5 M4 的瓶颈不在配额，在磁盘

`估算`（算式与三档明细见 本文 §9 与 `03` 的容量节，容量表由 `03` 维护）：10 币两所、高频窄表按 **150 B/行**（§11 第 3 项待校准），逐笔行/天 = `(R_binance + R_hl) × 86400 × 10`，低/中/高三档合计 **1.63 / 3.25 / 9.73 GB/天**；盘口与现货各 7.2 MB/天。**逐笔占 99.6–99.9%**，F21/F22 的成本在配额而不在存储。决定 A1 定逐笔**滚动留存 ≤7 天**、30 天分位算在可永久留存的 `cvd_1m` 上，稳态中档 **23 GB**、高档 68 GB；若改永久留存中档约 73 天吃光剩余空间。**结论完全取决于 `R`**（§11 第 1 项，跨 6 倍区间）。**注意生产是 hub，磁盘余量也是与旧采集器共享的**，上机前须一并核实 hub 当前可用空间。

## 10. M5 钱包链路端点（Hyperliquid）

| `type` / 来源 | 权重 | 用到的字段 | 分页与历史深度 | 标签 |
|---|---|---|---|---|
| `clearinghouseState` | **2** | `assetPositions[]{coin,szi,entryPx,leverage,liquidationPx,marginUsed,positionValue,unrealizedPnl,cumFunding}` · `marginSummary{accountValue,totalNtlPos,totalMarginUsed,totalRawUsd}` · `withdrawable` · `crossMaintenanceMarginUsed` · `time` | 快照，**无历史** | `官方` |
| `userFills` | 20，**且每 20 条 +1** | fill 对象（含 `liquidation{liquidatedUser,markPx,method}`，**按键是否存在判断**，见 §8） | **最多返回最近 2000 条** | `官方` |
| `userFillsByTime` | 同上 | 同上 | **每次 ≤2000 条，且只有最近 10000 条可取**；翻页 = 拿上次返回的最后一个 `time` 当下次 `startTime` | `官方` |
| `userFunding` / `userNonFundingLedgerUpdates` | 20 | 资金费结算 / 充提与转账流水 | `startTime` 必填、`endTime` 可选；适用通用规则「带时间区间的响应只返回 500 个元素或 500 个不同区块」 | `官方` |
| WS `userFills{user}` | 不吃 `/info` 权重 | 快照 + 流式 fills，可选 `aggregateByTime` | 受 **≤10 个不同 user** 硬顶约束 | `官方` |
| 排行榜 | – | – | **官方 `/info` 无排行榜 request type**（本次在官方文档中未找到公开端点） | `未验证` → §11 第 11 项 |

**「≤10 个不同 user」对「要盯多少个大户」的实际含义**：WS 实时只能同时盯 **10 个地址**，且这 10 个名额与钱包页（F25）的按需查询**共享**。因此 F24 的候选池（按账户价值 TOP N，N 取到 n ≥ 100，决定 C3）**不可能全部走 WS**；实时层只能是「**10 个固定席位 + 其余靠 REST 轮询**」。REST 轮询成本：`clearinghouseState` W=2，轮询 200 个地址 / 5 min = **80 权重/min**（`估算`），占 HL 预算 1080 的 7.4% —— 可行。这直接印证 F23/F24 的「展示窗口必须晚于采集延迟 P95」不是保守措辞，而是配额的必然结果。

**哪些靠 hub 导入、哪些必须现采**：

| 数据 | 结论 | 理由 |
|---|---|---|
| `fill_history`（2023-02→2026-08）· `fill`（5.29 亿行） | **导入，无替代** | `userFillsByTime` **只有最近 10000 条可取**，历史根本取不回来 |
| `position` · `leaderboard` · `account_snapshot`（2026-08 起）· `liquidation`（57 万行，恒 `lower_bound`） | **导入，无替代** | HL 无公开历史快照端点，也无公开排行榜端点；`liquidation` 另见 §8（派生样本，历史段捕获率 1.6–8%，不可跨期画趋势） |
| 当前持仓 · 实时 fills · 资金费流水 | **必须现采** | 导入数据截止 hub 采集时点；F24 的 ≤5 分钟新鲜度只能由生产机（hub）自采满足 |
| 候选池刷新（TOP N 重排） | **现采或从 hub 增量同步，来源待定** | 官方无公开排行榜端点（§11 第 11 项），hub 的 `leaderboard` 采集来源须先查清，否则 M5 候选池无可复现来源 |

## 11. 未验证清单

**实测一律在生产机（hub）上做，不在开发机上做** —— 开发机与 hub 同一个公网出口（§6）。**警告：在 hub 上做任何实测都会直接消耗旧采集器正在用的预算**，每一次探测都从同一份额度里扣，必须**先与旧采集器协调**（停掉对应车道、或在其低谷窗口做、或把探测预算从旧采集器额度里显式划出），**不能随手打**；未协调即开测与在开发机上乱打是同一个错误。

| # | 未验证项 | 阶段 | 方法 |
|---|---|---|---|
| 1 | **单币 `aggTrade` 消息量/秒 `R`** —— 本文最大不确定项，跨 6 倍区间，**M4 的磁盘结论完全由它决定**，官方任何一页都不给 | M4 开工前，preflight 之后 | 在 hub 上订阅 `wss://fstream.binance.com/market` 的 10 个 `@aggTrade`，**连续计数 24 小时**（须含一个高波动日），记 p50/p95 msg/s 与实际 JSON 字节数；零 REST 权重 |
| 2 | HL `trades{coin}` 消息量/秒 | 同上，**同一窗口** | 同一进程订阅 10 个 `trades`，与 #1 同窗口计数，得出两所比值 |
| 3 | `trade_tick` 单行实际落盘字节（含索引），150 B 是估算 | M4 第 1 天 | 灌入 #1#2 的真实一天数据后 `pg_total_relation_size(...) / count(*)` |
| 4 | Binance `depth` 的 `limit` 取多大才覆盖 ±0.5%（逐币不同） | M4 开工前 | 对 10 个白名单币各拉一次 `limit=500` 与 `limit=1000`，看最外档距中间价的百分比；一次性成本 10×(10+20) = 300 权重 |
| 5 | HL `l2Book` 每侧 20 档在各 `nSigFigs` 下能否覆盖 ±0.5% | 同上 | 对 10 个币分别以 `nSigFigs=null,5,4,3` 各取一次（W=2/次，共 80 权重），比较最外档价差 |
| 6 | **HL 现货 ↔ 永续的配对表**（F22 的前置） | M4 开工前 | 在 hub 上 dump 一次 `spotMeta`（W=20），把 `tokens[].name` 与 `meta.universe[].name` 人工比对成映射表，落进 `config/venues.yaml`；配不上的币按 F22 写「该币无对应现货交易对」 |
| 7 | Binance 现货与合约权重桶**是否真正独立**；兼测 `api.binance.com` 在非美出口是否 200（§6 的空格） | M4 开工前 | 同一 IP 同时打 `api.binance.com` 与 `fapi.binance.com`，各读自己的 `X-MBX-USED-WEIGHT-1M`，看两个计数器是否互相增长 |
| 8 | 每个白名单币在 Binance 现货是否都有 USDT 交易对 | 同 #6 | 拉一次 `/api/v3/exchangeInfo`，与白名单求交集 |
| 9 | base URL 拆分后，WS 连接规则（1024 流/连接、300 次连接/5min、24h 断开、ping/pong、入站 5 条/s）是否照旧 | M4 开工前（**M1 即受影响**） | 查 `/public` 与 `/market` 各自的 Connect 页（变更公告未说明这些数字是否照搬）；查不到就按 §2 现有数字保守记账并保留 `未验证` |
| 10 | `/futures/data/*` 的 1000 次/5min 与 `fundingRate` 的 500 次/5min **是否同一个桶** | M1 上机后 | 分别连打两类端点，观察 429 触发点；官方两页各自独立写限额，未说明关系 |
| **12** | **hub 旧采集器的实际权重占用（两所分别测）** —— §4 全部余量结论的唯一输入，现在一个数字都没有 | **上机前（最高优先级，早于一切采集）** | 在 hub 上**读旧采集器自己的限速账本与日志**（不是重新打交易所）：HL 侧统计其 `/info` 请求数 × 各自权重，得每分钟权重占用的 p50/p95；Binance 侧读其响应里的 `X-MBX-USED-WEIGHT-1M`（若旧采集器根本不打 Binance，记 0 并写明判定依据）。**三档窗口各测一次：日常、hub 回补作业运行时、行情高波动时**，取 p95 作为记账值 |
| **13** | **hub 出口的国家 / ASN，以及两所（Binance 合约+现货、HL）的可达性** | 上机前 | preflight 的最便宜探针各打一次（Binance `ping`、HL `meta`、`api.binance.com` `/api/v3/ping`），记国家、HTTP 码、是否 451；**这几次调用本身要计入第 12 项的协调窗口** |
| **14** | **旧采集器退役后预算怎么回收**：退役前后各自占用、回收是否真的落到本项目 | 退役前一周 + 退役后 24 小时**各测一次** | 用第 12 项的同一套测法各跑一遍，两次结果之差即回收量；回收后才允许把 `config/venues.yaml` 的本项目上界调高，**调高必须是一次显式改动 + 一次 preflight 复测，不得由代码自动扩张** |
| 11 | HL 是否存在公开排行榜端点（F24 候选池来源） | M5 开工前 | 本次在官方 `/info` 文档中未找到；须确认 hub 的 `leaderboard` 表走哪个来源采集，否则 M5 候选池无可复现来源 |

## 12. 本文需保持一致的下游

- 采集器的限速 / 预算配置直接读 §4、§2、§3、§9 的常数，不得另行定义；变更先改本文。**表名以 `03-ARCHITECTURE.md §5` 为准**。历史分位 F8 依赖 §5：HL OI 不可回填、**HL 1 分钟 K 线只有约 3.5 天**，两者共同决定该指标在满 30 天前必须显示"数据积累中"。本次修订发现三处 03 需补的缺口，**由架构 agent 处理，本文不改 03**：① `03 §6` 仍写 `/futures/data/*` 限额「唯一靠猜的常数，保守 500 次/5min」——官方已明写 **1000 次/5min**，该句须删，占用率按 10 分钟轮询重算为 **27%**；② `03 §5` 的 `ls_ratio` 仍是 15 分钟网格 51,840 行/天，改 10 分钟轮询 + `limit` 取中间点后为 5 分钟网格 155,520 行/天（约 +7 GB/年）；③ `03 §5` 缺 `cvd_1m`（M4 派生分钟表）、缺 K 线 `takerBuyBaseVolume` 与现货价的落点列。
- 跨所分歧 F7 依赖 §2/§3 标记价与费率端点的新鲜度（60 秒）与 §4 的余量；F1/F3 依赖 §2/§3 的 `exchangeInfo`/`meta` 与 §1 的全市场覆盖事实；F12 依赖 §8（爆仓数字恒带 `下界`，不得以 hub 数据冒充全所总量）；M4 的 F19 门控（14 天试运行）判**稳态占用**，输入是 §9.5 与 §11 第 1–3 项；M5 的 F23/F24 依赖 §10 的「≤10 个 user 席位」与「`userFillsByTime` 只有最近 10000 条」两条硬限制。
- **生产机变更（生产 = hub）使以下几处与本文冲突，由各自 owner 改，本文不代改**：`01-PRODUCT.md §7` 硬约束表「VPS 的公网出口 IP 必须与 hub 不同」**须删除或反转**；`03-ARCHITECTURE.md` 的机器规格「出口 IP 不得与 hub 任一 worker 相同」、出口冲突判定「相同即判红」、以及 §6「HL ≈ 40 权重/分 = 预算 1080 的 3.7%」（共用出口下不成立，分母应是**扣除 hub 占用后的余量**）三处须按本文 §4/§6/§7 重写；`docs/A4` 的「② 出口 IP 冲突（硬规则）」同理。**`EXPECTED_EGRESS_HASH` 比对本身保留**——它从「判冲突」变成「确认确实在 hub 上、没跑错机器」。

## 13. 本次修订改了什么（2026-09-19）

| # | 原结论（已证伪 / 已过时） | 新结论 | 来源 |
|---|---|---|---|
| 1 | §2「WebSocket `wss://fstream.binance.com/stream?streams=`」 | 该 legacy 地址**官方已于 2026-04-23 永久停用**，拆为 `/public`（`@depth`、`@depth{5,10,20}`、`@bookTicker`）· `/market`（`@aggTrade`、`@markPrice`、`@kline_`、`@ticker`、`@forceOrder`）· `/private`。**影响 M1/M2**：`!markPrice@arr` 与 `!forceOrder@arr` 在 `/market` 组；M4 起 depth 与 aggTrade 分属两组 → **至少两条 WS 连接** | `官方` |
| 2 | `/futures/data/*`「没有实测数字、只能保守猜 500 次/5min」（原句在 `03 §6`） | 官方文档已明写 **1000 requests/5min**，标签由 `未验证` 升为 `官方`；本文 §4 新增独立 `futures_data` 请求桶。`03 §6` 的「唯一靠猜的常数」一句须删（由架构 agent 改） | `官方` |
| 3 | §3/§5「`candleSnapshot` 5000 条/请求上限（未验证）」+ §5「HL K 线全历史」 | 是**两条不同的限制**：带时间区间的响应**每次只返回 500 个元素**；`candleSnapshot` **只保留最近 5000 根 K 线**（历史深度）。故 **HL 1 分钟 K 线只有约 3.5 天，不是全历史**，F8 冷启动回补方案须按 §5 重写 | `官方` |
| 4 | §1 表名 `ticks_1m` / `klines` / `funding` / `funding_pred` / `open_interest` | 与 `03 §5` 不一致，会导致按本文写代码的 agent 建错表。全部统一到 03：`market_1m`（价格/费率/OI 同表）、K 线为回补输入无独立表；`takerBuyBaseVolume` 与现货价**在 03 中暂无落点列**，已列入 §12 待补 | 本文与 03 交叉核对 |
| 5 | §1 末「不采集：盘口深度、逐笔成交全量、现货…」 | M4 之后不成立。改为分里程碑表述：**M1–M3 不采；M4 门控通过后按 `hf_whitelist` 白名单（≤10 币）采**，逐笔滚动 ≤7 天、盘口每分钟一张快照、现货只取价格 | 决定 A1 + F19–F22 |
| 6 | §4 多空比按 15 分钟轮询 = 36 次/min；§4 只有两行账本，无现货桶与请求桶明细 | 决定 A8：改 **10 分钟**并用 `limit` 取回 `period=5m` 中间点 → **54 次/min**，落库密度 ×3（5 分钟网格），修掉 15 分钟新鲜度超标，`ls_ratio` 约 +7 GB/年。账本重排为 7 行，含 **Binance 现货独立桶（6000/min）**、`futures_data` 与 `fundingRate` 两个请求桶；本项目自身最紧的桶是 `futures_data` 的 27% | 决定 A8 + §4 |
| 8 | §3 HL 权重组 2 被外部文档（`A0 F21`）读作「权重高」 | 权重组 2 是**最便宜**的一组（W=2）；HL 盘口的真正限制是**每侧至多 20 档**，不是权重 | `官方` |
| 9 | §2 缺 `/fapi/v1/depth`、`aggTrades`、`trades`、`historicalTrades`；无 M4/M5 章节 | 新增四行 REST 端点 + §9（M4 逐笔/盘口/现货）+ §10（M5 钱包链路），未验证项集中到 §11（11 项） | §2/§9/§10/§11 |
| **11** | 硬规则①、§6、§7「**采集器出口 IP 不得与 hub 相同，命中即拒绝启动**」 | **彻底作废**（raphael 2026-09-19）：**生产就是 hub 那台机器**，出口与 hub 旧采集器、与开发机三者必然相同。改为「一份预算、两个消费者」：preflight 不再判冲突，而是断言**已读到 hub 旧采集器当前占用并完成扣减**，读不到即拒启（不知道还剩多少就开采等于盲打） | raphael 确认 |
| **12** | §4 按「本项目独占 2400 / 1200 权重」记账 | 重排为「官方限额 / hub 旧采集器占用 / 本项目可用余量」三列。**hub 占用整列 `未验证`**：HL 侧 `03 §6`/`§9` 那句「hub 已把 HL 预算吃到接近 100%」引用的是「`04 §4`」，**而本文从未有过这个数字——是无实测支撑的循环引用**，不能当已知量用。HL 侧写明：若「接近 100%」属实，过渡期可用量接近 0，M5 的 +80/min 尤其做不成；Binance 侧写明 40% 红线下本项目 341 之外只剩 619/min 给 hub | 本文与 03 交叉核对 |
| **13** | §6「生产环境必须用非美出口」被当作采购条件 | 性质改变：生产出口 = hub 出口 = 开发机出口，**是否非美是既成事实，只能上机前实测确认**；落美则全部 Binance 走 `www.binance.com` 镜像。§11 新增第 12–14 项（hub 占用、hub 出口与可达性、退役后预算回收），并加警告：**在 hub 上实测会直接吃掉旧采集器的预算，必须先协调** | raphael 确认 |
| 10 | §3 HL WS 官方上限（10 连接 / 30 新连接每分 / 1000 订阅 / 10 user / 2000 消息每分 / 100 在飞 post）与 §8 两条爆仓实测结论 | **2026-09-19 逐条复核，无变化**（记录复核事实，非修正） | `官方` / `实测` |
