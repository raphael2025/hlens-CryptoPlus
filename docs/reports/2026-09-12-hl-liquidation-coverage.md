# hub `hl.liquidation` 覆盖率、延迟与采集能力测量

测量对象：hub 机器 `/mnt/data1/hlens-hub/data/curated/hl/liquidation`（Parquet）+ Postgres 控制面（`wallet` / `wallet_cursor` / `task` / `collection_log` / `worker_node` / `exit_ip_budget`）。DuckDB 2 线程 / 2 GB 只读；所有临时文件已清理，hub 代码与数据未改动。测量时刻 2026-09-12 21:00–22:00（hub 本地时区 UTC+3）。SQL 见附录。

**一句话结论**：这份数据是"逐钱包轮询"的副产品，不是账本。实时窗口（2026-09-08 起）估计完整度 **按笔 91.8% / 按金额 95.4%**，但该估计是自证的、按币差异 51%–93%、采集延迟中位数 **5.9 小时**，历史段（2026-08 之前）捕获率仅 1.6%–8%。**可以做"大户强平事件流"（`completeness=lower_bound`），不能做"全所爆仓统计"，不能做实时窗口，不能跨币排序，不能跨期画趋势。** 全所级要走节点数据。

## 1. 数据是怎么来的（代码事实）

- `hub/ingest.py:591`：拉某地址 fills 时，带 `liquidation{liquidatedUser, markPx, method}` 的成交顺手 `_buffer("hl.liquidation", …)`，并调用 `wallets.note_liquidated_users(liquidated)`。**`hl.liquidation` 是 `hl.fill` 的子集，不是独立采集**。
- 地址册入口只有两个：`hub/wallets.py:109` 排行榜 upsert（写 `lb_account_value`）；`hub/wallets.py:257` `note_liquidated_users`（被强平者补入）。**没有"当过对手方"这个入口**——`hl.fill` schema 里唯一的对方地址字段是 `liquidation_user`，普通成交不含对手方。
- `raw/hl/` 下只有 `clearinghouse / spot_clearinghouse / fills / leaderboard / ledger_update / candle`，**没有 `node_fills_by_block`**——不是节点回放。
- `dt=` 分区是**观测日**（目录只有 2026-09-08…12），`time_ms` 才是成交时间（2024-03-16 → 2026-09-12）。按目录判断"只有 5 天"是假象。

## 2. 数据形状

| 指标 | 值 |
|---|---|
| 行数 / 去重后成交笔数（`tid`） | 570,207 / **537,815**（重复 5.7%，`tid=0` 0 行） |
| 成交时间跨度 | 2024-03-16 → 2026-09-12 |
| 被强平地址 / 采集来源地址 / 币种 | 33,959 / 16,425 / 451 |
| 名义（去重后） | $1,897.5M（market $1,946.6M、backstop $86.1M，去重前口径） |
| `method` | market 568,005 笔；backstop 2,194 笔 |

观测来源拆分（行级）：受害者本人是被追踪钱包 493,843 行 / 15,248 人 / $1,667.5M；由被追踪对手方看到 76,364 行 / 33,695 人 / $365.2M。

## 3. 地址册与"排行榜发现不了的 77%"

`wallet` 表 75,103 个地址，无 `discovered_by` 列：

| 来源 | 地址数 | has_perp | tier1 |
|---|---|---|---|
| 排行榜（`lb_account_value` 非空） | 48,280 | 14,018 | 1,840 |
| 非排行榜（只能来自 `note_liquidated_users`） | 26,823 | 12,888 | 56 |

33,959 个被强平地址与 `wallet` 左连接：**上过排行榜 7,893（23%）；从未上榜 26,066（77%）**；不在册 0。纯排行榜发现会漏掉 77% 的被强平地址；对手方发现机制是覆盖率的主要来源。

## 4. 覆盖率估计（自证估计量）

估计量：受害者本人在册的强平（我们有其完整成交史）中，**同时也被某个在册对手方看到**的比例 = "受害者不在册时，靠对手方能看到"的概率。

### 4.1 成交笔（tid）级

| 时期 | 自观测笔数 | 也被对手方看到 |
|---|---|---|
| 全历史 | 493,758 | **6.5%** |
| 2025-10 … 2026-08（逐月） | — | 1.1%–2.9% |
| 2026-09（实时采集器 09-08 开跑后） | 67,469 | **35.4%** |

