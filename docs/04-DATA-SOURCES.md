# 数据源参考（Binance + Hyperliquid）

> 范围：第一切片只采 Binance USDⓈ-M 永续与 Hyperliquid 永续。字段名以采集器 contracts 为准。限速数字标注来源：`官方` = 官方文档原文（2026-09-12 核对）、`实测` = 探测记录、`未验证` = 文档未明写，按保守值记账。
>
> 三条硬规则：① 限速按**公网出口 IP** 记账，同一 IP 下所有进程共享一份预算，且**采集器出口 IP 不得与 `hlens-hub` 机器任何 worker 相同**；② 预算按官方上限的 **40%** 使用，Hyperliquid 因实测容量显著宽于文档值按 **90%** 使用，但记账口径仍取官方值 1200；③ 每个来源启动时跑一次自检（§7），结果写 `source_health`。

## 1. 数据类别与依赖的功能

| 类别 | 用途 / 依赖功能 | 表 | 首选端点 | 覆盖 |
|---|---|---|---|---|
| 标记价、指数价、24h 涨跌额 | 首页价格、`ticks_1m`、跨所分歧 F7 | `ticks_1m` | Binance WS `!markPrice@arr@1s`；HL WS `allMids` | 全市场一次 |
| K 线 1m/1h/1d | 市场环境、历史分位 F8、记分板 | `klines` | Binance `/fapi/v1/klines`；HL `candleSnapshot`（备选参考） | 逐币 |
| 主动买入量（taker buy volume） | taker 买卖比替代 | `klines.taker_buy_v` | Binance K 线字段 `takerBuyBaseVolume` | 逐币 |
| 资金费率（当前 + 预测 + 历史） | 分歧榜 F7、历史分位 F8、每日摘要 F10 | `funding`、`funding_pred` | Binance `/fapi/v1/premiumIndex` + `/fapi/v1/fundingInfo`；HL `metaAndAssetCtxs.funding` + `predictedFundings` | 全市场一次 |
| 持仓量（当前 + 历史） | 分歧榜 F7、历史分位 F8 | `open_interest` | HL `metaAndAssetCtxs.openInterest`（全市场一次）；Binance `/fapi/v1/openInterest`（**仅逐币**）+ `/futures/data/openInterestHist`（历史） | HL 全市场一次；Binance 逐币 |
| 多空比 / 主动买卖比（仅 Binance） | 币卡单所数字（F3：HL 该所不发布） | `ls_ratio` | Binance `/futures/data/{globalLongShortAccountRatio,topLongShortAccountRatio,topLongShortPositionRatio,takerlongshortRatio}` | 逐币 |
| 逐笔爆仓 / 爆仓聚合 | 爆仓事件流 F12（恒带 `下界`） | `liquidations` | Binance WS `!forceOrder@arr`（下界）；HL 无公开全所级流，见 §8 | 下界采样 |
| 合约元数据（符号、乘数、MMR 档、结算周期） | 币种范围 F1、口径对齐 F3、关键位（M6） | `instruments` | Binance `exchangeInfo` + `fundingInfo`；HL `meta`（`szDecimals/maxLeverage`），MMR ≈ 1/(2·maxLeverage) | 全市场一次 |

全市场端点（Binance `premiumIndex`/`ticker/24hr`、HL `metaAndAssetCtxs`）一次请求覆盖全部币，新增币不增加权重；逐币端点（Binance `openInterest`、多空比、K 线）才随币数线性增长，预算见 §4。不采集：盘口深度、逐笔成交全量、现货、期权逐合约、链上数据。

## 2. Binance USDⓈ-M 接口明细

`https://fapi.binance.com`；美区出口用镜像 `https://www.binance.com` 同路径（实测 200，见 §6）。文档：developers.binance.com/docs/derivatives/usds-margined-futures（2026-09-12 核对，含 `/futures/data/*`）。`W` = 权重。

