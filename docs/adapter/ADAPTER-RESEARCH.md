# hlens 交易所适配器研究与设计

## 0. 执行摘要

hlens 需要的不是一个“可以调用多个交易所下单接口”的通用交易 SDK，而是一层面向永续合约研究数据的统一适配器。它可以借鉴 CCXT 的 `exchange` 对象、统一方法名、`has` 能力声明、市场缓存和 endpoint cost 思路，但不能直接把 CCXT 的统一模型当作 hlens 的数据契约。

建议将适配器设计成四层：

```text
Venue Adapter
  ├─ Transport：HTTP / WebSocket / reconnect / timeout
  ├─ Exchange Mapping：endpoint、参数、限速、符号和原始 payload
  ├─ Normalization：统一字段、单位、时间、语义和事件 ID
  └─ Collection Runtime：调度、watermark、回补、source health、落库
```

核心结论：

1. 统一的是“数据语义”和“能力接口”，不是所有交易所原始字段。
2. `symbol`、`venue_symbol`、`instrument_id` 必须同时保留；不能用一个 CCXT 风格字符串替代交易所原始身份。
3. REST 负责快照、发现和回补，WebSocket 负责低延迟流；任何 WebSocket 事件都必须能够通过快照或 REST 水位恢复。
4. 一个全局 `rateLimit` 不够。必须使用按 endpoint、IP、用户、WebSocket 连接分别计账的多桶限速器。
5. 适配器输出不能只有数值，还要输出 `as_of`、`ingest_ts`、`source_ref`、`quality`、`raw_hash` 和缺失原因。
6. 大户 fills、钱包仓位和 Hyperliquid leaderboard 不应被强行塞进普通 CEX 的通用 `fetch_trades`；它们属于能力扩展或专用命名空间。
7. 第一阶段只做公开市场数据，不做交易执行、提现、账户资金和 API key 托管。这样与 hlens 的“不碰用户资金”原则一致。

本文件是研究和设计文档，不代表适配器代码已经实现。当前仓库仍是静态站；实际实现应在未来的 `packages/hlens-core/adapters` 与 `apps/collector` 中进行。

## 1. 研究范围与现状

### 1.1 当前代码做了什么

当前 [`scripts/fetch.py`](../../scripts/fetch.py) 是一个约 30 分钟运行一次的同步抓取脚本，直接在每个交易所函数中完成：

- Binance、Bybit、OKX、Gate、Bitget 的 REST 查询；
- Hyperliquid `metaAndAssetCtxs` 查询；
- Hyperliquid leaderboard 候选钱包和 `clearinghouseState` 查询；
- 费率归一化到 8 小时口径；
- OI、成交量、涨跌幅、多空比、主动买入比例和拥挤度计算；
- `latest.json` 与 `history.json` 写入。

这套实现适合静态快照，但不适合目标生产系统，原因是：

| 现状 | 生产适配器需要的能力 |
|---|---|
| 每个函数直接拼 URL | endpoint registry、参数模型和来源引用 |
| 用 Python `float` 解析金额 | Decimal/string arithmetic，避免价格和名义价值漂移 |
| 单次请求失败后返回 `None` | 结构化错误、按字段降级、重试策略和 source health |
| 没有 HTTP/WS 分层 | REST snapshot + WS stream + gap recovery |
| 交易所字段直接汇总 | instrument registry、单位/语义/时间统一 |
| 只存聚合 JSON | 原始 payload、normalized event、watermark 可重放 |
| 没有 endpoint 级限速 | IP/连接/权重/请求数多维预算 |
| `next_funding_ms` 在部分交易所为空 | 允许未知，并区分“源没有提供”和“采集失败” |

### 1.2 目标系统对适配器的约束

来自项目文档的约束必须成为适配器设计的前提：

- 采集在东京，生产出口 IP 与 hub worker 隔离；
- WS 优先，断线按 `ingest_watermark` REST 补齐；
- 价格与费率新鲜度预算约 60 秒，OI 约 2 分钟，多空比约 15 分钟，大户约 5 分钟；
- `instruments` 是符号、合约乘数、tick、MMR tiers 和上下架状态的归一化表；
- 写入必须经过 staging 与 `ON CONFLICT DO NOTHING`；
- 所有聚合结果带 `as_of` 和 `sources`，缺失不用 0 冒充；
- 爆仓数据必须标记为实时下界、模型估算或完整度未知；
- Hyperliquid fills 以 fills 为权威源，仓位快照只做对账；
- 历史导入和实时事件必须跑同一套 whale-engine 逻辑。

## 2. CCXT 可借鉴什么，不能照搬什么

### 2.1 可借鉴的部分

CCXT 的价值不在于“把所有交易所变得完全一样”，而在于提供一套稳定的最小共同接口：

