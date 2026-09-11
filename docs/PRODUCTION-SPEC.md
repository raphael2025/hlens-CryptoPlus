# hlens · 生产级产品与技术定义（v2）

> 状态：定稿草案，2026-09-11。取代 `SPEC.md`（v1 是零服务器版本，保留作为 v0.1 的记录）。
> 定位不变：**合约持仓棱镜**。把一个币的永续市场拆成散户、大账户、链上大户、资金费、持仓量、爆仓、期权七个面并排展示，分歧即信号；所有解读带证据标签；页面即 API 即 MCP。
> 生产级的含义：7×24 采集不断线、秒级数据、告警能到手机、有账号和 API key、有监控和备份、可以对外承诺可用性。
> **页面与显示以 [USER-SPEC.md](USER-SPEC.md) 为准**（按用户问题组织，含「我的仓位」模式）；本文第 2 节的页面清单保留作组件参考。服务器使用你现有的新加坡（主）与东京（采集与热备）两台，见 USER-SPEC 第 6 节。

---

## 0. 总览与决策

| 项 | 决定 | 理由 |
|---|---|---|
| 服务器 | 你现有的新加坡（主：数据库、API、Web、告警）+ 东京（采集、大户引擎、热备）两台 + Cloudflare 免费层 | 非美 IP 解除 Binance/Bybit 封锁；两台分离采集与服务，互为备份 |
| 后端语言 | Python 3.12：采集器 asyncio + FastAPI | 与你现有仓库（smip、hlens、hl-research）一致，可直接迁移采集逻辑 |
| 数据库 | PostgreSQL 16 + TimescaleDB（时序）+ Redis 7（缓存、发布订阅、限流） | 时序压缩 90% 以上，连续聚合免去手写汇总 |
| 前端 | Next.js 15（App Router）+ TypeScript + Tailwind + ECharts（热力图）+ lightweight-charts（K 线/时序） | SSR 保证 SEO 与首屏；你已有 Next.js 项目经验 |
| 实时 | WebSocket（后端 FastAPI）+ Redis Pub/Sub；前端断线重连、退化为 10 秒轮询 | 爆仓和大户事件需要秒级 |
| 告警 | Telegram Bot（主）、Email（Resend 免费 3000 封/月）、Web Push | 全球用户以 Telegram 为主 |
| 账号 | Email 魔法链接 + Google/GitHub OAuth；API key；无密码 | 减少攻击面 |
| 支付 | Stripe（全球）；付费只买"更快更多的告警与 API"，永不卖信号 | 与诚实定位一致 |
| 部署 | Docker Compose，GitHub Actions 构建镜像并 SSH 部署；Cloudflare Tunnel 暴露，VPS 不开公网端口 | 单机可维护，安全 |
| 监控 | Prometheus + Grafana（同机）、Sentry（免费层）、Better Stack 外部拨测（免费层） | 出问题先于用户知道 |
| 备份 | pg_dump 每日 → Cloudflare R2（10 GB 免费）；保留 30 天 | 数据是资产 |
| 历史 | 原始 tick 级只留 7 天；1 分钟聚合留 1 年；1 小时聚合永久 | 成本可控 |

**可用性目标（SLO）**：API 月可用 99.5%；数据新鲜度：行情 ≤ 5 秒、持仓比 ≤ 5 分钟、大户仓位 ≤ 60 秒、告警端到端 ≤ 10 秒。

---

## 1. 功能模块总表

