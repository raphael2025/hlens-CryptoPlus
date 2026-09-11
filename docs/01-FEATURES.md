# hlens · 功能与显示文档（v3）

> 与 `02-API.md` 的契约一一对应：每个组件标注它消费的端点与字段名（字段名以 `api/openapi.yaml` 为准）。
> 通用规则：每页第一行是答案句（中文 ≤ 20 字，英文 ≤ 25 词）+ 证据标签；四态（加载 / 正常 / 降级 / 错误）；三色（多绿空红中灰）；等宽数字；卡片右上角 "i"。

---

## 0. 站点地图与用户时刻

| 路由 | 时刻 | 问题 | 主端点 |
|---|---|---|---|
| `/` | 随时 | 现在怎么样 | `GET /v1/insights`, `GET /v1/market/breadth`, `GET /v1/coins` |
| `/missed` | 早上 | 我睡觉时发生了什么 | `GET /v1/events?since=` |
| `/coin/{symbol}` | 开仓前 | 我是不是来晚了 | `GET /v1/coins/{symbol}/prism` |
| `/coin/{symbol}/context` | 开仓前 | 这是什么市场 / 过去 24h | `GET /v1/coins/{symbol}/context` |
| `/coin/{symbol}/levels` | 设止损 | 危险在哪 | `GET /v1/coins/{symbol}/levels` |
| `/coin/{symbol}/cost` | 持仓 | 拿着多少钱 | `GET /v1/funding/{symbol}/history`, 本地计算 |
| `/whales` `/whale/{address}` | 无聊 | 大户在干什么 | `GET /v1/whales/*` |
| `/replay` | 亏后 | 当时发生了什么 | `GET /v1/coins/{symbol}/snapshot?at=` |
| `/find` | 选币 | 今天做哪个 | `GET /v1/coins?preset=` |
| `/alerts` | 持仓 | 帮我盯着 | `/v1/me/alerts` |
| `/method` `/scorecard` `/status` `/changelog` | 信任 | 凭什么信你 | `GET /v1/scorecard`, `GET /v1/status` |
| `/me` | – | 我的仓位、关注、设置 | 本地 + `/v1/me/*` |

---

## 1. 句子引擎（答案句如何生成）

**目标**：每页第一行由确定性模板生成，可审计、可测试、双语一致。

**输入**：`PrismSnapshot`（见 API `Prism`）、`MarketState`（三周期）、`Context24h`、`WhaleAgg`、`LiqAgg`。

**结构**：`[状态短语] + [关键数字 ≤ 3] + [标签]`。最多两句；第二句只在存在"矛盾"时出现。

**优先级（从上到下取第一个满足的主句）**：
1. 事件级：投降 / 级联 / 假突破（过去 4h 内）
2. 状态级：三周期组合句（"日线上涨中，4 小时健康回调"）
3. 人群级：拥挤度 |c| ≥ 0.3 或 分歧 ≥ 25 点
4. 默认："均衡，无明显一边倒。"

**矛盾消解**：若人群与状态方向相反（如"上涨·健康回调"且"多头拥挤 +0.4"），第二句用固定连接词"但"：`…健康回调，但多头仍拥挤 +0.40。`；若数据缺失（`whale_n < 3`、源降级），对应短语省略且不补零。

**置信门槛**：任何短语引用的数值必须来自 ≤ 5 分钟的数据；状态判定必须至少 2 根 K 线保持，否则用"转换中"。

**双语**：模板 ID 相同，`zh` 与 `en` 各一份；数字格式由前端统一函数处理，模板不含格式化。

**审计**：每次生成写 `sentence_log(ts, symbol, template_id, inputs_hash, text_zh, text_en)`；`/replay` 显示当时的句子。

**测试**：`tests/golden/sentences/*.json` 50 个历史快照 + 期望模板 ID；CI 断言。上线前：回放 30 天前 20 币的每小时句子，人工抽 200 句评分（清晰 / 准确 / 不矛盾），均分 ≥ 4/5。

**模板样例（ID → zh / en）**
- `S.UP_HEALTHY_PB`："{tf1}上涨中，{tf2}健康回调：OI {oi_chg}，回撤 {atr_x} ATR。" / "{tf1} uptrend, {tf2} healthy pullback: OI {oi_chg}, {atr_x} ATR off the high."
- `C.CROWDED_LONG`："多头拥挤 {crowding}，散户 {retail} 做多，费率年化 {apr}。" / "Longs are crowded ({crowding}): retail {retail} long, funding {apr} APR."
- `D.RETAIL_VS_WHALE`："散户 {retail} 做多，大户 {whale} 做多，差 {gap} 点。" / "Retail {retail} long vs whales {whale} long, a {gap}-pt gap."
- `E.CASCADE`："{ago} 前发生{side}爆仓级联 {usd}，价格 {move}。" / "{side} liquidation cascade {usd} {ago} ago, price {move}."