- `fetchMarkets` / `loadMarkets`：统一市场发现并缓存；
- `has`：明确某交易所是否支持某能力；
- `timeframes`：声明 K 线周期；
- `rateLimit` 与 endpoint cost：把请求成本纳入调度；
- 统一的 funding、OI、K 线、ticker 等方法；
- 保留原始响应 `info`，允许调用方访问交易所特有字段；
- 公共 API、私有 API 和错误分类的边界。

CCXT 文档明确说明，统一 API 只是各交易所共同能力的子集；交易所特有参数仍通过 `params` 传递，且单一 `rateLimit` 无法表达所有 endpoint、burst 和账户/IP 限制。[^1]

### 2.2 不应照搬的部分

| CCXT 的取舍 | hlens 的不同要求 |
|---|---|
| 以交易/账户/订单为大范围统一目标 | 先聚焦公开永续市场数据和研究事件 |
| 常见方法缺失时由 `has` 反映 | 缺失时还要给字段级质量、`as_of` 和原因 |
| 统一对象可包含交易所特有 `info` | 原始 payload 和规范化结果要分别落盘/落库，可重放 |
| 适配器通常面向请求调用 | hlens 需要长期运行的 stream、watermark 和恢复循环 |
| 一个市场对象可覆盖交易端需求 | hlens 还要表达 mark/index/premium、结算周期、乘数、MMR tiers |
| WS 需要额外产品或实现 | WS 是 hlens 的生产主路径之一，不是可选增强 |
| 统一 trade 不等于审计级 fill | Hyperliquid fills 有地址、tid、dir、start position、liquidation 等事件语义 |

因此建议采用“CCXT-like core + hlens-native extensions”，而不是引入完整 CCXT 再在其上打补丁。

## 3. 建议的总体架构

### 3.1 模块边界

```text
packages/hlens-core/adapters/
├── contract.py          # 对 collector 暴露的协议和数据类型
├── capabilities.py      # 能力、来源质量、产品类型
├── instruments.py       # 交易所符号与内部 symbol 映射
├── transport.py         # HTTP/WS 客户端、超时、重连、原始响应
├── ratelimit.py         # 多维桶、endpoint cost、AIMD 和 Retry-After
├── errors.py            # 结构化异常与可重试性
├── quality.py           # freshness、completeness、semantic quality
├── pagination.py        # 时间/ID/游标回补
├── adapters/
│   ├── binance_futures.py
│   ├── bybit_linear.py
│   ├── okx_swap.py
│   ├── hyperliquid.py
│   ├── gate_futures.py
│   └── bitget_futures.py
└── fixtures/             # 脱敏原始响应和规范化 golden 样本

apps/collector/
├── snapshot_scheduler.py # 周期性 REST 任务
├── stream_runtime.py     # WS 连接、订阅、重连和事件路由
├── recovery.py           # watermark 缺口检测和回补
└── health.py             # source_health 与采集指标
```

适配器自身只负责“如何访问和解释一个交易所”。调度、持久化、聚合和业务事件不应复制到六个交易所类中。

### 3.2 适配器协议草案

Python 伪接口如下。这里使用结构化请求和返回类型，避免让业务层依赖交易所原始 JSON。

```python
class VenueAdapter(Protocol):
    venue: VenueId
    product: ProductType

    def capabilities(self) -> CapabilitySet: ...
    async def discover_instruments(self) -> list[Instrument]: ...
    async def fetch_snapshot(self, request: SnapshotRequest) -> SnapshotBatch: ...
    async def fetch_klines(self, request: KlineRequest) -> list[Candle]: ...
    async def fetch_funding_history(self, request: FundingHistoryRequest) -> Page[FundingPoint]: ...
    async def fetch_oi_history(self, request: OiHistoryRequest) -> Page[OpenInterestPoint]: ...
    async def backfill(self, request: BackfillRequest) -> AsyncIterator[NormalizedEvent]: ...
    async def stream(self, request: StreamRequest) -> AsyncIterator[NormalizedEvent]: ...
    async def healthcheck(self) -> AdapterHealth: ...
```

建议的最小能力集合不是 CCXT 的所有方法，而是 hlens 当前路线图真正需要的能力：

```text
discover_instruments
fetch_tickers_batch
fetch_mark_prices
fetch_funding_rates
fetch_funding_history
fetch_open_interest
fetch_long_short_ratios
fetch_taker_flow
fetch_klines
stream_mark_price
stream_liquidations
stream_trades
backfill_liquidations
backfill_trades
```

Hyperliquid 额外暴露一个专用接口：

```text
discover_wallet_candidates
fetch_wallet_state
fetch_wallet_fills
stream_wallet_fills
stream_wallet_events
```