| # | 模块 | 一句话 | 免费 | Pro（约 9 美元/月） |
|---|---|---|---|---|
| M1 | 市场棱镜 | 单币七面并排 + 拥挤度 + 分歧陈述 | 全部 | – |
| M2 | 全市场筛选器 | 300+ 币，30 列指标，可排序筛选保存 | 全部 | 保存 10 个筛选 |
| M3 | 资金费率中心 | 六所费率、8h 口径、预测费率、跨所价差、套利收益、历史 | 全部 | – |
| M4 | 持仓与多空 | OI 历史、账户/大户/持仓三种多空比、OI 象限（价涨 OI 涨/跌） | 全部 | – |
| M5 | 爆仓中心 | 真实爆仓流（四所 WebSocket）+ 24h 聚合 + 模型热力图 + 级联检测 | 延迟 5 分钟 | 实时 + 全币热力图 |
| M6 | 大户透视 | HL 钱包、仓位、事件带、记分板、钱包主页、关注列表 | 前 100 钱包 | 前 1000 + 关注 50 个钱包 |
| M7 | 期权面 | Deribit DVOL、PCR、最大痛点、期限结构、25d skew、IV−RV | 全部 | – |
| M8 | 基差与期限结构 | 季度/交割合约年化基差，与费率年化并排 | 全部 | – |
| M9 | 宏观条 | F&G、稳定币供应、ETF 流、BTC 主导率、Polymarket | 全部 | – |
| M10 | 告警中心 | 规则引擎：费率、OI 变化、爆仓激增、大户事件、分歧阈值、价格 | 3 条规则，每日摘要 | 50 条规则，实时推送，Webhook |
| M11 | 关注列表与个人设置 | 币种与钱包关注、语言、时区、主题、默认交易所 | 是 | – |
| M12 | 开放 API 与 MCP | REST + WebSocket + MCP（Streamable HTTP）+ Python SDK | 60 次/分 | 600 次/分 + WebSocket |
| M13 | 研究与内容 | 方法页、证据库、每周复盘、变更日志、状态页 | 全部 | – |
| M14 | 管理后台 | 采集健康、源开关、告警投递、用户与订阅、特性开关 | 内部 | – |

---

## 2. 页面与显示定义

站点路由（Next.js App Router，全部 SSR + 客户端增量更新）。每个页面定义：目的、布局、组件、交互、状态、刷新、移动端。

### 2.1 全局

- **顶栏**：Logo（棱镜三角渐变）、全局搜索（币 / 钱包地址 / 页面，快捷键 `/`）、导航（棱镜、筛选器、费率、爆仓、大户、期权、告警、API）、语言（EN/中文，URL 前缀 `/zh`）、主题（暗/亮，默认暗）、登录/头像。
- **宏观条**（顶栏下方，可折叠）：BTC 价格与 24h、总 OI 与 24h 变化、24h 爆仓总额、F&G、稳定币 7 日变化、ETF 昨日净流、BTC 主导率。每项点击进对应页。
- **状态指示**：右上角小圆点显示 WebSocket 连接状态；断线显示黄色并自动重连。
- **页脚**：免责声明、数据源与延迟说明、状态页链接、GitHub、更新日志、社群入口。
- **全局约定**：数字等宽字体；多头绿、空头红、中性灰；所有时间显示用户时区并 hover 显示 UTC；每张卡右上角"i"图标弹出定义、数据源、局限、更新时间；每个解读句子后跟证据标签 `已验证 / 站内自验证 / 启发式 / 观点`。
- **状态处理**：加载骨架屏；空数据显示原因（如"该币无期权"）；数据源故障显示灰色卡 + 最后一次成功时间，不显示零。
- **性能预算**：首屏 LCP ≤ 2.0 秒（4G）；JS 首包 ≤ 180 KB gzip；图表按需加载。
- **无障碍**：键盘可达、对比度 AA、图表提供表格视图切换。

### 2.2 首页 `/`

目的：30 秒内让用户看到"今天市场里谁和谁在打架"。

1. **洞察带**（6 张卡，自动生成，每 60 秒刷新）：散户/大户分歧最大、最拥挤、大户净方向、费率极值、过去 1 小时最大爆仓、过去 24 小时最大大户事件。每卡：标题、大字、一行说明、证据标签、点击进详情。
2. **棱镜速览**：默认 BTC，Tab 切换关注列表中的币；六行光谱条 + 拥挤度仪表（同 v0.1，加 hover 显示各所数值）。
3. **大户事件流**（右侧栏，实时 WebSocket）：最近 20 条开/平/翻转，含名义、价格、钱包，1 小时后回填涨跌。
4. **爆仓脉冲**：最近 1 小时按分钟的多空爆仓柱状（实时）。
5. **今日光谱**：一张全宽的"棱镜图"：横轴币种（按 OI 排序前 30），每币一条竖向光谱，颜色映射拥挤度，亮度映射 OI，是站点的标志性视觉，可分享为图片。
6. **社群与内容入口**：最新一篇复盘、加入按钮。

### 2.3 币种棱镜 `/coin/[symbol]`