三分法（全历史）：仅自观测 461,465 笔 $1,532.3M；仅对手方 44,057 笔 $230.0M；两者 32,293 笔 $135.2M。

### 4.2 强平订单（victim, oid）级——正确的单位

一个强平订单被撮合拆成多笔，任一笔被看到即算捕获。**每笔强平成交只有一个对手方**（实时窗口 `n_cp`：0 → 26,159 笔，1 → 31,458 笔，3 → 7 笔），捕获与否 = 那个唯一对手方在不在册。

| 订单被拆成 | 订单数 | 平均名义 | 捕获率 |
|---|---|---|---|
| 1 笔 | 188,890 | $2,646 | 6.3% |
| 2–3 | 28,444 | $5,310 | 9.6% |
| 4–10 | 15,333 | $16,665 | 17.7% |
| 11–50 | 5,542 | $76,777 | 34.2% |
| 50+ | 425 | $789,876 | 50.6% |

实时窗口（≥ 2026-09-08），按订单金额分层：

| 金额档 | 自观测订单 | 平均拆分笔数 | 捕获率（按笔 / 按金额） |
|---|---|---|---|
| <$100 | 5,166 | 1.0 | 62.0% / 63.2% |
| $100–1k | 5,863 | 1.3 | 63.2% / 63.0% |
| $1k–10k | 3,947 | 2.6 | 65.4% / 66.7% |
| $10k–100k | 1,784 | 9.0 | 73.8% / 76.8% |
| >$100k | 307 | 32.0 | 85.0% / 93.7% |

全历史同表：<$100 7.9%、$100–1k 7.0%、$1k–10k 7.6%、$10k–100k 14.1%、>$100k 32.3%（按金额 44.0%）。

### 4.3 实时窗口完整度外推

完整度 = (在册受害者订单 + 不在册受害者订单) / (在册 + 不在册 ÷ 捕获率)。

| 档 | 捕获率 | 已捕获订单 | 已捕获金额 | 外推真实金额 | 完整度（按金额） |
|---|---|---|---|---|---|
| <$1k | 72.6% | 23,160 | $5.67M | $6.19M | 91.7% |
| $1k–10k | 72.3% | 8,508 | $28.98M | $31.61M | 91.7% |
| $10k–100k | 68.6% | 2,745 | $84.62M | $91.34M | 92.6% |
| >$100k | 76.1% | 323 | $167.81M | $171.34M | 97.9% |
| **合计** | | **34,909 → 外推 38,021（91.8%）** | **$287.1M → 外推 $301.0M（95.4%）** | | |

**这个数字不是高置信度**：(a) 估计量假设"不在册受害者的对手方在册概率 = 在册受害者的"，分层后缓解但未消除；(b) 只有 5 天样本，含 09-11 单日 $160.6M 放量；(c) 无外部真值。

### 4.4 按币差异（实时窗口，≥50 自观测订单的 60 个币）

捕获率分布：**min 51.2% / p25 68.6% / 中位 78.3% / p75 83.4% / max 93.2%**。主流币反而最低：ETH 58.1%（$26.7M）、BTC 65.5%（$164.5M）、SOL 68.0%、XRP 66.9%；冷门 WLD 88.6%、xyz:AAPL 92.9%。原因：主流币对手方池大，在册地址占比小。实时窗口 249 个币有强平，Top5 占金额 77.8%、Top20 占 91.2%——**总量的大头正好落在覆盖最差的币上**，跨币排序被系统性扭曲。

## 5. 采集延迟

实时窗口 80,759 行，`observed_at − time_ms`：**P25 36 分钟 / 中位 353 分钟（5.9 h）/ P75 994 分钟（16.6 h）/ P95 2,000 分钟（33 h）**。最近 6 小时按币小时聚合只出 2 行（PUMP $34.9k、xyz:UNITREE $13.8k）——不是没爆仓，是还没采回来。**任何"最近 N 小时"的数字都会在之后一两天自己往上爬。**

日序列（去重）：09-07 2,916 笔 → 09-08 7,323 → 09-09 10,589 → 09-10 21,810（$70.6M）→ 09-11 17,369（$160.6M）。09-08 的台阶是实时采集器开跑，不是行情。

月序列里 `observers`（贡献记录的在册钱包数）与强平量同步增长：2024-03 118 观察者 / 1,005 笔 → 2026-09 10,683 / 100,698。**历史"增长"主要是观测网变密，不能跨期画趋势。**

## 6. 对手方结构与做市商行为