这些方法不应被伪装成普通 CEX 的账户接口，因为它们的主体是公开地址，数据模型和恢复语义不同。

### 3.3 能力声明

每个适配器必须声明方法能力和质量级别：

```yaml
venue: binance
product: usdt_perpetual
capabilities:
  batch_ticker: {supported: true, mode: rest}
  mark_price: {supported: true, mode: ws_and_rest}
  funding_current: {supported: true, mode: ws_and_rest}
  funding_history: {supported: true, mode: rest}
  open_interest: {supported: true, mode: rest}
  long_short_ratio: {supported: true, mode: rest}
  taker_flow: {supported: true, mode: rest}
  liquidation_stream: {supported: true, mode: ws, completeness: lower_bound}
  liquidation_history: {supported: false}
  trade_stream: {supported: true, mode: ws}
  wallet_fills: {supported: false}
```

`supported` 只回答“能否调用”，`mode` 回答“通过什么路径”，`completeness` 回答“能否将数据当作完整历史”。这三者不能合并成一个布尔值。

## 4. 规范化数据模型

### 4.1 身份模型：不要只保留统一 symbol

内部建议使用以下结构：

```python
Instrument(
    instrument_id="binance:usdt_perpetual:BTCUSDT",
    venue="binance",
    product="perpetual",
    symbol="BTC",                 # hlens 的资产级 symbol
    venue_symbol="BTCUSDT",       # 交易所原始符号
    base_asset="BTC",
    quote_asset="USDT",
    settle_asset="USDT",
    contract_multiplier=Decimal("1"),
    contract_size_unit="base",
    is_inverse=False,
    tick_size=Decimal("0.10"),
    qty_step=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    funding_interval_s=28800,
    mmr_tiers=[...],
    status="trading",
    listed_ts=...,
    delisted_ts=None,
)
```

原因：

- `BTCUSDT`、`BTC-USDT-SWAP`、`BTC_USDT`、`BTC` 不是同一个字符串，但可能代表同一资产级永续；
- `1000PEPE`、`kPEPE`、`PEPE` 可能有不同合约乘数；
- 反向合约和 U 本位合约的 OI 单位不同；
- 交易所可能同时存在现货、永续、交割合约和期权；
- `symbol` 只是业务聚合键，不能承担交易所合约身份。

项目文档已有 `instruments` 表设计，适配器应以该表为中心，而不是在每个业务函数里重新拼符号。

### 4.2 Decimal、单位和空值

硬规则：

- 原始数字先以字符串保留，计算时使用 `Decimal`；
- 时间统一为 Unix milliseconds；原始时间单位必须记录；
- 费率统一存小数，例如 `0.0001 = 0.01%`；
- 百分比字段用百分数还是比例必须由字段名区分，不能混用；
- OI 同时保留原始数量、原始单位和 USD 估算值；
- 不知道的值为 `null`，不能用 `0`；
- `as_of` 是数据事件时间，`ingest_ts` 是本机收到时间，两者都必须保留；
- 计算得到的字段写入 `derived_from` 和公式版本。

### 4.3 统一事件信封

所有快照和事件都应有同一个来源信封：

```python
SourceEnvelope(
    venue="bybit",
    endpoint="/v5/market/tickers",
    transport="rest",
    request_id="...",
    received_ts=...,
    event_ts=...,
    sequence="...",           # 没有则 null
    source_cursor="...",     # ID/time cursor
    raw_hash="sha256:...",
    quality="fresh|stale|partial|estimated|error",
    completeness="full|lower_bound|unknown",
)
```

规范化对象应包含 `source: SourceEnvelope`，而不是只在最终聚合表里记录一个 venue 名。

### 4.4 建议的规范化类型

| 类型 | 关键字段 | 说明 |
|---|---|---|
| `Instrument` | identity、乘数、tick、qty step、funding interval、MMR tiers | 交易规则和符号基础数据 |
| `MarketSnapshot` | last、mark、index、premium、24h change、volume | 低频快照，可批量获取 |
| `FundingPoint` | rate、interval、funding_ts、mark、index | 必须区分预测费率与已结算费率 |
| `OpenInterestPoint` | oi_native、oi_usd、unit、mark、ts | 保留原始单位和换算来源 |
| `LongShortPoint` | kind、long_share、raw_ratio、sample_scope | kind 不允许混成一个平均值 |
| `TakerFlowPoint` | buy_qty、sell_qty、buy_share、period | 说明是成交量、名义价值还是账户比 |
| `Candle` | open_ts、close_ts、OHLC、volume、taker_buy | 统一时间边界和未收盘标记 |
| `LiquidationEvent` | side、price、size、notional、event_id | 必须带 completeness/lower-bound 语义 |
| `PublicTrade` | trade_id、price、size、maker_side、ts | 只表示公开成交，不等于用户 fill |
| `WalletPosition` | address、coin、side、size、entry、liq、leverage | HL 特有，可用于对账 |
| `WalletFill` | address、tid、content_hash、dir、start_position、closed_pnl | HL 事件权威源 |

