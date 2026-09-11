# hlens · CryptoPlus 产品方案（v0.1 → v1.0）

> 状态：草案 v1，2026-09-11。作者：Claude，按 raphael 的要求独立决策。
> 一句话定位：**合约持仓棱镜**。把一个币的永续合约市场拆成"散户 / 大账户 / 链上大户 / 资金费 / 持仓量 / 爆仓 / 期权"七个光谱面，并排展示，让分歧本身成为信号。
> 硬约束：零服务器（GitHub Pages + GitHub Actions），最多加一个 Cloudflare Worker；所有数据来自免 key 公开接口；面向全球，中英双语；未来同一份数据要能给 API 和 MCP 用。

---

## 0. 结论先行

| 问题 | 决定 |
|---|---|
| 和 Coinglass 拼什么 | 不拼数据广度。拼三件事：**分解视角**（一币多方并排）、**证据标签**（每句解读标注"已验证 / 启发式 / 观点"）、**机器可读**（页面即 JSON，JSON 即 API，API 即 MCP）。 |
| 直接照抄 Coinglass 什么 | 资金费率表、多空比、OI 历史、RSI 热力图、清算地图、期权最大痛点、ETF 流入。这些用公开接口都能做，而且 Coinglass 把其中清算热力图和 API 锁在付费墙后。 |
| 不做什么 | 喊单、跟单执行、账户托管、需要用户 API key 的任何功能、tick 级订单簿、需要付费数据源的链上净流。 |
| 变现 | 不卖信号。三条低成本路径：交易所返佣链接（Hyperliquid / Bybit / OKX）、GitHub Sponsors、以及后期的告警订阅（Telegram 推送，Cloudflare Worker 成本约 5 美元/月）。目标是覆盖服务器费用，不是做生意。 |
| 关于本地研究结论 | 全部作为"证据，附带条件"呈现，不当结论宣讲。用户明确说过这些不一定对。 |

---

## 1. 差异化：Coinglass 有什么、缺什么、我们做什么

### 1.1 Coinglass 现状（子代理调研，2026-09）

- 免费：资金费率、OI、多空比、清算地图（基础）、ETF 流、RSI 热力图、牛市顶部信号、Hyperliquid 鲸鱼预警（基础）。
- 付费墙：清算热力图全币种 + 自动刷新（Prime，28 美元/月）；API 无免费层（29 到 699 美元/月）；期权深度数据走付费 API。
- 已知短板：清算热力图是模型估算而非真实清算；告警只覆盖少数币种；App 与网页收藏不同步；CDRI 风险指数算法不透明。

### 1.2 hlens 的差异点（按重要性排序）

| # | 差异点 | Coinglass | hlens | 数据来源（已验证 HTTP 200） |
|---|---|---|---|---|
| 1 | **一币棱镜**：散户 / 大账户 / 主动买卖 / 大户 / 费率 / OI 六行并排，中心对齐条形 | 分散在不同页面 | 首屏核心 | Binance（镜像域名）、OKX、Gate、Bitget、Bybit（可用则用）、Hyperliquid |
| 2 | **散户 vs 大户分歧**作为首页头条 | 无 | 洞察条第一张卡 | 同上 |
| 3 | **大户事件带 + 公开计分板**：追踪钱包的开仓 / 平仓 / 翻转事件，记录 1h / 4h / 24h 后价格走向，滚动展示"大户开仓后价格顺向 / 逆向"的命中率 | 只有实时预警，不回看 | v0.3 | Hyperliquid `userFills`（每 30 分钟拉 60 个钱包，成本极低） |
| 4 | **证据标签**：每一句解读旁边有标签：`已验证`（附数据规模、检验方法、时间范围）/ `启发式` / `观点` | 无 | 全站 | 本地研究文档 + 站内自验证 |
| 5 | **真实清算 vs 模型清算并列**：OKX + Gate.io 真实强平记录，与自建的 OI 杠杆分布模型热力图并排，明确标注哪个是真的 | 只有模型，且付费 | v0.3 | OKX `liquidation-orders`、Gate `liq_orders` |
| 6 | **拥挤度公式公开**、可复算 | CDRI 黑箱 | 方法页写明公式 | 本站计算 |
| 7 | **散户心理面板**：恐惧贪婪 + 各区间典型行为，明确标注"视角而非事实" | 只有指数 | v0.1 已上 | alternative.me |
| 8 | **JSON 即 API，免费，无 key**；后续 MCP server | API 付费 | v0.2 定版 schema | 静态文件 |
| 9 | **无广告、单页、手机可用、双语** | 广告多、多页 | 是 | – |
| 10 | 期权：Deribit 最大痛点 / PCR / DVOL / 25d skew 免费展示 | API 付费 | v0.4 | Deribit public |
| 11 | 基差期限结构（Binance 季度、Bybit 交割、Deribit 期货） | 有但分散 | v0.4 | 已验证 |