实时窗口 1,446 个在册地址贡献 31,479 次对手方捕获：Top10 28.2%、Top50 62.2%、**Top200 83.5%**、Top1000 98.6%。头部为做市商画像（第一名 1,920 次、81 币、1,480 个不同受害者）。来源：排行榜 1,156 个（贡献 62.8%），自身被强平过 290 个（**20% 的地址贡献 37.2%**，单位价值 2.4×）；不在册 0。→ 榜外确有高价值对手方，但只能"意外"（自己被强平）进册；从未被强平、又不上榜的做市商结构性不可见。

做市商在对手方一侧的行为（实时窗口）：
- `crossed=false` **100%**（31,467/31,467）：全部是被动挂单被风控市价单打到，不存在"主动吃单"。
- `dir`：Open Long 45.9% / Close Short 29.7% / Open Short 13.5% / Close Long 8.4% / 翻转 2.5%——方向由受害者一侧决定，Open/Close 标签只反映当时库存符号。
- 被打之后：按 (钱包, 币) 全窗口 5,006 对，其余流向反向 58.4% / 同向 41.4%，`corr(x,y)=0.128`；**按被吸收金额加权反向 74.0%**（最终摊平）。按小时 (h, h+1) 11,373 组：反向 45.0% / 同向 50.7% / 无其他成交 4.3%——同向部分受"级联期间其余被动单也在被打"混淆，未拆 `crossed`，且 CEX 侧对冲不可见。**无方向性观点的证据**；只能当市场结构信号。

## 7. 采集能力

10 个 worker，**6 个出口 IP**（frankfurt / fnnas / shanghai / thinkpad 各两个 worker 共用一个 IP，共用同一份 1,200 weight/min + `max_inflight=10`）。过去 60 分钟：

| 节点 | weight/min | 占预算 | 平均 RTT |
|---|---|---|---|
| tokyo | 992 | 82.6% | 33 ms |
| frankfurt ×2 | 991 | 82.6% | 285 ms |
| singapore | 940 | 78.4% | 108 ms |
| fnnas ×2 | 780 | 65.0% | 471 ms |
| thinkpad ×2 | 577 | 48.1% | 878 ms |
| shanghai ×2 | 504 | 42.0% | 962 ms |
| **合计** | **4,784 / 7,200** | **66%** | |

- 队列几乎空：`hl.clearinghouse` 85 pending（36.09M done）、`hl.fills` 790（2.05M done）、`hl.ledger` 394、`hl.spot_clearinghouse` 2,212。`wallet_cursor`：fills 36,000 条中 35,848 caught_up；ledger 75,104 中 75,091。
- 分层未按设计生效：`perp_equity_at` 距今中位 tier1 **48 min** / tier2 44 min / tier3 70 min（文档写 hot 每 1 min）。
- 调用构成（过去 1 h）：`spot_clearinghouse` 33,999、`clearinghouse` 33,995、**`fills` 2,257**、`ledger` 853。fills 是唯一带 `liquidation` 字段的流。spot/ledger 有各自用途（`perp_equity` 分层、权益曲线拆出入金），不是冗余。
- 分钟级不可达：75,103 × 权重 2 = 150,206 weight/轮，对 7,200/min 差 **21×**（≈125 个独立出口 IP）。

**延迟与地址覆盖是两个独立的轴**：延迟只打死实时窗口，接受 48 h 延迟即消失；地址覆盖是永久的洞，等多久都补不回。两者都是"逐钱包轮询 REST"路径的属性，节点数据路径两者皆无。

## 8. 待办（按优先级）

0. **外部验证**：拉一天 `s3://hl-mainnet-node-data/node_fills_by_block` 与 hub 同日逐笔比对，把 §4.3 的自证估计换成实测，并给出按币真实缺口。**任何完整度声明的前提。**
1. **节点数据链路**（全所级唯一出路）：月度存档 `hyperliquid-archive` 做回填；实时需自跑非验证节点（`--write-fills --batch-by-block`），归入东京后端规划，不碰静态站。这是唯一能标 `completeness=full` 的路。
2. 轮询路径补 `trades{coin}` 发现入口（`users:[buyer,seller]`，按币订阅、不吃 `/info` 权重，见 `2026-09-12-hl-ws-trades.md`）→ 补上"当过对手方"入口。
3. 查分层为何未生效（影响大户临近强平告警），再用闲置的 34% 预算调节奏。
4. 共用出口 IP 的第二 worker 挪到新 IP 或关闭。
5. 展示策略：hub 来源只展示超过 P95 延迟（≈33 h）的窗口，或标"仍在回填"。
6. 契约：hub 路径一律 `completeness=lower_bound`；节点路径才可 `full`。钱包级衍生的方向性说法先过记分板。
7. （可选）做市商后续成交按 `crossed` 拆分，收干净 §6 的混淆。