---

## 2. 状态词典与校准

词典 14 态（上涨 5 / 下跌 5 / 震荡 3 / 压缩 1 / 转换 2）与规则见 `config/state_rules.v1.yaml`（开发文档附示例）。显示：状态芯片（颜色 = 组）、点开规则命中表、30 天状态色带、状态统计卡。

**校准流程（P0 交付，之后每季度）**
1. 数据：前 50 币，2 年 1h K 线 + OI + 费率 + 爆仓（Gate/OKX 真实 + Binance 补齐）。
2. 运行 `analytics/calibrate_states.py` → 报告：每状态频率、中位持续、转移矩阵、进入后 4/24/72h 收益分布、相邻小时抖动率。
3. 验收：频率 2%–35%；中位持续 ≥ 3 根；最大占比 ≤ 40%；抖动率 < 15%；否则调阈值重跑。
4. 输出 `config/state_rules.v{n}.yaml` + `docs/reports/state-calibration-v{n}.md`，进变更日志。

---

## 3. 页面组件规格

### 3.1 首页 `/`
| 组件 | 内容 | 数据 | 刷新 |
|---|---|---|---|
| `MarketSentence` | 一句话市况 + 标签 | `insights.sentence` | 60s |
| `QuestionCards`（6） | 来晚了吗 / 危险在哪 / 大户 / 睡觉时 / 多少钱 / 谁快爆 | `insights.cards[]` | 60s |
| `MyPositionsBar` | 每仓一行：盈亏 %、距强平 %、今日费率、风险句 | 本地仓位 + `prism` | 10s |
| `PrismChart` | 前 30 币竖向光谱：颜色 = crowding，高度 = oi_total_usd，宽度等分；hover 显示七个数值；点击进币页；"保存图片" | `coins[]` | 60s |
| `MarketRegimeRow` | BTC 三周期状态芯片 + 波动状态 + 动力来源 | `context(BTC).state` | 60s |
| `BreadthBars` | 前 100 币：24h 上涨 % / EMA20 之上 % / OI 上升 % + 一句话 | `market/breadth` | 5m |
| `StateChipsRow` | BTC 与前 10 币状态芯片 | `coins[].state` | 60s |
| `SourceDots` | 源红绿灯 + 更新时间 | `status` | 30s |

### 3.2 我睡觉时 `/missed`
`RangePicker`（8h / 24h / 自定义，默认用户时区昨夜 23:00 起）· `SummaryThree`（三句自动总结）· `EventTimeline`（五类：价格 ±3%/15m、爆仓 ≥ 1000 万/5m 或级联、费率 ±0.05% 或价差 > 0.03%、大户 ≥ 500 万事件或被强平、拥挤度穿越 ±0.3；每条：图标、标题、数字、"当时看"）· `SubscribeDigest`。数据：`GET /v1/events?since&until&types`。

### 3.3 币页 `/coin/{symbol}`
| 组件 | 内容 | 字段 |
|---|---|---|
| `CoinHeader` | 名、价、24h、总 OI、成交、六所可用图标、关注、分享 | `prism.price`, `chg24h_pct`, `oi_total_usd`, `venues[].ok` |
| `AnswerSentence` | 主句 + 可选矛盾句 + 标签 | `prism.sentence` |
| `StateStrip` | 三周期状态芯片 + 30 天色带 | `prism.state`, `GET …/state-history` |
| `SpectrumRows`（7） | 散户多头 / 大账户多头 / 主动买入 / 大户多头 / 费率 8h / OI 分位 / 24h 爆仓多空比；中心对齐条；右侧数字；hover 各所明细与 7 日迷你图 | `prism.facets.*` |
| `CrowdingGauge` | −1..+1 + 30 天分位 | `prism.crowding`, `crowding_pctl_30d` |
| `RetailVsWhale` | 事实句 + 与我仓位关系 | `facets.retail_long`, `facets.whale_long`, `whale_n`, `funding_8h` |
| `TimeSeriesPanel` | 价格 + 叠加（拥挤度 / 费率 / OI / 散户 / 大户净仓 / 爆仓柱）；15m–1d；1d–90d | `GET …/history?fields=` |
| `VenueTable` | 价 / 费率原始·8h·年化 / 结算倒计时 / OI / 成交 / 散户多头；最便宜所高亮 | `prism.venues[]` |
| `WhalesOnCoin` | 多空堆叠 + 前 10 仓位（距强平 %） | `GET /v1/whales/positions?symbol=` |
| `OIQuadrantCard` | 当前象限 + 站内统计 | `context.oi_quadrant`, `scorecard` |
| `SubpageLinks` | 危险在哪 / 多少钱 / 回放 / 市场环境 | – |