---

## 2. 架构

```
GitHub Actions (cron */30)                         浏览器
┌──────────────────────────────┐                  ┌──────────────────────────┐
│ 1. curl 线上 data/history.json│                  │ index.html (静态)        │
│ 2. python scripts/fetch.py    │  upload-pages    │  ├ fetch data/latest.json│
│    ├ Binance(镜像)/OKX/Gate/Bitget/Bybit │ ───────────────▶ │  ├ fetch data/history.json│
│    ├ Hyperliquid ctx+leaderboard│  deploy-pages  │  └ 纯前端渲染，i18n      │
│    ├ alternative.me F&G        │                  └──────────────────────────┘
│    └ 写 data/*.json            │
│ 3. 整个目录发布为 Pages 工件    │      可选（v0.3+）
└──────────────────────────────┘      ┌──────────────────────────────────┐
                                      │ Cloudflare Worker + KV            │
仓库里永远不提交 data/，历史只存在      │  a) Binance 反代（若 Actions 美区 451）│
于线上工件，避免每半小时一次 commit     │  b) forceOrder WS 60s 采样         │
                                      │  c) 告警：阈值→Telegram Bot        │
                                      │  d) /api/* 带 CORS 与缓存头         │
                                      └──────────────────────────────────┘
```

**已知风险与对策（2026-09-11 已在 Actions 内实测）**

| 风险 | 实测事实 | 对策（已实施 / 计划） |
|---|---|---|
| Binance 合约接口对美国 IP 返回 451 | `fapi.binance.com` 从 GitHub runner 得到 451；`www.binance.com/fapi/*` 与 `www.binance.com/futures/data/*` 同样路径全部 200 且返回真实数据 | **已实施**：脚本优先走 `www.binance.com` 镜像，失败再回退 `fapi`。 |
| Bybit 对美国 IP 返回 403 | `api.bybit.com`、`api.bytick.com`、`.nl`、`.kz`、`-tr`、`bybitglobal` 全部 403；Cloudflare Worker 反代的出口位置跟随调用方所在 colo，从美国调用大概率同样 403 | **已实施**：Bybit 保留为"可用则用"，失败时 `sources.bybit` 显示灰色说明；**新增 Gate 与 Bitget** 补足（两者从美国 200）。若日后用自有服务器做 self-hosted runner，Bybit 自动恢复。 |
| Gate / Bitget / OKX / Deribit / Hyperliquid 从美国可达 | 全部 200 | 作为主数据源。Gate `contract_stats` 一次给出账户多空比、大账户多空比、主动买卖比、美元 OI 与真实爆仓金额，性价比最高。 |
| Hyperliquid 排行榜 JSON 约 37 MB | 每 30 分钟拉一次，下载 + 解析约 5 秒 | 可接受。v0.3 改为整点全量、其余复用候选名单。 |
| Actions 用量 | 并发后单次约 40 到 70 秒；公开仓库的托管 runner 不计费 | 仓库保持 public。 |
| 排行榜账户价值是快照 | 已观察到 6000 万 vs 8669 美元的差异 | 以 `clearinghouseState` 实时值为准，排行榜只用于候选。 |
| 公开接口变更 | 不可控 | 每个源独立 try，`sources` 字段暴露状态，前端显示红绿灯；`.github/workflows/diag.yml` 可手动触发探测各域名。 |