## 5. REST、WebSocket 与恢复设计

### 5.1 REST 的职责

REST 适合：

- 定期发现 instruments；
- 全市场 ticker、mark、funding、OI 快照；
- 低频多空比、taker flow；
- K 线和 funding history；
- WebSocket 断线后的时间段回补；
- 启动时建立初始状态。

适配器不要为了统一而把每个字段都按单币请求。优先使用全市场或批量 endpoint，尤其是 OI、ticker 和 mark price。

### 5.2 WebSocket 的职责

WebSocket 适合：

- mark price / index / funding 的低延迟更新；
- liquidation stream；
- public trades；
- Hyperliquid 的大户 fills 和用户事件；
- 需要在 5–10 秒内触发告警的事件。

每个 WS 连接都必须实现：

1. 明确订阅清单和订阅确认；
2. ping/pong 或协议心跳；
3. 服务端主动断开处理；
4. 指数退避和最大重连间隔；
5. 连接代号、订阅版本和最后事件时间；
6. 断线后先建立新连接，再按 watermark 回补；
7. 重复事件去重；
8. 无法证明连续性时标为 gap，不得静默伪造连续序列。

Binance 的衍生品 WS 连接有单连接流数量、消息速率和周期性断开约束；官方还建议在实时场景优先使用 WebSocket，而不是依赖延迟可能更高的 REST。[^7] Hyperliquid 官方文档也明确要求处理服务端断线，且可以用对应的 info 请求补回错过的数据。[^8]

### 5.3 Watermark 设计

不同源的游标分成三类：

```text
time watermark      event_ts > last_event_ts
id watermark        trade_id / sequence > last_id
composite watermark (event_ts, seen_ids_at_same_ms, raw_hash)
```

恢复流程：

```text
WS disconnect
  -> record gap(start_ts, connection_id)
  -> reconnect and obtain fresh snapshot
  -> REST backfill [last_watermark, snapshot_ts]
  -> normalize + dedupe
  -> compare snapshot with stream state
  -> close gap or mark unresolved
```

Hyperliquid fills 必须使用复合游标，因为同一毫秒可能有多个 tid，且项目已有 `tid=0` 数据，需要把内容哈希纳入去重键。历史和实时都使用同一套 `(address, tid, content_hash)` 语义。

### 5.4 一致性级别

适配器对外声明一致性，而不是只返回数组：

| 级别 | 含义 |
|---|---|
| `snapshot` | 某一时刻的完整或近似完整快照 |
| `append_only` | 事件可以按游标追加，缺口可回补 |
| `best_effort` | 交易所没有完整历史或事件会被限流 |
| `lower_bound` | 只代表观测到的下界，例如部分清算流 |
| `estimated` | 由模型推导，不是交易所原始事件 |

聚合层必须保留这些级别，不能把 `lower_bound` 变成普通 `liquidations` 数值。

## 6. 限速与错误模型

### 6.1 多维限速桶

不要实现一个全局 `sleep(rate_limit)`。建议每个 endpoint 声明：

```yaml
endpoint: binance.futures.mark_price
limits:
  - key: ip:weight
    capacity: 2400
    window_s: 60
  - key: ip:requests
    capacity: 1200
    window_s: 60
cost: 1
burst: 20
retry_after_header: true
```

实际 limiter 需要按以下维度分别计账：

- 出口 IP；
- API key / 用户；
- WebSocket 连接；
- endpoint weight；
- 并发请求数；
- 交易所特有的连接或订阅上限。

CCXT 的 endpoint cost 思路值得保留，但 hlens 还需要按交易所的限速维度扩展。Hyperliquid 官方公开了按 IP 聚合的 REST 权重、特定 info 请求权重，以及 WebSocket 消息上限；项目对 hub 的实测还发现连接限速和权重限速需要区分处理。[^1] [^9]

### 6.2 重试原则

| 错误 | 默认动作 |
|---|---|
| timeout / connection reset | 指数退避，限次数，可重试幂等请求 |
| HTTP 429 / 交易所限速 | 读取 Retry-After；降低对应 bucket，不重试非幂等操作 |
| 5xx | 短退避重试，超过阈值熔断 |
| invalid symbol | 不重试，标记 instrument stale 或 delisted candidate |
| auth / permission | 不重试，触发配置告警 |
| schema decode error | 保留 raw payload，立即降级并报警，不盲目重试 |
| semantic validation error | 丢弃该字段/事件，保留 source health 记录 |
| geo block / policy block | 切换允许的 host 或 venue policy，不循环撞同一地址 |

