# hlens · 功能与显示文档（v3.1）

> 字段名以 `api/openapi.yaml` 为准；本文引用的路径形如 `prism.facets.retail_long_share`。每个组件表含四态列（空 / 降级 / 骨架高度）。

## 0. 站点地图
| 路由 | 时刻 | 问题 | 主端点（次端点） |
|---|---|---|---|
| `/[locale]` | 随时 | 现在怎么样 | `getInsights`, `listCoins`, `getBreadth` |
| `/missed` | 早上 | 我睡觉时发生了什么 | `listEvents` |
| `/coin/{symbol}` | 开仓前 | 我是不是来晚了 | `getPrism`（`getStateHistory`, `listWhalePositions`, `getHistory`, `listLiquidations`） |
| `/coin/{symbol}/context` | 开仓前 | 这是什么市场、过去 24h | `getContext` |
| `/coin/{symbol}/levels` | 设止损 | 危险在哪 | `getLevels` |
| `/whales` `/whale/{address}` | 无聊 | 大户在干什么 | `getWhaleBoards`, `listWhaleEvents`, `getWallet` |
| `/replay?symbol=&at=` | 亏后（从"当时看"进入） | 当时发生了什么 | `getSnapshot` |
| `/find` | 选币 | 今天做哪个 | `listCoins?preset=` |
| `/alerts` | 持仓 | 帮我盯着 | `listAlerts`, `createAlert` |
| `/method` `/scorecard` `/evidence` `/status` `/changelog` | 信任 | 凭什么信你 | `getScorecard`, `getStatus` |
| `/me` | – | 我的仓位、关注、告警、API key、设置 | 本地 + `/me/*` |
| `/terms` `/privacy` `/disclaimer` `/sources` | – | 法律 | 静态 |
手机底部五键：现在（含"我睡觉时"顶部入口）· 币（含"今天做哪个"）· 大户 · 盯盘 · 我。
URL 状态约定：`?tf=1h|4h|1d`、`?range=1d..90d`、`?tab=`、`?preset=`、`?sort=`、`?at=<ms>`、`?since=<ms>`；`Event.replay_url` 固定为 `/replay?symbol={s}&at={ms}`；列表→详情返回保持滚动位置。

## 1. 句子引擎
- **输入**：`Prism`、`MarketState`、`Context`、`WhaleCoinAgg`、`LiqAgg`，以及各字段的 30 天分位。
- **输出**：`Sentence{template_id, vars, tags, zh, en, contradiction?}`。`vars` 是变量槽（`{name: {v, type: pct|usd|ratio|bps|ts|atr|count, prec}}`），**前端按 locale 渲染**（zh 用万/亿，en 用 K/M/B；时间用用户时区，hover 显示 UTC）；`zh/en` 仅作服务端兜底与日志。
- **每句必须含对比**：至少一个分位或历史频次（"比过去 30 天 91% 的时候都挤"、"90 天里分歧第 3 大"）。只念数字的模板不允许上线。
- **优先级**：事件级（投降 / 级联 / 假突破，4h 内）→ 状态级（三周期组合）→ 人群级（|crowding| ≥ 0.3 或分歧 ≥ 25 点）→ 默认（"均衡"）。
- **矛盾消解**：方向相反时第二句用"但"；缺数据的短语省略；置信门槛按 §00 §4.4 的分面新鲜度预算，超限短语置灰而非整句降级。
- **后果分布句**（允许，非建议）："过去 2 年拥挤度 > +0.4 出现 187 次，之后 24h 中位 −0.3%，22% 出现过 ≥ 5% 回撤。" 只在记分板对应分组 n ≥ 100 时出现，标 `站内自验证`。
- **上线范围**：S2 五个模板（状态组合、拥挤、分歧、事件、默认）；黄金测试用**合成输入**（覆盖 v1 规则可达的 13 态 × 矛盾 × 缺数据；含 OI/爆仓的 3 态随 v2 规则补），CI 断言 `template_id`；真实回放人工评审推迟到自有数据满 30 天。
- **审计**：`sentence_log` 只在 `inputs_hash` 变化时写入，存 inputs 与 template_id，不存文本。