---

## 3. 数据契约（这就是未来的 API）

所有文件放在 `data/`，静态托管自带 CORS（GitHub Pages 允许跨域 GET）。字段一经 v0.2 定版即冻结，新增只加不删，破坏性变更走 `data/v2/`。

### 3.1 `data/latest.json`

```jsonc
{
  "schema": 1,
  "generated_at": 1789139919832,        // ms
  "generated_iso": "2026-09-11T15:23:27+00:00",
  "sources": { "binance": "ok", "bybit": "ok", "okx": "ok", "hyperliquid": "ok", "hl_leaderboard": "ok", "fear_greed": "ok" },
  "coins": [ {
    "coin": "BTC",
    "price": 78662.7, "chg24h_pct": 1.94,
    "funding_8h": 0.0000637,            // 四所 OI 加权，统一 8h 口径
    "funding_annualized_pct": 6.97,
    "oi_total_usd": 1.757e10, "vol24h_total_usd": 3.11e10,
    "retail_long_share": 0.583,          // Binance 全局账户多空 + Bybit 账户比 + OKX 账户比 的均值
    "top_trader_long_share": 0.678,      // Binance 大户持仓比
    "taker_buy_share": 0.486,            // Binance + OKX 主动买占比均值，1h
    "whale_long_share": 0.339, "whale_long_usd": 2.06e8, "whale_short_usd": 4.01e8, "whale_n": 24,
    "crowding": 0.124,                   // 见 §5.3 公式
    "exchanges": [ { "exchange": "binance", "price": ..., "funding": ..., "funding_interval_h": 8, "funding_8h": ..., "oi_usd": ..., "vol24h_usd": ..., "retail_long_share": ..., "top_trader_long_share": ..., "taker_buy_share": ... }, ... ]
  } ],
  "macro": { "fng": { "value": 56, "label": "Greed", "ts": ..., "history": [68, 69, ...] } },
  "whales": {
    "count": 53, "candidates": 143,
    "wallets": [ { "address": "0x…", "name": null, "account_value": 1.03e8, "pnl_day": ..., "pnl_week": ..., "pnl_month": ..., "roi_month": ..., "pnl_all": ..., "n_pos": 7, "long_notional": ..., "short_notional": ..., "positions": [ { "coin": "ETH", "side": "short", "size": 111000, "notional": 2.53e8, "entry": 2270.5, "liq": 3542.1, "lev": 5, "lev_type": "cross", "upnl": -3.33e7, "roe": -0.61 } ] } ],
    "by_coin": [ { "coin": "ETH", "long_usd": ..., "short_usd": ..., "n_long": 9, "n_short": 12, "total_usd": ..., "long_share": 0.48 } ],
    "top_positions": [ { ...position, "address": "0x…", "name": null, "account_value": ... } ]
  }
}
```

### 3.2 `data/history.json`

数组，每 30 分钟一个点，最多 336 个（7 天）：`{ "t": ms, "c": { "BTC": [price, funding_8h, oi_usd, retail_long_share, whale_long_share, crowding], ... } }`。v0.3 起另存 `data/history/{coin}.json` 保留 90 天。

### 3.3 v0.2 起新增文件