异常必须带：`venue`、endpoint、symbol、request_id、attempt、retryable、raw_status、body_hash。

### 6.3 熔断

建议使用 source × capability 粒度，而不是整个交易所一起熔断：

```text
Binance / mark_price: healthy
Binance / long_short_ratio: stale
Binance / liquidation: error
```

这样多空比接口变化不会让价格和费率整所消失。

## 7. 六个目标交易所的适配策略

### 7.1 能力矩阵

下表是面向 hlens v1 采集的工程矩阵；“语义风险”比“接口是否存在”更重要。

| Venue | 合约产品 | 适配重点 | 主要风险 |
|---|---|---|---|
| Binance USDⓈ-M | USDT/USDC perpetual | 全市场 mark、OI、funding、LS、taker、forceOrder、K 线 | 权重/请求双约束；WS 连接周期；清算流完整性不是历史档案 |
| Bybit linear | USDT/USDC linear perpetual | batch tickers、funding、OI、account ratio、liquidation WS | `category` 和产品维度必填；funding interval 按 symbol 变化 |
| OKX swap | USDT/USDC/crypto-margined swap | instruments、tickers、funding、OI、mark-price、liquidation-orders | REST 各服务有独立缓存；instId、instType、settleCcy 语义复杂 |
| Hyperliquid | perp DEX | `metaAndAssetCtxs`、allMids、trades、fills、wallet state | info 权重、WS 恢复、公开地址 fills、HL 特有事件语义 |
| Gate USDT futures | USDT futures | contracts、tickers、funding、contract_stats、risk tiers | 合约乘数/大小单位；liquidation 字段可能是估计值 |
| Bitget USDT futures | USDT-M futures | ticker、funding history、account L/S、taker flow、WS ticker | API 版本/产品类型；部分统计字段并非所有 symbol 都有 |

### 7.2 Binance

Binance 官方 USDⓈ-M 文档将 Exchange Information、Funding Rate、Open Interest、Long/Short Ratio、Taker Buy/Sell Volume、Mark Price、24h Ticker、Force Orders 等列为不同的市场数据能力。[^3]

适配器建议：

- `exchangeInfo` 作为 instrument registry 的来源；
- `premiumIndex` 批量拉 mark、index、funding、next funding；
- `openInterest` 作为当前 OI，`openInterestHist` 只用于有期限限制的历史补充；
- `globalLongShortAccountRatio`、`topLongShortPositionRatio`、`takerlongshortRatio` 分别写入不同 `kind`；
- `!markPrice@arr@1s` 与 `!forceOrder@arr` 进入 WS；
- 所有 `@` endpoint 的原始字段保留，不能只保留 `value`；
- 清算事件标 `completeness=lower_bound`，除非后续验证了覆盖率。

### 7.3 Bybit

Bybit V5 的 ticker 接口同时返回 mark price、funding rate、open interest value 等字段；funding history 接口明确指出不同 symbol 的 funding interval 可能不同，且只传 `startTime` 会报错。[^4]

适配器建议：

- 所有请求显式携带 `category=linear`；
- instrument discovery 读取 funding interval、settle coin、contract size 和 status；
- ticker 的 `openInterest` 与 `openInterestValue` 不混淆；
- funding history 使用时间边界和 limit 进行稳定分页；
- account ratio 的 `buyRatio`/`sellRatio` 作为账户统计，不命名为 taker flow；
- `allLiquidation` WS 单独建事件类型，并记录是否为 snapshot、增量或聚合消息。

### 7.4 OKX

OKX V5 的公开市场数据包括 tickers、candles、funding rate、funding history、mark price、open interest、trades 等；其文档说明市场数据由多个服务提供，独立缓存可能导致后一次请求的结果反而更早。[^5]

适配器建议：

- `instType=SWAP` 和 `instId` 作为结构化参数，禁止拼接不带产品类型的 URL；
- instrument channel 用于发现 tick size 和上下架变化；
- ticker、funding、OI、mark price 记录各自源时间，不能假设同一 snapshot 时间；
- `long-short-account-ratio` 先保存原始 ratio，再计算 share；
- liquidation-orders 单独归档，区分 partial liquidation、liquidation、ADL 和普通强平相关事件；
- 对 OKX 聚合结果采用 field-level `as_of`，不要用一个请求时间覆盖全部字段。

### 7.5 Hyperliquid

Hyperliquid 的 `info` API 以 POST body 的 `type` 区分能力，官方文档覆盖 `clearinghouseState`、`userFillsByTime`、portfolio 等查询；WS 支持 `allMids`、`trades`、`clearinghouseState`、`userFills` 和用户事件订阅。[^6] [^8]

适配器建议：