## 2. 状态词典与校准
- 16 态：上涨 5（推进 / 回调·浅 / 回调·带杠杆堆积 / 过热 / 衰竭）、下跌 5（镜像 + 投降）、震荡 3（上沿 / 中部 / 下沿）、压缩 1、转换 2（突破待确认 / 假突破）。`StateId` 枚举为准。其中 `up_pullback_leveraged`、`down_bounce_leveraged`、`down_capitulation` 依赖 OI/爆仓，属 v2 规则；v1 可达 13 态。
- **命名规则**：在记分板该状态 n ≥ 100 且 FDR 显著之前，界面用**中性名**（`display_tier: provisional`，如"回调·浅"），之后才可启用带判断的名（"健康回调"）。状态芯片旁永远一行：`近 2 年出现 312 次 · 之后 24h 中位 +0.4% · 46% 跌破本次低点`，或"样本不足"。
- **v1 规则只用价格类特征**（EMA/ATR/ADX/结构/量比/RSI），2 年 K 线可回补；含 OI、爆仓、CVD 的条件进 `state_rules.v2`，自有数据满 90 天后校准。
- 校准流程：前 50 币、2 年 1h；输出频率、中位持续、转移矩阵、前瞻分布、抖动率；验收：频率 2–35%、中位持续 ≥ 3 根、最大占比 ≤ 40%、抖动 < 15%；报告入 `docs/reports/`。
- 显示：状态芯片（组色）+ 点开规则命中表 + 30 天色带 + 统计行。

## 3. 页面组件（每表含四态）

### 3.1 首页
| 组件 | 内容 | 数据 | 刷新 | 空 / 降级 / 骨架 |
|---|---|---|---|---|
| `AbnormalThree` | **今天最不正常的 3 个币**：币 + 一句为什么（分歧最大 / 费率最贵 / 大户刚翻转）+ 数字 + 分位 | `insights.abnormal[]` | 60s | 无则显示"今天没有异常"；降级灰卡 + as_of；高 96px |
| `MarketSentence` | 一句市况 + 标签 | `insights.sentence` | 60s | 缺则隐藏；单行骨架 |
| `QuestionCards`（3） | 来晚了吗 / 危险在哪 / 大户在干什么 | `insights.cards[]` | 60s | 每卡独立降级；高 120px |
| `MyPositionsBar` | 每仓：盈亏 %、距强平 %、今日费率、风险句 | 本地仓位 + WS `prism:{symbol}`（REST 首屏 `listCoins?symbols=`） | WS | 无仓位显示"＋ 我的仓位"；高 56px/仓 |
| `StateChipsRow` | BTC 与前 10 币状态芯片 | `listCoins` 的 `state_group_*`、`state_*`、`state_since_ts` | 60s | – |
| `PrismChart` | 前 30 币光谱（次要区块，可折叠） | `listCoins` | 60s | 表格视图切换 |
| `BreadthBars` | 三根进度条 + 一句话 + 7 天时段格 | `getBreadth` | 5m | – |
| `SourceDots` | 红绿灯 + 覆盖币数 + 更新时间（**如实显示，如"覆盖 8 币 · 2 分钟前"**） | `getStatus` | 30s | – |
| `MacroStrip` | 恐惧贪婪 + 稳定币 7 天变化 + BTC 主导率，一行三项 | `getMacro` | 1h | 缺项隐藏 |

### 3.2 我睡觉时 `/missed`
`RangePicker`（8h/24h/自定义）· `SummaryThree`（先说我持仓的币："你的 SOL 多单夜里最深浮亏 −6.2%（03:40），现在 −1.1%"）· `EventTimeline`（7 类：price / liq / cascade / funding / whale / crowd / state，各自图标与样式；每条：一句话 + 三个数字 + "当时看"）· `SubscribeDigest`。数据 `listEvents?since&until&types`。