| 文件 | 内容 | 刷新 |
|---|---|---|
| `data/funding.json` | 全币种（Binance/Bybit/OKX/HL 交集，约 150 币）当前费率 + 8h 口径 + 跨所最大价差，按年化排序 | 30 分 |
| `data/funding_history/{coin}.json` | 30 天费率历史，四所 | 30 分 |
| `data/oi_history/{coin}.json` | 30 天 OI 历史（Binance 1h、Bybit、OKX 按币聚合） | 30 分 |
| `data/whale_events.json` | 大户开 / 平 / 翻转事件流（§4.4） | 30 分 |
| `data/whale_scorecard.json` | 事件后 1h / 4h / 24h 收益统计（§4.4） | 30 分 |
| `data/liquidations.json` | OKX + Gate 真实强平（近 24h，按币、按方向聚合 + 最近 200 条） | 30 分 |
| `data/liq_map/{coin}.json` | 模型清算热力图（§4.5） | 30 分 |
| `data/options.json` | Deribit BTC/ETH：DVOL、PCR、最大痛点、期限结构、25d skew | 30 分 |
| `data/basis.json` | 季度 / 交割合约年化基差期限结构 | 30 分 |
| `data/rsi.json` | 全币种 1h / 4h / 1d RSI | 30 分 |
| `data/macro.json` | F&G、稳定币供应（DefiLlama）、ETF 流（SoSoValue，非官方，带兜底）、Polymarket 加密相关市场 | 30 分 / 日 |
| `data/index.json` | 文件清单 + schema 版本 + 每个文件的 generated_at | 每次 |

### 3.4 MCP server（v0.5）

一个 Python 包 `hlens-mcp`，`pip install` 后本地运行，只读线上 JSON，不需要 key。工具列表：

| 工具 | 参数 | 返回 |
|---|---|---|
| `get_prism` | coin | `coins[]` 中对应对象 |
| `list_coins` | – | 币种 + 24h 涨跌 + 拥挤度 |
| `get_whales` | coin?, min_notional? | 钱包 / 仓位过滤结果 |
| `get_whale_events` | coin?, since? | 事件流 |
| `get_funding` | sort?, top? | 费率表 |
| `get_liquidations` | coin?, window? | 真实强平聚合 |
| `get_options` | currency | Deribit 摘要 |
| `get_history` | coin, field, days | 序列 |
| `explain_signal` | coin | 把棱镜六行翻成一段英文 / 中文自然语言，附证据标签 |

同一份 JSON 也可直接被 Claude / GPT 的 `fetch` 工具读取，README 会给示例 prompt。

---

## 4. 页面规格（细颗粒度）

站点是单页 + 锚点，v0.4 起拆为多路由（`/`、`/funding`、`/liquidations`、`/options`、`/whales`、`/api`、`/method`），用纯前端 hash 路由，仍然静态。

### 4.1 顶栏（已实现）

- 左：棱镜三角 Logo（渐变描边）+ `hlens` + 副标 "the perp positioning prism / 合约持仓棱镜"。
- 右：锚点导航（Prism / Whale lens / Method / GitHub）、语言切换按钮（记忆到 localStorage，默认按浏览器语言）、主按钮 **Join the community / 加入社群**（渐变底、深色字）。
- 移动端：隐藏副标和文字导航，只留语言和主按钮。

### 4.2 首屏 Hero + 状态条（已实现）

- 标题 "One coin. Every angle. / 一个币，所有角度。"
- 状态条：`updated N min ago`、每个数据源一个红绿灯 pill（hover 显示错误文本）、`53 whales tracked`。

### 4.3 洞察条（已实现，v0.2 扩展）

四张卡，每张 = 小标题 + 大字 + 一行说明，左侧渐变竖线：

| 卡 | 规则 | 说明文案 |
|---|---|---|
| 散户 / 大户分歧最大 | 在 `whale_n ≥ 2` 的币中取 `abs(retail − whale)` 最大 | "散户 73% 做多 vs 大户 2% 做多" |
| 最拥挤 | `abs(crowding)` 最大 | "拥挤度 +0.31" |
| 大户净方向 | 所有追踪仓位多 / 空名义合计 | "多 $1.16B · 空 $1.74B · 53 个钱包" |
| 资金费率最高 | `abs(funding_8h)` 最大 | "年化 9.0% · 持仓 $13.09B" |

v0.2 新增第五、六张：**过去 24h 最大大户事件**（来自事件流）、**真实强平 24h 最多的币**。

### 4.4 棱镜（已实现）