- **头部**：币名、Logo、聚合价、24h 涨跌、总 OI、24h 成交、六所可用性图标、关注按钮、分享按钮（生成带当前光谱的 OG 图）。
- **左列（光谱）**：七行：散户多头、大账户多头、主动买入、大户多头（HL）、费率 8h、OI 相对 30 日分位、24h 爆仓多空比。每行 hover 展示各所明细与 7 日迷你图；点击展开为全宽时序图。
- **拥挤度仪表** + **分歧陈述**（事实句 + 证据标签）。
- **中列（时序图）**：lightweight-charts，主图价格；叠加层可切换：OI、费率、账户多空、大户净仓位、爆仓柱；周期 1m/5m/15m/1h/4h/1d；范围 1d 到 1y。
- **右列**：分交易所表（价格、费率原始/8h/年化、下次结算倒计时、OI、成交、多空比）；大户在该币的仓位表；期权摘要（若有）；基差摘要（若有）。
- **底部**：OI 象限历史统计卡（价涨 OI 涨/跌 ×4，各自后 24 小时平均收益，标 `站内自验证`）；相关新闻/公告（Binance 公告过滤该币）。
- 刷新：价格与费率 WebSocket；多空比每 5 分钟；大户仓位 60 秒。

### 2.4 筛选器 `/screener`

- 表格 300+ 币，虚拟滚动。默认列：币、价格、24h、OI、OI 24h 变化、费率 8h、年化、跨所价差、散户多头、大账户多头、大户多头、拥挤度、24h 爆仓、成交。可加列至 30 个（期权 IV、基差、RSI 1h/4h/1d、OI/市值等）。
- 多条件筛选器（如 费率 < 0 且 OI 24h 变化 > 10%）、排序、列配置、保存筛选（登录）、导出 CSV（Pro）。
- 行 hover 显示迷你光谱；点击进棱镜页。
- 预设：负费率、拥挤多头、拥挤空头、OI 激增、大户与散户相反。
- 刷新每 60 秒，变化的单元格闪烁。

### 2.5 资金费率中心 `/funding`

- 顶部三卡：最高正、最低负、跨所价差最大（附扣费后套利年化）。
- 主表：币 × 交易所矩阵，单元格显示 8h 口径，颜色热力；切换显示原始/8h/年化/预测（Binance/Bybit/OKX 提供的下一期预测）。
- 倒计时列：各所下次结算时间。
- 历史页 `/funding/[symbol]`：六所费率折线 + OI 叠加，30 天 / 90 天 / 1 年；累计费率收益计算器（持仓方向、金额、期间）。
- 套利面板：费率套利（跨所对冲）与期现套利（永续 vs 季度）的毛收益与扣费净收益，标 `启发式`，说明执行风险。

### 2.6 爆仓中心 `/liquidations`

- **实时流**：Binance `!forceOrder@arr`、Bybit `allLiquidation`、OKX、Gate WebSocket 汇合，每条：时间、所、币、方向、价格、金额；≥ 10 万美元高亮；≥ 100 万美元触发全站 toast。
- **24h 聚合**：按币、按所、按方向的柱状；1h/4h/24h 切换。
- **级联检测**：5 分钟内同向爆仓 ≥ 2000 万美元且价格移动 ≥ 1% 标记为"级联事件"，写入事件表并可告警。
- **热力图**（模型）：`/liquidations/heatmap/[symbol]`，ECharts 热力图：横轴 7/30 天，纵轴价格；用六所 OI 变化 + 杠杆分布假设（可调滑块：10x/25x/50x/100x 权重）估算未平仓杠杆的清算价密度；当前价横线；标 `模型估算，非真实清算`；旁边并排真实爆仓分布做对照。免费用户 BTC/ETH，Pro 全币。
- **历史**：单币爆仓历史 1 年，与价格叠加。

### 2.7 大户透视 `/whales`