### 3.3 币页 `/coin/{symbol}`
| 组件 | 内容 | 字段 |
|---|---|---|
| `CoinHeader` | 名、价、24h、总 OI、成交、所可用图标、关注、分享 | `prism.price`, `prism.chg24h_pct`, `prism.facets.oi_total_usd`, `prism.venues[].ok` |
| `AnswerSentence` | 主句（含分位）+ 矛盾句 + 标签 | `prism.sentence` |
| `StateStrip` | 三周期芯片 + 统计行 + 30 天色带 | `prism.state`, `getStateHistory` |
| `SpectrumRows`（6） | 散户多头 / 大账户多头 / 主动买入 / 大户多头 / 费率 8h / OI 分位；中心对齐条 + 数值 + 分位；**大户行 n < 10 置灰写"只追踪到 n 个仓位，样本太少"**；hover 显示各所当前值（`listLsRatios`）与聚合 7 日迷你图（`getHistory?fields=retail_long_share,…`） | `prism.facets.*`, `prism.facets.whale_long_share.n` |
| `CrowdingGauge` | −1..+1 + 30 天分位 | `prism.crowding`, `prism.crowding_pctl_30d` |
| `RetailVsWhale` | 事实句 + 与我仓位关系 | `facets.retail_long_share`, `facets.whale_long_share`（含 `.n`）, `facets.gap_pts`, `facets.funding_8h` |
| `CostRow` | 一行："你这个仓位每天 12.4 美元，换到 OKX 省 4.1"，点开展开表 | `prism.venues[]` + 本地仓位 |
| `TimeSeriesPanel` | 价格 + 叠加：crowding / funding_8h / oi_total_usd / retail_long_share / whale_net_usd / liq_long_usd+liq_short_usd | `getHistory?fields=` |
| `VenueTable` | 价、费率原始·8h·年化、结算倒计时、OI、成交、散户多头；最便宜所高亮 | `prism.venues[]` |
| `WhalesOnCoin` | 多空堆叠 + 前 10 仓位（距强平 %） | `listWhalePositions?symbol=` |
| `LiqStream` | 该币最近真实爆仓 20 条（标"下界"） | `listLiquidations?symbol=` |
| `OIQuadrantCard` | 当前象限 + 记分板统计 | `context.last24h.oi_quadrant`, `getScorecard` |
四态：每面独立；`whale_n<10` 置灰；源降级灰条 + as_of；骨架按行高 44px。

### 3.4 市场环境 `/coin/{symbol}/context`
首屏只留：`FourSentences`（状态 / 位置 / 动力 / 波动）+ `KeyLevelsLadder`（昨日高低收、周月日开盘、日 VWAP、24h POC、整数关口；与爆仓堆重合加粗）+ `Last24hPanel`（高低区间与位置、量比、最大 15m 波动、三时段、OI 象限、费率变化、爆仓合计）。"更多"折叠：均线堆叠、BTC 相关、现货 vs 永续标签与溢价曲线。**不做（v1.1）**：CVD、盘口厚度、期权、基差。`RiskCalc`：纯计算器，用户自填风险金额与止损距离（ATR 倍数或价位），输出算术结果，不预填、不推荐。`EventCalendar`。

### 3.5 危险在哪 `/coin/{symbol}/levels`
`PriceLadder`：竖向轴 ±15%。**真实优先**：右侧大户真实强平价（点开地址可查）、过去 24h 真实爆仓价位；左侧模型堆缩为灰色背景并标"模型，不是订单"。中：我的开仓 / 止损 / 强平。`LadderSentence` 先说真实："下方 −2.4% 有 2 个大户合计 1.8 亿美元的多头强平价"。`ModelSliders` 300ms 防抖后重新请求 `getLevels?lev_weights=`（不在前端重算）。热力图为 Tab，懒加载。移动端改竖向列表 + 距离百分比。