**分工不变**：钱包级（大户在哪、谁快爆、地址历史）留在轮询路径；全所级（强平总量、全所多空、持仓地图）去节点数据。

## 附录：核心 SQL（DuckDB，`P` = liquidation Parquet glob）

```sql
-- 去重与形状
SELECT count(*), count(DISTINCT tid), count(DISTINCT liquidated_user),
       count(DISTINCT address), min(time_ms), max(time_ms) FROM read_parquet(P);

-- 每笔 tid 的观测方式
CREATE TEMP VIEW obs AS
SELECT tid, bool_or(lower(address) =  lower(liquidated_user)) AS by_self,
            bool_or(lower(address) <> lower(liquidated_user)) AS by_cp
FROM read_parquet(P) GROUP BY tid;

-- 强平订单 = (victim, oid)，任一笔被对手方看到即捕获
CREATE TEMP VIEW orders AS
WITH d AS (SELECT DISTINCT ON (tid) tid, liquidated_user, oid, coin, time_ms, px, sz FROM read_parquet(P))
SELECT d.coin, d.liquidated_user, d.oid, min(d.time_ms) AS time_ms, sum(d.px*d.sz) AS notional,
       count(*) AS n_fills, bool_or(o.by_self) AS victim_tracked, bool_or(o.by_cp) AS seen_by_cp
FROM d JOIN obs o USING (tid) GROUP BY 1,2,3;

-- 捕获率（在受害者在册的订单上测）与分层外推
WITH b AS (SELECT *, CASE WHEN notional<1000 THEN 'a' WHEN notional<10000 THEN 'b'
                          WHEN notional<100000 THEN 'c' ELSE 'd' END AS bkt
           FROM orders WHERE time_ms >= epoch_ms(TIMESTAMP '2026-09-08')),
rate AS (SELECT bkt, count(*) FILTER (WHERE seen_by_cp)::double/count(*) AS p FROM b WHERE victim_tracked GROUP BY 1),
cap  AS (SELECT bkt, count(*) FILTER (WHERE victim_tracked) n_t, sum(notional) FILTER (WHERE victim_tracked) u_t,
                count(*) FILTER (WHERE NOT victim_tracked) n_u, sum(notional) FILTER (WHERE NOT victim_tracked) u_u
         FROM b GROUP BY 1)
SELECT sum(n_t+n_u) captured, sum(n_t+n_u/p) implied, sum(u_t+u_u) captured_usd, sum(u_t+u_u/p) implied_usd
FROM cap JOIN rate USING (bkt);

-- 延迟
SELECT median(epoch(observed_at)-time_ms/1000.0)/60, quantile_cont(epoch(observed_at)-time_ms/1000.0,0.95)/60
FROM read_parquet(P) WHERE time_ms >= epoch_ms(TIMESTAMP '2026-09-08');

-- 每笔成交的在册对手方数（只能是 0/1）
SELECT n_cp, count(*) FROM (SELECT tid, count(*) FILTER (WHERE lower(address)<>lower(liquidated_user)) n_cp
                            FROM read_parquet(P) GROUP BY 1) GROUP BY 1;
```

```sql
-- Postgres：受害者/对手方与地址册来源的交叉（victims.csv 由 DuckDB COPY 导出）
CREATE TEMP TABLE v(address text);  \copy v FROM 'victims.csv' CSV HEADER
SELECT count(*) FILTER (WHERE w.lb_account_value IS NOT NULL) on_leaderboard,
       count(*) FILTER (WHERE w.address IS NOT NULL AND w.lb_account_value IS NULL) never_on_leaderboard,
       count(*) FILTER (WHERE w.address IS NULL) not_in_book
FROM v LEFT JOIN wallet w USING (address);

-- 采集能力
SELECT exit_ip, count(*), sum(weight_spent)/60.0 wpm, avg(duration_ms)
FROM collection_log WHERE observed_at > now() - interval '60 min' GROUP BY 1;
SELECT tier, count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM now()-perp_equity_at)/60)
FROM wallet GROUP BY 1;
```