- **总览**：追踪规模（钱包数、总账户价值、总名义仓位）、大户净方向、按币多空堆叠条（前 20）、24h 大户资金流向（各币净开仓）。
- **钱包排行**：按账户价值 / 30 日 PnL / 30 日 ROI / 胜率（站内计算）排序；列：钱包、标签（显示名、已知实体）、账户价值、仓位数、净方向、1d/7d/30d PnL、30d 最大回撤（站内）、跟随人数（关注数）。
- **最大持仓**：全站前 100 单笔仓位，含距强平价百分比，颜色警示 < 5%。
- **事件带** `/whales/events`：实时 WebSocket；类型：开仓、加仓、减仓、平仓、翻转、接近强平（距强平 < 3%）、被强平；每条附 1h/4h/24h 后价格回填；筛选币/类型/最小名义/钱包。
- **记分板** `/whales/scorecard`：按事件类型 × 方向 × 币种分组的样本数、后续平均收益、顺向命中率、置信区间；滚动 30/90/365 天；方法说明；标 `站内自验证`。不预设结论。
- **钱包主页** `/whale/[address]`：账户价值曲线（HL portfolio 接口）、当前仓位、历史成交（fills）、按币盈亏、交易风格标签（持仓时长、杠杆偏好、方向偏好，站内算法）、事件时间线、关注按钮、告警按钮。
- **关注列表**：登录后可关注钱包，首页与事件带可只看关注。
- 候选池：排行榜前 1000 + 用户关注 + 事件流里出现的大额地址；每 60 秒轮询前 200 个活跃钱包，其余每 10 分钟。

### 2.8 期权 `/options`

Deribit BTC/ETH：DVOL 当前与 90 天；IV−RV 价差；PCR（OI 与成交）；各到期日最大痛点表；期限结构折线；25d skew 折线；OI 按行权价柱状（最近三个到期）；到期日历（含到期名义规模）。

### 2.9 基差 `/basis`

Binance 季度、Bybit 交割、OKX 币本位交割、Deribit 期货：年化基差期限结构；与永续费率年化对照；历史 1 年。

### 2.10 告警中心 `/alerts`

- 规则构建器：对象（币 / 钱包 / 全市场）× 指标（费率、费率跨所价差、OI 变化 %、爆仓 1h 总额、级联事件、大户开/平/翻转、距强平、分歧阈值、拥挤度、价格）× 条件 × 冷却时间 × 渠道（Telegram / Email / Web Push / Webhook）。
- 模板一键创建：负费率提醒、大户翻转、爆仓激增、关注钱包动作。
- 投递记录：时间、规则、渠道、状态、内容预览；失败重试 3 次。
- Telegram Bot：`/start` 绑定、`/status`、`/prism BTC` 直接回图、`/whale 0x…`。
- 每日摘要（免费）：UTC 00:00 一张棱镜图 + 五条要点。

### 2.11 账号与设置 `/settings`

登录方式、语言、时区、主题、默认交易所、关注列表管理、API key 管理（创建/吊销/用量）、订阅与发票（Stripe Portal）、数据导出、删除账号。

### 2.12 API 与 MCP `/api-docs`

OpenAPI 交互文档、示例、SDK 安装、MCP 接入说明（Claude Desktop / Cursor 配置片段）、速率与状态、变更日志。

### 2.13 研究与内容 `/research`、`/method`、`/changelog`、`/status`

方法页：每个指标定义、公式、来源、局限；证据库：每条"已验证"标签对应的数据量、期间、方法、效应大小，含你本地研究的摘要（明确标注样本期与"未在本站复现"）；每周复盘文章；状态页：各源延迟与 30 天可用率。

### 2.14 管理后台 `/admin`（仅管理员）

采集器健康（每源最后成功、错误率、延迟）、源开关与限速调整、告警投递统计、用户与订阅、特性开关、手动回填任务、审计日志。

---

## 3. 后端定义

### 3.1 服务拆分（同一台机器上的容器）

| 服务 | 职责 | 技术 |
|---|---|---|
| `collector-rest` | 每 5 秒到 10 分钟不等的 REST 轮询：行情、费率、OI、多空比、期权、基差、宏观、HL 上下文与钱包状态 | Python asyncio + httpx，按源限速与退避，任务表驱动 |
| `collector-ws` | 常驻 WebSocket：Binance/Bybit/OKX/Gate 爆仓流、Binance/HL 标记价与成交 | Python asyncio + websockets，自动重连、心跳、序号校验 |
| `whale-engine` | 钱包候选池维护、fills 增量拉取、事件识别、回填收益、记分板计算 | Python |
| `analytics` | 连续聚合、拥挤度、分歧、OI 象限、清算热力图模型、级联检测、风格标签 | Python + SQL（Timescale 连续聚合） |
| `alert-engine` | 规则评估（Redis 流触发 + 定时兜底）、去重冷却、投递、重试 | Python |
| `api` | REST + WebSocket + MCP + OpenAPI | FastAPI + uvicorn |
| `web` | Next.js SSR | Node 20 |
| `postgres` `redis` `prometheus` `grafana` `caddy/cloudflared` | 基础设施 | 官方镜像 |