| 端点 | 关键参数 | 字段 | W | 限 / 史 | 频率 |
|---|---|---|---|---|---|
| `/fapi/v1/premiumIndex` | 不传 symbol = 全市场 | `markPrice, indexPrice, lastFundingRate, nextFundingTime` | 1 单币 / 10 全市场 | 快照 | 60s 兜底，主用 WS |
| `/fapi/v1/openInterest` | `symbol` 必填 | `openInterest` | 1 | 快照 | 逐币 1 min（300 币 = 300 W/min） |
| `/futures/data/openInterestHist` | `symbol, period(5m…1d), limit≤500` | `sumOpenInterestValue` | 0（无 `X-MBX-USED-WEIGHT` 头，仍受 IP 频率限制，账本按 1 记） | 500 / 30 天 | 冷启动一次 |
| `/futures/data/globalLongShortAccountRatio` | 同上 | `longAccount` | 0 | 500 / 30 天 | 5 min |
| `/futures/data/topLongShortAccountRatio` | 同上 | `longAccount` | 0 | 500 / 30 天 | 5 min |
| `/futures/data/topLongShortPositionRatio` | 同上 | `longAccount` | 0 | 500 / 30 天 | 5 min |
| `/futures/data/takerlongshortRatio` | 同上 | `buyVol, sellVol` | 0 | 500 / 30 天 | 5 min |
| `/fapi/v1/fundingRate` | `symbol` 可选，`limit≤1000` | `fundingRate, fundingTime` | 独立限速 500 次/5min/IP | 1000 / 全史 | 冷启动 + 每 8h 对账 |
| `/fapi/v1/fundingInfo` | – | `fundingIntervalHours, adjustedFundingRateCap` | 同上独立限速 | 只列非默认周期币 | 1 h |
| `/fapi/v1/klines` | `symbol, interval, limit≤1500` | OHLCV + `takerBuyBaseVolume` | <100→1, <500→2, ≤1000→5, >1000→10 | 1500 / 全史 | 收盘补齐 |
| `/fapi/v1/markPriceKlines` | 同上 | 标记价 OHLC | 同上 | 1500 | 断线补齐 |
| `/fapi/v1/ticker/24hr` | 不传 symbol = 全市场 | `priceChangePercent, quoteVolume` | 1 单币 / 40 全市场 | 快照 | 1 min 全市场 |
| `/fapi/v1/exchangeInfo` | – | `symbols[].filters, contractType, status` | 1 | – | 1 h；`contractType` 只取 `PERPETUAL` |
| `/fapi/v1/leverageBracket` | 需签名（USER_DATA） | `brackets[].maintMarginRatio` | 1 | – | 不采集，无替代近似来源 |

WebSocket `wss://fstream.binance.com/stream?streams=`：`!markPrice@arr@1s`（全市场标记价+费率，1s）、`!forceOrder@arr`（爆仓，**每符号每秒只推最大一笔，是下界**）、`<symbol>@kline_1m`、`!ticker@arr`。连接规则：每连接 ≤1024 流；每 IP 每 5min ≤300 次连接；24h 强制断开需主动轮换；服务端 3min ping，10min 内须 pong；入站 ≤5 条/s。

限速（官方）：`REQUEST_WEIGHT` 每 IP **2400/min**，响应头 `X-MBX-USED-WEIGHT-1M` 可读；超限 429，继续打则 **418 封 IP**（`Retry-After` 2min–3 天递增）。`fundingRate`/`fundingInfo` 另有独立桶 **500 次/5min**。批量历史 `data.binance.vision/data/futures/um/{daily,monthly}/`（klines、markPriceKlines、fundingRate、metrics 等）无鉴权无限速，实测可下；**2 年日线、90 天小时线、30 天以前的比率历史一律走这里**。

## 3. Hyperliquid 接口明细

POST `https://api.hyperliquid.xyz/info`；WS `wss://api.hyperliquid.xyz/ws`。文档：hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits（2026-09-12 核对）。

| `type` | 请求字段 | 字段 | 权重 | 限 | 频率 |
|---|---|---|---|---|---|
| `meta` | – | `universe[].name, szDecimals, maxLeverage, marginTables` | 20 | – | 1 h |
| `metaAndAssetCtxs` | – | `funding（每小时）, openInterest, markPx, oraclePx, premium, dayNtlVlm, impactPxs` | 20 | 全市场一次 | 1 min |
| `allMids` | – | 币→中间价 | 2 | – | WS 替代 |
| `predictedFundings` | – | 各所预测费率（含 Binance 的预测值） | 20 | – | 5 min |
| `fundingHistory` | `coin, startTime, endTime` | `fundingRate, premium, time` | 每 20 条 +1 | 官方未写上限 | 冷启动 |
| `candleSnapshot` | `req{coin, interval, startTime, endTime}` | `t,o,h,l,c,v,n` | 每 60 条 +1 | 5000（第三方文档，未验证） | 备选参考 |