- 把 `info` request type 作为 endpoint registry 的一部分；
- `metaAndAssetCtxs` 负责市场元数据和 asset context，但 mark、funding、OI 的字段语义仍需逐项验证；
- `allMids`、`trades` 进入实时市场流；
- 不要在设计中假设可以为 200 个 hot 钱包逐一订阅 `userFills`。当前 `06-DATA-SOURCES.md` 记录的官方限制是每个 IP 最多 10 个不同 user，必须先验证 `trades{coin}` 的 `users` 字段是否足以发现地址，再决定 hot 钱包的 WS/REST 分层；在验证完成前，warm/hot 路径以 `clearinghouseState` + `userFillsByTime` 为保守方案；
- `clearinghouseState` 只做仓位/权益对账，不作为交易事件权威源；
- `dir`、`startPosition`、`tid`、`liquidation`、`closedPnl` 必须原样保留；
- `liquidation: null` 不能通过字符串存在性判断强平；
- `accountValue`、perp equity 和 leaderboard account value 必须分字段保存，不能混成一个钱包价值。

### 7.6 Gate

Gate Futures 文档提供 contracts、tickers、funding、contract stats、risk limits 等字段；文档特别强调 contract multiplier、funding interval、mark/index/last price、liq price 和 maintenance rate 等语义。[^10]

适配器建议：

- `quanto_multiplier`、`order_size_min`、`enable_decimal` 进入 instrument；
- `funding_interval` 从秒转换成 `funding_interval_s`，不要默认 8 小时；
- `open_interest_usd` 直接使用时仍保留原始 contract stats；
- `liq_price` 标为交易所估计值，不能当真实清算事件；
- risk limit tiers 进入 `mmr_tiers`，供模型计算使用，但模型输出必须标 `model_estimated`；
- `contract_stats` 的 L/S、taker 和清算统计按原始统计窗口写入，不与实时事件相加后冒充同一口径。

### 7.7 Bitget

Bitget 合约 API 将 market、funding、public websocket ticker、long-short 等能力分开；其 funding history 文档明确了 `productType=USDT-FUTURES`、分页大小和 funding settlement timestamp。[^11] [^12]

适配器建议：

- 所有请求显式携带 `productType`；
- `holdingAmount` 与 USD OI 分开保存，乘以 mark 的结果只能叫 `oi_usd_estimated`；
- `change24h` 的比例转百分数时只在规范化层转换一次；
- `nextFundingTime` 不存在时保留 null，不从 8 小时默认值反推；
- L/S 和 taker endpoint 对缺失 symbol 采取 field-level partial，而不是整所失败；
- WS ticker 的 funding/OI 字段和 REST ticker 要做一致性检查，差异记录为 observation，不静默覆盖。

## 8. hlens 特有的质量层

### 8.1 质量不是一个布尔值

建议每个字段附带：

```yaml
quality:
  state: fresh | stale | partial | estimated | error
  as_of: 1730000000000
  ingest_ts: 1730000001000
  age_ms: 1000
  completeness: full | lower_bound | unknown
  semantic: exchange_reported | normalized | derived | modeled
  sample_n: 12
  reason: null
```

例如：

- Binance 的 mark price：`exchange_reported + fresh`；
- 由 OI 数量 × mark 得到的 USD OI：`derived`；
- 按 MMR tiers 计算的清算价：`modeled`；
- Gate 统计接口中的过去 1 小时清算金额：可能是 `exchange_reported + partial`；
- 追踪 3 个 HL 钱包得到的 whale share：`exchange_reported + sample_n=3`，前端按项目规则置灰。

### 8.2 语义验证规则

适配器至少验证：

- `0 <= share <= 1`；
- 费率和价格是有限数；
- OI 非负；
- `funding_interval_s > 0`；
- event time 不应远超本机时间，时钟偏移单独记录；
- trade/fill size 非负，side/dir 属于已知枚举；
- 同一 instrument 的 tick/step 不在短时间内无解释地跳变；
- WS sequence 如存在则不能倒退；
- 服务器返回空列表和请求失败要区分；
- 某字段从接口消失时不能自动解释为 null 以外的 0。

### 8.3 Source health

建议 `source_health` 至少按 `(venue, capability, transport)` 记录：

```text
venue
capability
transport
last_ok_ts
last_event_ts
latency_ms_p50/p95
consecutive_fail
last_error_class
last_http_status
gap_count_24h
freshness_state
```

这与项目的状态页和 `sources{venue: ok|stale|error}` 约定相容，同时比整所一个状态更有用。

## 9. 适配器与现有代码的迁移映射

当前 `fetch.py` 不应直接重写成一个“大类”。建议按以下顺序迁移：