### 3.2 数据模型（主表）

时序表（TimescaleDB hypertable，压缩 7 天后）：
- `ticks(ts, venue, symbol, mark, index, last, bid, ask)` 5 秒
- `funding(ts, venue, symbol, rate, interval_h, rate_8h, next_ts, predicted)` 每分钟
- `open_interest(ts, venue, symbol, oi_base, oi_usd)` 每分钟
- `ls_ratio(ts, venue, symbol, kind ∈ {account, top_account, top_position, taker}, long_share)` 5 分钟
- `liquidations(ts, venue, symbol, side, price, qty, usd, event_id)` 实时
- `options_snapshot(ts, currency, expiry, strike, type, oi, iv, mark, volume)` 5 分钟
- `basis(ts, venue, symbol, expiry, annualized)` 每分钟
- `macro(ts, key, value)` 每日/小时
- `hl_asset_ctx(ts, coin, mark, funding_1h, oi, premium, vlm)` 每分钟
- `hl_wallet_state(ts, address, account_value, margin_used, withdrawable)` 60 秒（活跃）
- `hl_positions(ts, address, coin, side, size, notional, entry, liq, lev, upnl)` 60 秒
- `hl_fills(fill_id PK, address, ts, coin, side, px, sz, fee, closed_pnl, dir, liquidation)` 增量
- `whale_events(id, ts, address, coin, type, side, notional_delta, px, ret_1h, ret_4h, ret_24h, filled_at)`
- `liq_cascades(id, start_ts, end_ts, symbol, side, usd, price_move_pct)`

聚合与派生：`coin_prism_1m`（每币每分钟一行：聚合价、funding_8h、oi_total、retail、top、taker、whale_share、crowding）、`funding_1h`、`oi_1h`、`liq_1h`、`scorecard_daily`。

业务表：`users`、`sessions`、`api_keys`、`watchlists`、`alert_rules`、`alert_deliveries`、`subscriptions`、`wallet_labels`、`feature_flags`、`audit_log`、`source_health`。

### 3.3 REST API（前缀 `/v1`，JSON，ETag 与 Cache-Control，CORS 开放 GET）

| 端点 | 说明 |
|---|---|
| `GET /coins` | 币列表 + 最新棱镜摘要（筛选器数据），支持 `sort`、`filter`、`cols` |
| `GET /coins/{symbol}/prism` | 单币完整棱镜 + 分所明细 + 分歧陈述 |
| `GET /coins/{symbol}/history?field=&interval=&from=&to=` | 时序 |
| `GET /funding` `GET /funding/{symbol}/history` `GET /funding/arb` | 费率 |
| `GET /oi` `GET /oi/{symbol}/history` `GET /ls/{symbol}` | 持仓与多空 |
| `GET /liquidations/recent` `GET /liquidations/agg` `GET /liquidations/heatmap/{symbol}` `GET /liquidations/cascades` | 爆仓 |
| `GET /whales` `GET /whales/{address}` `GET /whales/{address}/fills` `GET /whales/positions/top` `GET /whales/events` `GET /whales/scorecard` `GET /whales/by-coin` | 大户 |
| `GET /options/{currency}/summary` `GET /options/{currency}/chain` | 期权 |
| `GET /basis` | 基差 |
| `GET /macro` | 宏观 |
| `GET /insights` | 首页洞察卡 |
| `GET /status` | 各源健康 |
| `POST/GET/DELETE /me/watchlist` `…/alerts` `…/api-keys` | 用户（需登录） |
| `GET /openapi.json` | 文档 |

WebSocket `/v1/stream`：订阅 `prism:{symbol}`、`liq:*`、`liq:{symbol}`、`whale:events`、`whale:{address}`、`funding:{symbol}`；消息带序号与服务器时间。