### 3.4 市场环境 `/coin/{symbol}/context`
`FourSentences`（状态 / 位置 / 动力 / 波动）· `StatePanel`（三周期标签、均线堆叠、震荡区间、波动分位、BTC 相关与 Beta、状态解读一句）· `KeyLevelsLadder`（昨日高低收、周月日开盘、日 VWAP ±1σ、锚定 VWAP、24h POC/HVN/LVN、整数关口、0.5 位；与爆仓堆重合加粗）· `Last24hPanel`（高低区间与位置、量比、最大 15m 波动、三时段涨跌、OI 象限、费率变化、爆仓合计、大户净开仓、迷你时间线）· `DriverPanel`（现货 vs 永续标签与溢价曲线、CVD 双线、突破真假三信号、盘口厚度、OI/市值分位、期权隐含波动与 skew、基差一致性）· `RiskSizer`（风险预算 → 名义与杠杆；已有仓位时对比）· `EventCalendar`。数据：`GET /v1/coins/{symbol}/context`（一次返回全部，见 API `Context`）。

### 3.5 危险在哪 `/coin/{symbol}/levels`
`PriceLadder`：竖向轴 ±15%（可缩放）；左：模型爆仓堆柱（`levels.liq_clusters[]`，`模型估算`）；右：大户强平价点（`levels.whale_liq[]`）、24h 真实爆仓价位（`levels.real_liq_24h[]`）；中：我的开仓 / 止损 / 强平。`LadderSentence`：上方 / 下方最近堆与距离。`ClusterTotals`、`RealVs24h`、`ModelSliders`（杠杆权重可调，前端重算）、`HeatmapTab`（ECharts 热力图，7/30 天 × 价格）。

### 3.6 拿着多少钱 `/coin/{symbol}/cost`
`CostInputs`（方向、金额、时长；默认读我的仓位）· `CostTable`（每 8h / 每天 / 期间；六所各自；"换到 X 省 Y"）· `CumFundingChart`（30 天该方向累计）· `ArbNote`（跨所价差扣费年化，`启发式`）。

### 3.7 大户 `/whales`
`WhaleSentence` · `EventFeed`（实时；一条一句；1h/4h/24h 回填颜色；筛选币 / 类型 / 金额 / 关注）· `BetsBoard`（前 20 仓位；按距强平排序即 `RektSoonBoard`）· `CapitulationBoard`（24h 被强平与 ≥ 100 万亏损平仓）· `WinnersLosers`（24h 实现盈亏）· `ByCoinStack` · `ShareCard`（每卡可导出图片，带水印链接）。钱包页 `/whale/{address}`：`EquityCurve`（`portfolio`）、`PositionsTable`、`FillsTable`、`StyleTags`、`WalletEventHistory` + 命中率、`FollowButton`、`AlertButton`。
样本规则：任何统计显示 `n`；n < 30 显示"样本不足"，不显示百分比。

### 3.8 回放 `/replay`
`ReplayPicker`（币 + 时间；可从时间线 / 我的仓位开仓时间进入）· `SnapshotPrism`（七行 + 拥挤度 + 当时的句子）· `AroundChart`（±6h 价格 / 费率 / OI / 爆仓 / 大户事件）· `AroundEvents` · `CompareNow` 开关 · `ReplayCard` 导出。数据：`GET /v1/coins/{symbol}/snapshot?at=`。

### 3.9 今天做哪个 `/find`
`PresetChips`（空头太挤 / 多头太挤 / 大户与散户相反 / OI 刚冲高 / 有人付钱给你 / 大户刚翻转 / 健康回调中 / 压缩中）→ `ResultList`（币、价、迷你光谱、一句为什么入选、状态芯片、关注 / 告警按钮）· `AdvancedTable`（列配置、条件、保存、导出 Pro）。数据：`GET /v1/coins?preset=&sort=&cols=`。