MMR 近似：HL ≈ 1/(2·maxLeverage)（官方近似公式，非独立端点）。

WebSocket 官方频道：`allMids`、`trades{coin}`、`candle{coin,interval}`、`userFills{user}`、`userEvents`、`userFundings`、`activeAssetCtx{coin}`、`bbo{coin}`、`clearinghouseState`。官方上限：每 IP **≤10 连接**、**≤30 次新连接/min**、**≤1000 订阅**、**≤10 个不同 user**、**≤2000 消息/min**、**≤100 个在飞 post**。WS 不消耗 `/info` 权重。

限速（官方+实测）：`/info` 每 IP **1200 权重/min**；权重 2 组 = `l2Book, allMids, clearinghouseState, orderStatus, spotClearinghouseState, exchangeStatus`；`userRole` 60；其余 20；历史类每 20 条 +1（`candleSnapshot` 每 60 条 +1）。响应头**没有任何限速字段**。实测：① 单出口容量约 2350–2990 权重/min，比文档宽，**记账仍按 1200**；② 两种 429：响应体 JSON `null` = 权重限速 → 退避权重；nginx HTML 页 = 连接速率限速 → 降并发，`max_inflight` 硬顶 **10**，超过吞吐反降；③ 串行只能用掉 25% 预算，8 线程刚好打满；④ AIMD：撞 429 砍到 75%，冻结 1h。地址级限速只针对下单，读接口没有。

## 4. 限速总表与预算

| 来源 | 官方上限（每出口 IP） | 记账口径 | 预算 | 超限行为 | 响应头 |
|---|---|---|---|---|---|
| Binance | 2400 权重/min；`fundingRate` 类另 500 次/5min | 权重 | 960/min（40%） | 429→继续打 418 封 IP 2min–3 天 | `X-MBX-USED-WEIGHT-1M`（可读） |
| Hyperliquid `/info` | 1200 权重/min | 权重（历史类按条数） | 1080/min（90%）；`max_inflight` 10 | 429 两种（JSON null=权重；HTML=连接） | 无 |
| Hyperliquid WS | 10 连接/IP，30 新连接/min，1000 订阅，10 个不同 user，2000 消息/min | 订阅数 | 8 连接、≤800 订阅、≤8 user | 断连 | – |

两档预算比例（40% / 90%）不对称，原因：
- Binance 官方限速与实测基本吻合，按官方值的 40% 留安全边际，响应头 `X-MBX-USED-WEIGHT-1M` 可直接校准账本。
- Hyperliquid 实测单出口容量（约 2350–2990 权重/min）明显宽于官方文档的 1200，但记账仍按官方保守值 1200，只是用去这个保守值的 90%。
- Hyperliquid 响应头不带任何限速字段，本地账本只能靠请求计数自估、按 429 类型（§3）反推是否逼近上限。

## 5. 历史回填能力（历史分位 F8 的输入）

| 数据 | Binance | Hyperliquid |
|---|---|---|
| 资金费率 | 按 8 小时结算网格；`fundingRate` 分页可到全史（独立限速 500 次/5min），批量走 `data.binance.vision` | 按 1 小时结算网格；`fundingHistory` 分页冷启动，官方未写数据保留上限 |
| 持仓量 | `/futures/data/openInterestHist`，5 分钟…1 天粒度，**仅最近 30 天** | **不可回填**（无历史端点），只能自采集起自建历史 |
| K 线 / 价格 | `/fapi/v1/klines`，全历史，批量走 `data.binance.vision` | `candleSnapshot`，全历史，5000 条/请求上限（未验证） |

Hyperliquid OI 无法回填，是历史分位功能在满 30 天前必须显示"数据积累中"而非外推的直接原因。

## 6. 出口可达性（实测）