MCP（`/mcp`，Streamable HTTP，可选 API key）：工具 `list_coins`、`get_prism`、`get_history`、`get_funding`、`get_liquidations`、`get_whales`、`get_whale`、`get_whale_events`、`get_scorecard`、`get_options`、`get_basis`、`get_macro`、`explain_coin`（生成带证据标签的中英文解读）、`create_alert`（需登录）。

### 3.4 限流与配额

匿名 30 次/分（按 IP）；免费用户 60 次/分；Pro 600 次/分 + WebSocket 10 个订阅；超限 429 带 `Retry-After`。缓存：Cloudflare 缓存公共 GET 5 到 60 秒；Redis 缓存热点查询。

### 3.5 采集策略与限速

| 源 | 频率 | 限速预算 |
|---|---|---|
| Binance REST | 行情 5 秒、费率/OI 1 分钟、多空比 5 分钟、K 线补齐 | 2400 权重/分，用 ≤ 40% |
| Binance WS | 标记价流、`!forceOrder@arr` | 1 连接 |
| Bybit REST/WS | 同上 | 120 次/分 |
| OKX REST/WS | 同上；`rubik` 统计 5 分钟 | 20 次/2 秒 |
| Gate REST/WS | `contract_stats` 1 分钟（含真实爆仓金额），爆仓流 | 200 次/10 秒 |
| Bitget REST | 行情 1 分钟、比率 5 分钟 | 20 次/秒 |
| Hyperliquid | `metaAndAssetCtxs` 10 秒；活跃钱包 `clearinghouseState` 60 秒；fills 增量 60 秒；排行榜每小时 | 1200 权重/分，用 ≤ 50% |
| Deribit | 期权链 5 分钟、DVOL 1 分钟 | 20 次/秒 |
| 宏观 | F&G、DefiLlama 每小时；ETF 流每日；CoinGecko 每 10 分钟 | 低 |

每个源有熔断：连续 5 次失败停 5 分钟；`source_health` 记录；状态页与后台可见；任何源故障不影响其他源和页面。

---

## 4. 前端定义

- **技术**：Next.js 15、TypeScript 严格模式、Tailwind、shadcn/ui 组件基座、TanStack Query（缓存与轮询）、Zustand（WebSocket 状态）、ECharts（热力图、光谱图）、lightweight-charts（K 线时序）、next-intl（i18n）、next-pwa（离线壳与 Web Push）。
- **渲染**：页面 SSR 取首屏数据（缓存 10 秒），客户端接管后 WebSocket 或轮询更新；分享链接生成 OG 图（`/og/coin/BTC.png`，Satori）。
- **设计系统**：暗色主 + 亮色；等宽数字；多空色；棱镜渐变仅用于品牌与拥挤度轨道；8 px 栅格；三种断点（≥1280 三列、768–1279 两列、<768 单列）。
- **组件库（核心）**：`SpectrumRow`（中心对齐条）、`CrowdingGauge`、`PrismChart`（全市场光谱图）、`VenueTable`、`TimeSeriesPanel`（叠加层选择）、`HeatmapPanel`、`EventFeed`（虚拟列表 + 实时插入动画）、`WalletCard`、`ScorecardTable`、`AlertRuleBuilder`、`EvidenceTag`、`InfoPopover`、`SourceHealthDot`、`Skeleton`、`EmptyState`。
- **状态规范**：每个数据面板四态：加载（骨架）、正常、部分降级（灰色 + 最后成功时间）、错误（重试按钮）。
- **SEO**：每个币、每个钱包一个可索引页面，结构化数据，中英双语 hreflang，sitemap 自动生成。
- **分析**：Cloudflare Web Analytics（无 cookie）+ 自建事件（页面、筛选保存、告警创建、加入社群点击、返佣点击）。
- **移动端**：底部导航（首页、筛选器、爆仓、大户、告警）；表格横向滚动并固定首列；图表手势缩放；PWA 可安装。

---

## 5. 告警规则引擎

规则 = `{ scope: coin|wallet|market, metric, op, value, window, cooldown, channels[] }`。评估：`collector` 写入后发布 Redis 流 `metric:{scope}:{id}`；`alert-engine` 消费并匹配规则；命中写 `alert_deliveries` 并投递；同规则冷却期内不重复；投递失败指数退避重试 3 次；每用户每小时上限（免费 20，Pro 500）。消息模板中英文，附深链接与一张迷你光谱图。

---

## 6. 安全与合规