### 3.6 大户 `/whales`
`WhaleSentence` · `EventFeed`（实时；一句话一条；1h/4h/24h 回填颜色；筛选）· `BetsBoard` / `RektSoonBoard`（按距强平排序）· `ClosedLossBoard`（24h 被强平与 ≥ 100 万实现亏损的平仓；措辞"已平仓，实现亏损 X"）· `WinnersLosers` · `ByCoinStack` · **`WhaleCard`**（主分享卡：一句话大字 + 三个数字 + 地址前 6 位 + 可点验证链接）。钱包页首行："近 90 天开仓 48 次，之后 24h 有 61% 在赚（n=48）"，不准也写；`EquityCurve`（`wallet.equity_curve`）、仓位、成交、事件时间线、关注、告警。样本规则见 §00 §4.3。钱包政策见 §00 §4.9。

### 3.7 当时看 `/replay?symbol=&at=`
从事件、时间线、我的仓位开仓时间进入；`SnapshotPrism`（当时六面 + 拥挤度 + 当时句子）· `AroundChart`（±6h）· `AroundEvents` · `CompareNow` · `ReplayCard`。数据 `getSnapshot`。

### 3.8 今天做哪个 `/find`
8 个中性名预设：空头持仓集中度高 / 多头持仓集中度高 / 大户与散户相反 / OI 24h 增幅大 / 费率为负 / 大户刚翻转 / 回调·浅 / 压缩。结果行：币、价、迷你光谱、一句为什么（含分位）、状态芯片、关注 / 告警。高级表格不做（v1.1 Pro）。

### 3.9 告警 `/alerts`
免费 5 条（必含"距强平 < 10%"模板）+ 每日摘要（Telegram 或 Web Push）；Pro 50 条。模板（人话）：距强平 < 10%（免费）；状态变化（免费）；拥挤度穿越（免费）；**Pro 专属三种**：有大户在我反向新开 ≥ 500 万（`whale_event`）、我的币开始连环爆仓（`cascade`，相对阈值）、我持仓方向费率转为不利且年化 > 20%（`funding_apr_pct`）。渠道：Telegram（主）、Email（仅规则告警，不发摘要）、Web Push（`/me/push-subscriptions`；iOS 需先安装 PWA）。消息：一句话 + 三数字 + 迷你光谱图 + "当时看"深链。

### 3.10 我的仓位（全站）
- 输入：表单或**粘贴一行**"BTC 多 78200 10x"；字段 `symbol, side, entry, lev, venue?, size_usd?, stop?`；本地 `localStorage['hlens.pos.v1']`（含 `schema`、`client_id`、`updated_ts`）；登录后与 `/me/positions` 按 `client_id` 双向同步，`updated_ts` 大者胜，冲突弹合并面板。
- 换来三件事（首页与币页顶部）：① 我的强平价离最近真实强平堆 / 大户强平价 X%；② 我这个方向今天付 X 美元、按月 X 美元（占本金 X%）；③ 换到 X 所每天省 X 美元。
- 隐私文案："不联网。按 F12 看 Application → Local Storage 可以自己查。" + "一键清空"。
- 免责一句常驻。

### 3.11 方法 / 记分板 / 证据 / 状态 / 变更
`MetricCards`（定义、公式、来源、局限、频率）· `EvidenceLibrary`（`listEvidence`）· `ScorecardTables`（分组、n、中位、p25/p75、命中率与 Wilson 区间、q 值；n < 30 显示"样本不足"）· `SourceStatus` · `Changelog`（`listChangelog`）。记分板页顶："历史分布，非预测"。本组页面与钱包页、币页**不出现返佣入口**；返佣只在 `/sources` 与 `/me`。