- 币种 Tab：BTC、ETH、SOL、XRP、DOGE、HYPE、BNB、SUI，每个带 24h 涨跌；v0.2 扩到按 OI 前 20 + 搜索框。
- 左卡（六行光谱，每行 = 标签 + 副标 + 中心对齐条 + 数值）：
  1. 散户多头占比（三所均值）
  2. 大账户多头占比（Binance 前 20%）
  3. 主动买入占比（1h）
  4. 大户多头占比（HL 名义加权，括号显示样本数）
  5. 资金费率 8h（零中心，±0.05% 满格）
  6. 持仓量（绝对条，以全币最大值为 100%）
  - 拥挤度仪表：渐变轨道 + 白针，−1 到 +1，文字标签 "空头拥挤 / 均衡 / 多头拥挤"（阈值 ±0.15）。
  - 分歧框：只陈述事实："散户 X% 做多，大户 Y% 做多，差距 G 点。资金费率为正/负，此刻是多头/空头在付费持仓。"
  - 三条 7 天迷你曲线：价格、8h 费率、拥挤度（历史不足 3 点时显示提示）。
- 右列：
  - **分交易所表**：交易所、价格、原始费率 + 结算周期、8h 口径、年化、OI、24h 成交、散户多头。脚注解释 HL 每小时结算的折算。
  - **散户心理卡**：恐惧贪婪大数字（≤45 红、≥55 绿、中间黄）+ 分类文字 + 渐变轨道 + 14 天迷你曲线 + 五个区间的典型行为列表（当前区间高亮）+ 标签"一种视角，不是事实"。

v0.2 加：每行右侧一个"?"提示，点开显示该行的定义、数据源、局限（例如"Bybit 账户比不区分持仓大小"）。

### 4.5 大户透视（已实现 + v0.3 扩展）

已实现：
- **大户的钱在哪些币上**：前 14 个币，堆叠条（绿多 / 红空），条长按总名义归一，右侧数值。
- **最大的单笔持仓**：前 20，列：币、方向、名义、开仓价、强平价、杠杆（isolated 标 iso）、浮盈亏、钱包（链接到 hypurrscan）。
- **追踪中的钱包**：前 40，列：#、钱包、账户价值、1d / 7d / 30d 盈亏、30d ROI、净方向、持仓 chips（最多 6 个 + "+N"）。

v0.3 新增：**大户事件带 + 计分板**（差异点 #3）
- 抓取：每 30 分钟对追踪钱包调 `userFills`（取上次之后的成交），聚合成事件：`open`（从 0 到有仓）、`add`、`reduce`、`close`（到 0）、`flip`（方向反转），记录币、方向、名义变化、成交均价、时间戳。
- 展示：时间倒序列表，每条 = 时间 · 钱包 · 动作 · 币 · 方向 · 名义 · 之后 1h / 4h / 24h 价格变动（用 HL `candleSnapshot` 回填，未到期显示 "…"）。
- 计分板：按动作 × 方向分组，显示样本数、事件后 1h / 4h / 24h 平均收益（顺方向为正）、顺向命中率。标签 `站内自验证 · 滚动 90 天`。
- 关键：**不预设结论**。本地研究显示"大户开仓后 1h 逆向、平仓后顺向"，这只作为方法页里的一段"曾在 1.27 亿条成交上观察到的现象，样本期 2025-2026，效应经济上极弱"，计分板用线上数据自己说话。

### 4.6 资金费率页（v0.2）

- 全币种表（约 150 币）：币、四所原始费率、8h 口径、年化、跨所价差（最高 − 最低，标出方向）、OI 合计、24h 涨跌；默认按 `abs(年化)` 排序；筛选：只看负费率、只看价差 > 0.01%。
- 顶部三张卡：正费率最高、负费率最低、跨所价差最大（潜在套利，附"扣除双边手续费后"提示）。
- 单币点开：30 天四所费率折线 + OI 折线（同一图，双轴）。
- 与 Coinglass 对比：一样的数据，多了 8h 统一口径、跨所价差和 JSON 直出。

### 4.7 清算页（v0.3）