| 端点族 | 美国出口（GitHub Actions） | 非美出口 |
|---|---|---|
| `fapi.binance.com` | **451** | 200 |
| `www.binance.com/fapi/*`、`/futures/data/*`（同路径镜像） | 200 | 200 |
| `data.binance.vision` | 200 | 200 |
| `api.hyperliquid.xyz`、`stats-data.hyperliquid.xyz` | 200 | 200 |

`fapi.binance.com` 与同路径的 `www.binance.com` 镜像在同一时间、同一出口下行为不同（前者 451、后者 200），判定为应用层按地理位置拒绝，不是网络层不可达。结论：生产环境必须用**非美出口**；美区出口若临时使用只能走 `www.binance.com` 镜像路径。采集器出口 IP 不得与 `hlens-hub` 机器任何 worker 的出口 IP 相同（两套限速器互不知情，命中即拒绝启动，见 §7）。

## 7. 时钟与预检（preflight）

启动时跑一次、之后每小时一次，结果写 `source_health`。检查项：

1. 出口：IP / ASN / 国家，与 hub 的出口 IP 表比对，命中即拒绝启动。
2. 可达性：最便宜探针（Binance `ping`、HL `meta`），记录 HTTP 码、延迟、是否 451 地域拒绝。
3. 覆盖：对 `instruments` 中每个币探一次每类端点是否返回数据，空的记为覆盖不足。
4. 限速头：读取并校准本地账本（Binance `X-MBX-USED-WEIGHT-1M`；HL 无头，只能按 429 类型反推）。
5. 时钟：与交易所 `serverTime` 比对，**偏差需 <1 秒**；WSL 环境下实测偏差约 1.35 秒，超出门槛，需 NTP/chrony 校准后再采集。
6. WS：首条消息时延；HL 另记录当前连接数 / 订阅数距上限的余量。
7. 存储：DB 可写、磁盘余量 >20%。

任一项失败 → 该来源 `ok=false` 熔断，其余来源照常，页面对应分面置灰。

## 8. 爆仓数据的两条实测结论

① **Hyperliquid WS `trades{coin}` 不带爆仓字段**（实测，2026-09-12；方法：逐条解析 WS 消息体核对字段存在性）。消息携带双方地址（`users:[buyer,seller]`），但没有 `liquidation` 字段；逐笔强平只出现在钱包级端点 `userFills`/`userFillsByTime` 返回的 fill 对象上（`liquidation{liquidatedUser,markPx,method}`，实测 6000 条 fill 中 21 条带该字段）。**因此在钱包级工作开展之前，没有可用的全所级公开强平流**；逐钱包采样口径为 `lower_bound`。同批实测还发现：
- 约 28% 的成交 `hash` 为全零，不能当作爆仓标记；
- `tid` 不唯一（hub 实测 207,635 条 `tid=0`）；
- fill 对象里存在 `"liquidation": null` 这个键，必须按**键是否存在**判断是否强平，不能按值判断。

② **`hlens-hub` 机器的 HL 强平数据集是被追踪钱包成交的派生结果，不是账本**（实测，2026-09-12；方法：以追踪钱包集合的成交流覆盖率与已知强平事件集合比对）。实时窗口完整度按 USD 约 95%、按币种 51–93%；采集延迟中位数约 6 小时；历史捕获率仅 1.6–8%。**结论：可作为大户强平事件参考源，不可作为全所级总量**；全所级真值唯一出路是节点数据 `node_fills_by_block`。

## 9. 本文需保持一致的下游

- 采集器的限速 / 预算配置直接读 §4、§2、§3 的常数（权重、上限、`max_inflight`），不得另行定义；变更先改本文。
- 历史分位 F8 依赖 §5 的回填粒度：HL OI 不可回填的事实决定该指标在满 30 天前必须显示"数据积累中"。
- 跨所分歧 F7 依赖 §2/§3 中标记价、费率端点的新鲜度（60 秒）与 §4 的限速余量。
- 爆仓事件流 F12 依赖 §8 的两条结论：所有爆仓数字必须带 `下界`，且不得以 hub 数据冒充全所总量。
- 币种范围 F1、口径对齐 F3 依赖 §2/§3 的 `exchangeInfo`/`meta` 元数据与 §1 的全市场端点覆盖事实。