### 3.10 告警 `/alerts`
`RuleWizard`（对象 → 模板 → 渠道）· `TemplateList`（每条一句人话）· `DeliveryLog` · `PauseAll`。消息：一句话 + 三数字 + 迷你光谱图 + "当时看"深链。免费 3 条 + 每日摘要；Pro 50 条 + Webhook。

### 3.11 我的仓位模式（全站）
`PositionForm`（币、方向、开仓价、杠杆；可选金额、止损）→ 本地 `localStorage`（登录后同步 `/v1/me/positions`）。影响：`MyPositionsBar`、`SpectrumRows` 上"你"的标记、`RetailVsWhale` 改为与你的关系、`PriceLadder` 画三条线、`CostTable` 默认填充、`EventFeed` 筛"与我反向"、`RuleWizard` 一键仓位套餐（距强平 < 10%、费率对我不利、大户反向 ≥ 500 万、级联、人群反向拥挤突破）。免责句常驻。

### 3.12 方法 / 记分板 / 状态 / 变更
`MetricCards`（定义、公式、来源、局限、频率）· `EvidenceLibrary`（每条已验证标签的出处）· `ScorecardTables`（状态 × 周期、大户事件 × 类型 × 方向、OI 象限；n、中位、分位、CI；滚动 30/90/365）· `SourceStatus`（延迟与 30 天可用率）· `Changelog`。

---

## 4. 视觉识别

- **棱镜图（PrismChart）**：画布宽度等分为 N 列（N ≤ 30）；每列一条竖向渐变条，高度 = `oi_total_usd` 相对最大值的平方根比例（避免 BTC 压扁其余），颜色 = crowding 映射到品牌渐变（−1 紫 → 0 青 → +1 橙红），条内部用细横线标出 `retail_long` 与 `whale_long` 的位置（两条线的距离即分歧）；底部币名；顶部数值 hover 出现。动效：数据更新时高度与颜色 300ms 缓动。导出：1200×630 PNG，右下角水印 `hlens.xyz`。
- **设计令牌**：背景 `#0b0d12` / 卡 `#161a23` / 线 `#242a36` / 文本 `#e6e9ef` / 次 `#8b93a3` / 多 `#22c55e` / 空 `#ef4444` / 警 `#fbbf24` / 品牌渐变 `#8b5cf6→#3b82f6→#22d3ee→#22c55e→#fbbf24→#ef4444`；亮色模式同色相降饱和。字体：Inter + Noto Sans SC；数字 JetBrains Mono。间距 8px 栅格；圆角 12px；阴影不用，靠边框分层。
- **分享卡三模板**：`CoinCard`（币 + 句子 + 七行光谱）、`WhaleCard`（事件一句 + 仓位数字）、`MarketCard`（全市场光谱图 + 三要点）。统一 1200×630，右下水印，左上日期与 UTC 时间。
- **状态芯片**：胶囊，组色 10% 底 + 组色文字，左侧 6px 圆点；hover 显示规则命中表。
- **P0 交付**：设计稿（首页、币页、市场环境、价格标尺、大户、手机版五屏）评审后再写前端。

---

## 5. 大户覆盖扩展

| 来源 | 内容 | 接口 |
|---|---|---|
| Hyperliquid 排行榜前 1000 | 候选 | `stats-data…/leaderboard`（每小时） |
| Hyperliquid 大额成交发现 | 单笔 ≥ 100 万美元成交的地址加入候选 | WS `trades` |
| OKX 带单交易员公开榜 | 中心化交易所大户样本：交易员列表、当前仓位、历史 | `/api/v5/copytrading/public-lead-traders`、`public-current-subpositions`（P3 前验证可用性与限速） |
| 用户关注 | 用户添加的地址进入高频池 | – |
轮询：活跃前 200 每 60 秒；其余每 10 分钟；fills 增量每 60 秒。

---

## 6. 显示规范摘要（全站）
答案在前 · 三色 · 等宽数字 · K/M/B · 费率四位小数 · 用户时区 hover UTC · "i" 卡 · 四态 · 不显示零冒充 · 分享水印 · 手机底部五键（现在 · 币 · 大户 · 盯盘 · 我）· 首屏 1 秒可读 · 交互 100ms 反馈 · 中英术语表统一（crowding 拥挤度、liquidation cluster 爆仓堆、healthy pullback 健康回调、cascade 级联、capitulation 投降）。