- 左：**真实强平**（OKX + Gate，明确标注"仅两所，约占全市场 15-20%"）：近 24h 按币聚合的多单 / 空单强平金额柱状；最近 200 条明细（时间、所、币、方向、价格、金额）；1h 滚动最大单笔。
- 右：**模型热力图**（复刻 Coinglass Prime 付费功能）：对每个币，用四所 OI 历史 + 假设杠杆分布（10x / 25x / 50x / 100x 权重 0.4 / 0.3 / 0.2 / 0.1）估算价格轴上的清算密度，画成 7 天 × 价格网格热力图，当前价横线。标签 `模型估算，非真实清算`。方法页写明假设，允许用户在页面上改杠杆权重实时重算（纯前端）。
- v0.4 可选：Cloudflare Worker 每 30 分钟采样 Binance `!forceOrder@arr` 60 秒，把 Binance 真实强平样本补进左侧。

### 4.8 期权页（v0.4）

Deribit BTC / ETH：DVOL 当前值 + 30 天曲线、已实现波动率对比（IV − RV 价差）、PCR（按 OI 和按成交量）、最大痛点（按到期日）、期限结构（各到期日 ATM IV）、25d skew（自算，方法页写明）、OI 按行权价分布柱状（最近两个到期日）。全部免费，Coinglass 走付费 API。

### 4.9 基差页（v0.4）

Binance 季度（`BTCUSDT_YYMMDD`）、Bybit 交割、Deribit 期货：年化基差期限结构折线；与永续费率年化并排，显示"期现套利毛收益"。

### 4.10 RSI 热力图（v0.2，低成本高流量）

全币种 1h / 4h / 1d RSI，颜色格子，点击跳到该币棱镜。Coinglass 同款，社区有开源复刻，需求真实。K 线来自 Binance / Bybit 公开接口。

### 4.11 宏观条（v0.3）

页面顶部一条横向滚动 pill：BTC 主导率（CoinGecko，30 分钟一次不触限速）、稳定币总供应 7 天变化（DefiLlama）、BTC / ETH 现货 ETF 昨日净流（SoSoValue 非官方接口，失败则隐藏）、Polymarket 上与 BTC 价格相关的前 3 个市场概率、F&G。

### 4.12 方法页（已实现，持续扩写）

四张卡：费率归一化、三种持仓视角、拥挤度公式、大户筛选。v0.2 加：证据标签说明、每个指标的局限列表、本地研究摘要（标注为"曾观察到，未在本站复现"）、变更日志。

### 4.13 加入社群（已实现）

渐变短线 + 标题 "一起读光谱" + 一段说明（不喊单、不收费带单）+ 按钮（Telegram 主按钮、Discord、X、微信二维码图片）。链接为空时显示"社群入口准备中，先 star 仓库"。全部读 `config.js`。

v0.2 加"入群你会得到什么"三条：每日一张棱镜截图与一句话解读、大户事件推送（先手动、后 Bot）、每周一次公开复盘（含亏损）。

### 4.14 API 页（v0.2）

列出所有 JSON 文件、字段、示例 `curl` 与 `fetch`、给 AI agent 的示例 prompt、MCP 安装命令、速率说明（静态文件无限制，请勿每秒轮询）、schema 版本策略。

### 4.15 页脚（已实现）

免责声明（不构成投资建议、可能亏损超过本金、数据可能延迟或错误）、数据源、刷新周期、MIT、GitHub 链接。

---

## 5. 指标定义

### 5.1 资金费率归一化
`funding_8h = funding_raw × (8 / interval_h)`。跨所平均按 OI 加权。年化 = `funding_8h × 1095`。

### 5.2 三种持仓
- 散户多头占比：Binance `globalLongShortAccountRatio.longAccount`、OKX `long-short-account-ratio` 折算 `r/(1+r)`、Gate `contract_stats.lsr_account` 折算、Bitget `account-long-short.longAccountRatio`、Bybit `account-ratio.buyRatio`（可用时），算术平均。局限：按账户数不按金额。
- 大账户多头占比：Binance `topLongShortPositionRatio.longAccount`（前 20% 账户按持仓金额）与 Gate `contract_stats.top_lsr_size` 折算，二者平均。
- 大户多头占比：追踪的 HL 钱包在该币上的 `long_notional / (long + short)`。样本数显示在旁。