| 现有函数/逻辑 | 新位置 | 迁移说明 |
|---|---|---|
| `http` | `transport.py` | 加 timeout、request id、raw body hash、结构化错误 |
| `f` | `numeric.py` | 改为 Decimal 解析，保留原始 string |
| `binance` | `adapters/binance_futures.py` | 拆成 instruments、snapshot、ratios、stream |
| `bybit` | `adapters/bybit_linear.py` | 显式区分 ticker OI 和 account ratio |
| `okx` | `adapters/okx_swap.py` | 记录独立服务缓存带来的 event time 差异 |
| `gate` | `adapters/gate_futures.py` | 加 multiplier、risk tiers、funding interval |
| `bitget` | `adapters/bitget_futures.py` | product type 和可选统计字段 |
| `hyperliquid_ctx` | `adapters/hyperliquid.py` | asset context 进入规范化 snapshot |
| `fetch_whales` | `adapters/hyperliquid.py` + `whale-engine` | adapter 只取数据，候选池/事件/评分留在 engine |
| `norm8h` | `normalizers/funding.py` | 使用真实 interval，返回原始值与归一化值 |
| `build_coin` | `apps/analytics/prism_builder` | 适配器不做跨源聚合和 crowding 业务逻辑 |
| `fetch_macro` | `adapters/macro/` | 不与交易所 adapter 混为一谈 |

### 9.1 第一阶段不改动的部分

- `scripts/fetch.py` 继续作为静态降级层运行；
- `assets/app.js` 和 `index.html` 不直接依赖新 adapter；
- `data/latest.json` schema 1 不在 adapter 层强行升级；
- API/OpenAPI 仍是对外契约，adapter 的内部类型不直接等于 API schema；
- hub 生产采集系统不容器化、不改造，只通过 importer 读取历史数据。

## 10. 测试策略

### 10.1 每个交易所必须有的 fixture

每个 endpoint 至少保存：

- 正常成功响应；
- 空列表响应；
- 缺少可选字段；
- 交易所错误响应；
- 限速响应；
- 符号下架或状态变化；
- 极端价格/费率/大数字；
- 时间戳边界和单位错误；
- WS snapshot、增量、重复和乱序事件。

fixture 必须脱敏，保留原始字段名，并包含抓取日期和官方文档 URL。

### 10.2 Contract tests

统一测试不是“六个 adapter 返回了同样的 JSON”，而是每个 adapter 都满足以下不变量：

```text
same instrument maps to a stable instrument_id
all timestamps are milliseconds
all Decimal values round-trip without float drift
missing is null, never fabricated zero
funding interval is explicit
OI unit is explicit
event identity is deterministic
source envelope is complete
unsupported capability returns typed UnsupportedCapability
retryability is deterministic
```

### 10.3 Replay tests

将 raw fixture 重新送入 normalizer 和 recovery engine，验证：

- 重放结果与第一次结果一致；
- WS 重复事件不会重复入库；
- 断线回补不会漏边界毫秒；
- 同一事件的 raw hash 和 normalized ID 稳定；
- schema 变化会让 fixture 测试失败，而不是悄悄产生 null；
- 历史导入与实时路径生成同样的 HL whale event。

### 10.4 线上验收

第一版上线前至少完成：

1. 4 个核心 venue（Binance、Bybit、OKX、Hyperliquid）连续 14 天采集；
2. 每源分钟级绿色率达到项目目标；
3. WS 主动断线演练后 gap 可发现、可回补或明确标记 unresolved；
4. 限速压测不触发持续封禁；
5. instruments 变更能在一小时内反映；
6. 每个聚合字段能追溯到 venue、endpoint、event time 和 raw hash；
7. Gate/Bitget 作为第二批接入，不阻塞核心四源。

## 11. 实施路线

### M0：契约和实验（1–2 周）

- 建立 `packages/hlens-core/adapters`；
- 定义 `Instrument`、`SourceEnvelope`、`MarketSnapshot`、`FundingPoint`、`OpenInterestPoint`；
- 从 Binance 和 Bybit 各录制一批 fixture；
- 实现 Decimal、时间、错误、质量模型；
- 不接数据库，先完成纯函数 normalization tests。

### M1：REST snapshot（1–2 周）

- Binance、Bybit、OKX、Hyperliquid；
- instruments、ticker/mark、funding、OI；
- endpoint registry 和多维 limiter；
- 输出规范化事件到本地 JSONL/Parquet fixture；
- 接入 `source_health`。

### M2：WS 和恢复（2–3 周）

- mark/trade/liquidation stream；
- 连接管理、订阅确认、断线重连；
- watermark、backfill、dedupe；
- 主动断线和延迟注入测试。

### M3：数据库和 collector（2 周）

- 对接 `instruments`、`ticks_1m`、`funding`、`open_interest`、`liquidations`；
- staging + `ON CONFLICT DO NOTHING`；
- `ingest_watermark` 和 `source_health`；
- 将 `build_coin` 移至 analytics/prism_builder。