## 4. 术语气泡（首次出现给一次，不跳转）
拥挤度 = 人群一边倒的程度（+1 全在做多）· OI 分位 = 现在的持仓量比过去 90 天 X% 的时间都高 · 压缩 = 波动被压扁，快要选方向 · 级联 = 连环爆仓 · 投降 = 大规模割肉 · POC = 过去 24h 成交最密的价格 · 站内自验证 = 我们自己回测过 n 次，结果在这 · 模型估算 = 用假设算出来的，不是真实挂单 · 分歧 = 散户多头占比与大户多头占比的差。

## 5. 视觉识别
- **令牌**：字号 12/13/15/17/22/30；行高 1.5；断点 360/744/1100；z-index 层 10/20/30；间距 8px 栅格；圆角 12。色：背景 `#0b0d12`、卡 `#161a23`、线 `#242a36`、文本 `#e6e9ef`、次 `#8b93a3`、多 `#22c55e`、空 `#ef4444`、警 `#fbbf24`；状态组色：up `#22c55e`、down `#ef4444`、range `#8b93a3`、squeeze `#8b5cf6`、transition `#fbbf24`（各 10% 底）；图表序列 `--chart-1..6`：`#22d3ee #fbbf24 #8b5cf6 #22c55e #ef4444 #f472b6`；亮色模式：背景 `#f7f8fa`、卡 `#ffffff`、线 `#e5e7eb`、文本 `#111827`、次 `#6b7280`，语义色不变。
- **PrismChart**：单张 Canvas；N ≤ 30 列；高度 = OI 平方根比例；颜色 = crowding 映射品牌渐变；列内两条细线 = 散户与大户多头位置；每列数值标签；`prefers-reduced-motion` 直接跳变；提供表格视图。
- **分享卡**：`WhaleCard`（主）、`CoinCard`、`MarketCard`；1200×630；`GET /og/{template}`，Satori 渲染，CJK 用运行时子集字体（`subset-font`）或预生成 3500 字子集，产物按 `inputs_hash` 缓存到 R2，`Cache-Control: immutable`。
- **状态芯片**、**EvidenceTag 组件**（六值固定中英译名与颜色）。
- S2 前出五屏设计稿评审。

## 6. 前端数据流
1. SSR 取该路由 1–2 个端点，`dehydrate` 注入 TanStack Query；答案句随 HTML 到达（LCP 元素零 JS 依赖）。
2. Cloudflare 边缘缓存 SSR HTML，`s-maxage` 对齐 `x-hlens-cache`，`stale-while-revalidate=600`。
3. 客户端 `openapi-fetch` + TanStack Query，`staleTime` = `x-hlens-cache`，`queryKey=[resource, ...params]`。
4. 单条 WS；v1 每标签页一条，S2 后优化为 SharedWorker + 多标签页 leader 选举；订阅集合 = 挂载组件注册的并集，引用计数归零才退订。
5. WS 消息 JSON Merge Patch 合并进 `setQueryData`，不触发网络请求。
6. `seq` 缺口或心跳超时 → 频道标 `stale` → `invalidateQueries` 走 REST 补齐，卡片角标"补齐中"。
7. 我的仓位独立于 Query 缓存，通过 selector 与行情 join。
8. 离线：`persistQueryClient` 落 IndexedDB；Service Worker 只缓存壳与最近币页；恢复后按 `as_of` 显示"数据过时"横幅。

## 7. 性能与规范
- 分路由预算：壳层 ≤ 120 KB gzip（零图表库）；`TimeSeriesPanel` 动态加载 lightweight-charts；热力图动态加载 `echarts/core` 定制构建；CI `size-limit` 门禁。
- LCP 元素 = 答案句；实验室 ≤ 2s、现场 p75 ≤ 2.5s；交互 100ms。
- i18n：路由 `/[locale]/…`，`hreflang`，`Accept-Language` 一次 302 + cookie；数字格式按 locale；术语表中英统一。
- SEO：每个币页与术语页生成中英 title/description/OG、结构化数据、`sitemap.xml`。
- 无障碍：键盘可达、AA 对比、图表表格等价物、reduced-motion；并入每页 DoD。
- 测试：Playwright 关键路径、axe、Lighthouse CI、size-limit。