### 5.3 拥挤度
`crowding = mean( clip(funding_8h / 0.0005, −1, 1), clip((retail − 0.5) × 4, −1, 1), clip((taker_buy − 0.5) × 6, −1, 1) )`。缺项自动剔除。只描述人群，不预测。

### 5.4 大户筛选
排行榜账户价值前 90 ∪ 月盈亏前 40 ∪ 周盈亏前 30 ∪ （账户 ≥ 100 万美元中）月 ROI 前 25 → 去重约 140 → 查实时状态 → 保留账户 ≥ 30 万美元或有 ≥ 10 万美元仓位者。

### 5.5 证据标签（全站规范）
- `已验证`：给出数据量、期间、检验方法、效应大小，并链接到方法页。
- `站内自验证`：来自计分板的滚动统计。
- `启发式`：经验规则，无统计检验。
- `观点`：作者立场。
禁止出现无标签的预测性句子。

---

## 6. 变现（目标：覆盖成本，不做生意）

| 路径 | 成本 | 预期 | 备注 |
|---|---|---|---|
| 交易所返佣链接（Hyperliquid 邀请码、Bybit / OKX 联盟） | 0 | 最现实。合约站点用户与开户高度重合；几十个活跃用户即可覆盖每月几十美元 | 页面上以"支持本站：通过此链接注册"呈现，不做诱导 |
| GitHub Sponsors / Ko-fi | 0 | 零星 | README + 页脚 |
| 告警订阅（v0.5）：Telegram Bot 推送大户事件 / 费率极值 / 强平激增，免费版每日摘要，付费版实时（约 5 美元/月） | Cloudflare Worker 约 5 美元/月 | 10 个订阅即覆盖 | 不推信号，只推数据事件 |
| API 高频层（远期） | Worker + KV | 视需求 | 静态 JSON 永远免费，付费只是更高频 |

不做的：付费群、课程、跟单分成。原因：与"证据优先"定位冲突，且用户本地文档明确把此类模式列为骗术。

---

## 7. 路线图

| 版本 | 时间 | 内容 | 验收 |
|---|---|---|---|
| **v0.1** | 今天 | 棱镜 8 币、大户透视、散户心理、双语、Actions 部署 | 线上可访问，`sources` 全绿或 Binance 单独降级 |
| v0.2 | +1 周 | 全币种费率表、RSI 热力图、币种扩展与搜索、API 页、`data/index.json`、schema 冻结、钱包查询并发化、返佣链接位 | Actions 单次 < 90 秒 |
| v0.3 | +3 周 | 大户事件带 + 计分板、真实强平 + 模型热力图、宏观条、90 天历史分文件 | 计分板样本 > 200 事件 |
| v0.4 | +6 周 | 期权页、基差页、hash 路由多页、每行"?"说明 | – |
| v0.5 | +8 周 | MCP server、Telegram Bot 每日摘要（免费）、告警订阅 | `pip install hlens-mcp` 可用 |
| v1.0 | +3 月 | 稳定 schema v1、90 天无中断记录、社群 ≥ 500 人 | – |

---

## 8. 成功指标

- 访问：GitHub Pages 无内置统计，用 Cloudflare Web Analytics（免费、无 cookie）。目标 3 个月日活 300。
- 仓库：3 个月 200 star。
- 社群：转化率 = 点击"加入"/ 访问 ≥ 3%。
- 数据质量：`sources` 全绿的运行占比 ≥ 95%。
- 成本：每月 ≤ 5 美元。

---

## 9. 待用户决定（不阻塞开发）

1. 社群平台与链接（Telegram 建议作为全球主入口，微信作为中文补充）。
2. 返佣链接：是否使用，用哪家。
3. 域名：是否买一个（如 hlens.xyz），GitHub Pages 支持自定义域名，零成本。
4. 是否把 `smip` 等旧公开仓库 README 加一行指向本站。