Cloudflare Tunnel（VPS 无公网端口）、WAF 与速率限制；HTTPS 全站；魔法链接 15 分钟有效；会话 Cookie `HttpOnly Secure SameSite=Lax`；API key 只显示一次，哈希存储；CSRF 令牌；输入校验（pydantic）；SQL 参数化；依赖扫描（Dependabot）；秘钥在 `.env` 且不入库；审计日志；用户可导出与删除数据（GDPR）；隐私政策与条款页；全站免责声明；不提供任何交易执行、不托管资金、不做收益承诺；返佣链接明确标注。

---

## 7. 运维

- **CI**：PR 触发 lint、类型检查、单元测试（采集解析器用录制的响应做快照测试）、构建镜像；main 合并后自动部署 staging（同机 compose profile）；手动批准上生产。
- **部署**：`docker compose pull && up -d`，零停机靠健康检查与 Caddy 优雅切换；数据库迁移用 Alembic。
- **监控指标**：每源延迟与错误率、队列积压、WebSocket 连接数、API p95 延迟、告警投递成功率、磁盘与内存；告警到你的 Telegram。
- **备份**：每日全量 + WAL 归档到 R2；每月恢复演练。
- **容量**：日活 5000、300 币、1000 钱包时，写入约 200 行/秒，磁盘年增约 60 GB（压缩后），单机足够；超出时把 `web` 迁到 Cloudflare Pages、数据库迁到托管。

---

## 8. 变现（覆盖成本优先）

| 路径 | 说明 |
|---|---|
| 返佣 | Hyperliquid 邀请码、Bybit/OKX/Gate 联盟链接，放在页脚、棱镜页交易所表、告警消息底部；明确标注 |
| Pro 订阅 | 9 美元/月或 79 美元/年：实时爆仓与全币热力图、50 条实时告警、Webhook、API 600 次/分、筛选器保存与导出、关注 50 个钱包 |
| API 团队版 | 49 美元/月：6000 次/分、WebSocket 50 订阅、商用许可 |
| 赞助 | GitHub Sponsors、Ko-fi |

不做：付费群、课程、跟单、信号。

---

## 9. 路线图（生产级）

| 阶段 | 周 | 交付 | 验收 |
|---|---|---|---|
| P0 基础设施 | 1–2 | VPS、Compose、Postgres/Timescale/Redis、Tunnel、CI/CD、监控、备份；`collector-rest` 六所行情/费率/OI/多空比；`api` 基础端点；Next.js 骨架迁移现有页面 | 数据 24h 不断；状态页上线 |
| P1 核心页面 | 3–4 | 棱镜页（时序图）、筛选器、费率中心、持仓与多空、宏观条、i18n、SEO | Lighthouse ≥ 90 |
| P2 实时与爆仓 | 5–6 | `collector-ws` 四所爆仓流、级联检测、爆仓中心、热力图模型、WebSocket 推送 | 端到端延迟 ≤ 5 秒 |
| P3 大户 | 7–9 | `whale-engine`、钱包主页、事件带、记分板、关注列表、账号系统 | 记分板样本 ≥ 500 |
| P4 告警与 API | 10–11 | 规则引擎、Telegram/Email/Push/Webhook、API key、限流、OpenAPI、MCP、Python SDK | 告警端到端 ≤ 10 秒 |
| P5 期权基差与商业化 | 12–13 | 期权页、基差页、Stripe、Pro 权益、返佣位、研究页 | 首笔收入 |
| P6 打磨发布 | 14 | 性能、无障碍、移动端、内容、发布文章、社群 | 公开发布 |

每阶段末：一次真实用户测试（5 人）、一次安全自检、更新变更日志。

---

## 10. 与 v0.1 的关系

v0.1（静态站）继续作为免费快照层保留并每 30 分钟更新，直到 P1 上线后切换域名；`data/latest.json` 的字段设计延续到 `/v1/coins/{symbol}/prism`，保持兼容。

---

## 11. 待你确认（不阻塞 P0）

1. VPS 供应商与地区（建议 Hetzner 新加坡或 Vultr 东京；若你已有服务器，给我系统与配置）。
2. 域名（建议 `hlens.xyz` 或 `hlens.app`）。
3. Telegram Bot 与社群的名字。
4. 是否接 Stripe（需要你的账号），还是先只做返佣。