### M4：Hyperliquid whale path（2–3 周）

- leaderboard 作为候选池种子；
- `userFills`、`userFillsByTime`、`clearinghouseState`；
- 复合游标和 `content_hash`；
- 事件引擎、PIT 评分、liquidation reliability 门。

### M5：Gate/Bitget 和静态降级（1–2 周）

- 接入 Gate/Bitget；
- 校验与核心四源相同的 normalized contract；
- 新建 `apps/snapshot`，从生产 API 生成 schema 2；
- 保留 `scripts/fetch.py` 作为过渡和应急快照层。

## 12. 最终建议

### 12.1 建议采用的设计

```text
CCXT-like public interface
        +
structured instrument identity
        +
REST snapshot / WS stream / recovery triad
        +
multi-dimensional limiter
        +
source envelope and field-level quality
        +
HL-specific wallet/fill extension
```

它能满足当前静态站到生产系统的演进，同时避免在业务层复制每个交易所的特殊处理。

### 12.2 不建议采用的设计

- 直接把 CCXT 的统一对象当作数据库 schema；
- 用一个 `fetch()` 方法接受任意字符串并返回任意 JSON；
- 只实现 REST，不实现 WS 和回补；
- 只用一个全局 `rateLimit`；
- 用 float 计算合约乘数、OI 和 USD 名义价值；
- 把所有交易所的“清算”都当成同样完整的真实事件；
- 把 Hyperliquid wallet fills 当作普通公共 trades；
- 在 adapter 内计算 crowding、state、sentence 和 scorecard；
- 为了新 adapter 破坏当前 GitHub Pages 静态降级层。

### 12.3 最小可行版本

如果需要尽快开始编码，最小版本只实现：

```text
Instrument discovery
Batch market snapshot
Funding current
Open interest current
Kline backfill
Mark price WS
Typed errors
Endpoint cost limiter
Source envelope
Replay fixtures
```

先不要实现下单、账户、提现、盘口、所有历史清算和所有交易所的全部接口。适配器的第一个成功标准是“可靠地产生可解释、可回补、可追溯的 hlens 数据”，不是“覆盖最多 API”。

## 13. 参考资料

以下资料均为官方或项目原始文档，访问时间为 2026-09-12。交易所接口会变化，实施前应再次核对 endpoint、限速和字段定义。

[^1]: CCXT, [Manual](https://github.com/ccxt/ccxt/wiki/manual). 统一 API、`has`、markets、timeframes、rate limit、endpoint cost、funding/OI 方法和 precision 说明。
[^2]: CCXT, [Base exchange implementation](https://github.com/ccxt/ccxt/blob/master/python/ccxt/base/exchange.py). 基础 exchange 属性、rate limiter、timeout、markets 和 API registry。
[^3]: Binance, [USDⓈ-M Futures REST Market Data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data). Exchange Information、mark price、funding、OI、long/short、taker flow 和 force order 目录。
[^4]: Bybit, [Get Tickers](https://bybit-exchange.github.io/docs/v5/market/tickers)；[Get Historical Funding Rates](https://bybit-exchange.github.io/docs/v5/market/history-fund-rate)。Ticker、OI、funding 和 interval 语义。
[^5]: OKX, [V5 API Guide](https://app.okx.com/docs-v5/en)。Market data、instruments、funding、OI、WS 和多服务缓存说明。
[^6]: Hyperliquid, [Info endpoint](https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint)。`metaAndAssetCtxs`、`clearinghouseState`、`userFillsByTime` 和 portfolio 信息接口。
[^7]: Binance, [Websocket Market Streams](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect)；[Mark Price Stream](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Mark-Price-Stream)；[Liquidation Order Streams](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Liquidation-Order-Streams)。连接、消息限制、断开和实时流说明。
[^8]: Hyperliquid, [Websocket subscriptions](https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/websocket/subscriptions)；[Websocket](https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/websocket)。订阅、snapshot ack、user fills、用户事件和断线恢复说明。
[^9]: Hyperliquid, [Rate limits and user limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)。REST 权重、按返回项加权和 WebSocket 消息上限。
[^10]: Gate, [Futures API v4](https://www.gate.com/docs/developers/apiv4/en/futures/)。合约、乘数、费率周期、风险限额、OI 和清算价字段。
[^11]: Bitget, [Get Historical Funding Rates](https://www.bitget.com/api-doc/classic/contract/market/Get-History-Funding-Rate)。产品类型、分页和结算时间字段。
[^12]: Bitget, [Market WebSocket Ticker Channel](https://www.bitget.com/api-doc/classic/contract/websocket/public/Tickers-Channel)。WS ticker、funding rate 和 open interest 字段。
