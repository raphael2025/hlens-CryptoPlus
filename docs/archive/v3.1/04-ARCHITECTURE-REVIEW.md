# hlens · 仓库级架构审查（2026-09-11）

> 目的：以 raphael 现有代码的真实状态为准，回答"CryptoPlus 复用什么、不碰什么、Docker 怎么落"。审查对象：GitHub `hlens`、`hlens-crypto`、`hlens_V3`、`hlens_V4`、`smip`、`hlens-dashboard`，以及 hub 机器上正在生产运行的 `hlens-hub`（未上 GitHub）。三个 Opus 审查代理分组阅读代码，本文是合并后的结论；细节引用见 `docs/reviews/2026-09-11-round3-repos.md`。

## 0. 三个纠正

1. **`hlens`（GitHub）不产生 Parquet**。它是一套内容寻址的原始档案采集系统（chunk = SHA256 + zstd，spool/WAL，无数据库），3 万行代码、2 万行测试、CI 四道契约门禁。hub 上的 `curated/hl/*.parquet` 由 **`hlens-hub`**（hub 机器本地项目，FastAPI 控制面 + 分布式 worker + DuckDB 转 Parquet）生产。之前文档把两者混为一谈。
2. **`hlens-crypto` 不是代码仓库**：92% 体积是误提交的 agent 运行时缓存（`.ccb/` 684 MB），其余是 Obsidian 知识库；不产生任何服务。建议 `git filter-repo` 清理。
3. **`hlens-CryptoPlus` 里没有多 Agent 运行基础设施**，只有静态站与抓取脚本。

## 1. 十四问总表

| # | 问题 | 结论（按仓库） |
|---|---|---|
| 1 | 目录结构 | `hlens`：src 30,890 行 / tests 20,555 行 / docs 18 MB（交易所 API 原文）。`hlens-hub`：31 个源文件，hub/ worker/ dashboard/ tools/。`smip`：三层 FastAPI + Next 14。`hlens-dashboard`：adapters + registry + 两套前端。`hlens_V3`：TS + Postgres。`hlens_V4`：Python + DuckDB 回测台。 |
| 2 | 启动入口 | `hlens`：单 CLI `hlens {record,hub-serve,hl-addresses,verify,preflight,...}` + systemd（**bug**：`hlens-hub.service` 写的是 `hlens hub`，命令不存在）。`hlens-hub`：`run-hub.sh`（nohup）+ `python -m hub`，worker 由 `join.sh` 装成 systemd。`smip`：docker compose。 |
| 3 | 依赖 | `hlens`：pyproject + uv.lock，Python ≥ 3.12，可复现；两个 extras（hub/offline）代码零引用。`hlens-hub`：6 个依赖，worker 零第三方依赖。`smip` 前端无 lockfile。 |
| 4 | 存储 | `hlens`：文件系统 chunk + 可重算 SQLite。`hlens-hub`：Postgres 只做控制面（"研究数据一行不进 PG"），产物 Parquet 按观测日分区，DuckDB 只是 COPY 引擎，`catalog` 存绝对路径。`smip`：SQLAlchemy 旧式模型，无 hypertable 无唯一键。 |
| 5 | 边界 | `hlens`：recorder / poller / spool / shipper / hub / hyperliquid / assertions / contract，无共享 DB，worker↔hub 只有 HTTP + 内容寻址幂等。`hlens-hub`：hub 分配配额、worker 单槽执行、submit 与 claim 合并；纯 REST，**零 WebSocket**。 |
| 6 | 文档 | `hlens` contracts 与代码由 CI 强制一致（可信）；STATUS 是快照。`hlens-hub` 的注释里写满了实测常量（限速、429 两种形态、AIMD），比代码更值钱。 |
| 7 | 配置 | `hlens`：一份 YAML + 唯一令牌走 env（容器友好）；但存在两套 `NodeConfig` 模型。`hlens-hub`：**`hub.env` 明文提交 Tailscale OAuth secret；admin 端点无鉴权且绑 0.0.0.0**。`hlens-dashboard` 偷读隔壁仓库 `.env`。 |
| 8 | 状态恢复 | `hlens`：WAL 三不变量、重启重封幂等、receipt、复合游标。`hlens-hub`：`wallet_cursor(cursor_ms, cursor_tids, caught_up, fill_rate)`、`collection_log`、`sync_round`、可中断重跑的 `build_fill_history.py`。`smip`：无。 |
| 9 | 测试 | `hlens` 87 个文件完全离线可跑；`hlens-hub` 46 个真行为用例需真 Postgres；`hlens-dashboard` 有可注入 Registry 的测试基座；`smip` 零测试。 |
| 10 | Docker 痕迹 | 只有 `smip` 有（单阶段、root、端口绑 0.0.0.0、密码进 command、`.dockerignore` 放错位置）。其余全零。四台云机"都装了 docker，零容器"。 |
| 11 | 与 CryptoPlus 耦合 | 唯一真实交集是 **hub 的历史数据**。hub 无读接口、无 WS、fills 最快 5 分钟一轮，喂不到"大户 ≤ 5 分钟"的新鲜度预算，实时必须东京自建。 |
| 12 | 必须先改的文档 | 见 §4。 |
| 13 | 不要动的代码 | 见 §3。 |
| 14 | Docker 服务数 | CryptoPlus 自己的：东京 9、新加坡 10（见 `05-DOCKER.md`）。hub 与 `hlens` **不进** CryptoPlus 的 compose。 |

## 2. 复用清单（路径 → 去向）

| 来源 | 去向 | 说明 |
|---|---|---|
| `hlens/src/hlens/contract/**` | `packages/hlens-core/contracts` | chunk_id 是跨系统引用锚点，原样 |
| `hlens/src/hlens/assertions/**`（2193 行 QA 规则） | `packages/hlens-core/qa` | CryptoPlus 没有等价物，价值最高 |
| `hlens/contracts/*.yaml` + `hub/catalog.py` + CI 四门禁 | `packages/hlens-core/catalog` + CI | 机器强制一致性 |
| `hlens/src/hlens/hyperliquid/budget.py`（EgressWeightBudget） | `packages/hlens-core/ratelimit` | 即 `venues.yaml` 预算的成品实现 |
| `hlens/src/hlens/node/{egress,preflight,country}.py` | `packages/hlens-core/preflight` | 容器化后出口判定更重要 |
| `hlens/src/hlens/instruments/symbolmap.py` | `packages/hlens-core/instruments`（扩展到 300 币） | |
| `hlens/src/hlens/recorder/venues/*.py` | 只抄常数（URL、探活、feed_fidelity 实测结论） | I/O 层重写：hlens 输出 raw frame，CryptoPlus 要 parsed row |
| `hlens-hub` 注释与 `quota.py` 的常量 | `venues.yaml` + `03-DEV §4.1` | 两种 429、`max_inflight=10`、AIMD 砍 75% 冻结 1h、L3 按条数保留 |
| `hlens-hub` `wallet_cursor` 语义（时间 + 该毫秒已见键重数） | 东京 collector 的游标 | 抄语义不抄代码 |
| `hlens_V4/backtest/pit.py`（PIT 六维 grade，含无前视断言） | `apps/analytics` 钱包评分 | **不要用** V3 `grade-calculator.ts`（会前视） |
| `hlens_V3/style-tagger.ts`（whale/veteran/high_freq/stable/concentrated） | `wallets.style_tags` | high_freq 是剔除做市商的过滤器 |
| `hlens_V3/signal-engine.ts` `calcLiqDistance` | RektSoonBoard | **必须加** hub 的 `liq_px_reliable` 门 |
| `hlens_V4/storage/silver_build.py` dir 归一化与强平标志陷阱 | 事件识别 | `"liquidation": null` 键会把全部成交误标为强平 |
| `hlens_V4/{friction,ledger,metrics,baselines}.py` + 回测陷阱清单 | 记分板 | 块 bootstrap、基线、holdout lock 的现成实现 |
| `hlens_V4/tools/grab_binance_klines.py` | K 线回填 | 300 币历史走 Binance data.vision |
| `smip/backend/app/{database,config}.py` | `packages/hlens-core/db` | 引擎、session、Redis 工厂 |
| `smip/backend/app/collectors/binance_collector.py` WS 重连骨架 + `on_conflict_do_nothing` 写法 | `apps/collector` 片段 | 其余重写 |
| `smip/frontend/Dockerfile` 三阶段 standalone | `apps/web/Dockerfile` | 加 USER、HEALTHCHECK、lockfile |
| `smip/nginx/nginx.conf` 反代与 WS 段 | `deploy/Caddyfile` | 限流键改 `CF-Connecting-IP` |
| `smip/frontend/src/components/{OrderbookPage,LiquidationPage,SignalsPage,ErrorBoundary}.tsx`、`i18n/*.json` | `packages/ui` | 多空条、堆叠柱、事件流、错误边界、术语表 |
| `smip/docker-compose.yml` 服务切分与 `service_healthy` | `deploy/compose.*.yml` 骨架 | 硬化后 |
| `hlens-dashboard/backend/app/{adapters/base,cache,registry,deps}.py` | `packages/hlens-core/adapters` + API 读层降级（`cache_if` 5s） | |
| `hlens-dashboard/backend/tests/conftest.py`、`pytest.ini` | `tests/` 基座 | `-m "not live"` |
| `hlens-dashboard/web/{package.json,lib/hooks.ts,lib/api.ts,components/Chart.tsx}` | `apps/web` 骨架 | Next 15 + React 19 + TanStack Query 5 + lightweight-charts，与目标一致 |
| `hlens-CryptoPlus/scripts/fetch.py` 六个交易所函数 + `build_coin/norm8h` | `packages/hlens-core/adapters/*` + `apps/analytics/prism_builder` | 改 instruments 驱动、字段名对齐 Prism、写库 |
| `hlens-CryptoPlus/.github/workflows/{refresh,diag}.yml` | CI 骨架 + 源可达性探针 | |

## 3. 现在不要动的代码

- `hlens`：`spool/`、`contract/chunk.py`、`shipper/state.py`、`hyperliquid/{cursor,store,budget}.py`、`recorder/venues/*.py` 的实测常数。改一行的表现是五年后数据系统性偏低而非报错。
- `hlens-hub`：全部。它在生产采集，L3 成交窗口只有 1.9 小时，中断即永久丢数据。上线前唯一可评估的改动是加一个只读导出端点。**但两处安全问题应由 raphael 立即处理**：轮换 `hub.env` 的 Tailscale secret；`HLENS_HOST` 改回 127.0.0.1 或补 admin 鉴权。
- `hlens-CryptoPlus`：`scripts/fetch.py`、`assets/app.js`、`index.html`、`refresh.yml` 是当前唯一在线产品，S1 前保持可用；改造在新建 `apps/snapshot` 里做。
- `smip`、`hlens-dashboard`：只拷不改。
- `openapi.yaml` 的既有 operationId 与 Prism 字段名不为迁就 fetch.py 而改。

## 4. 大户引擎决策：数据走 hub，引擎自建，否决容器化 hub

| 选项 | 结论 |
|---|---|
| (a) 只读 hub 的 Parquet 作为上游 | **采用**（数据面）。一次全量（`fill_history` 2023-02 → 2026-08）+ 持续增量（`fill` 仍在长，按 `time_ms` 水位，`(address, tid, tid=0 时内容哈希)` 去重）。 |
| (b) 把 hub 容器化纳入 compose | **否决**。零 Docker 痕迹；要改 DATA_ROOT、catalog 绝对路径、PG 连接、systemd→compose、7 个远端 worker；收益为零，因为容器不改变出口 IP，配额按出口 IP 记账；风险是不可逆丢数据。 |
| (c) 东京重写 whale-engine | **采用**（引擎面）。事件用 `dir` + `start_position` 判定，历史与实时同一套代码。 |

八步迁移：① 核对东京出口 IP 不在 hub 的 `exit_ip_budget` 里（相同则两套限速器互不知情会超发）；同时处理 hub 两处安全问题。② 先改 03-DEV §3/§4.1/§4.2/§4.7（本轮已改）。③ `apps/importer` 在 hub 上只读 DuckDB（2 线程 / 3 GB），不碰控制面。④ 全量导入 fill_history、position、account_snapshot、liquidation、leaderboard → staging → `ON CONFLICT DO NOTHING`。⑤ 增量只取 `fill` 中 `time_ms >` 水位的行。⑥ 东京 whale-engine：事件识别、V4 PIT 评分、V3 style_tags、`calcLiqDistance` + `liq_px_reliable` 门。⑦ 东京 HL collector：WS `userFills` hot 200 + REST `clearinghouseState`；断线缺口走 `userFillsByTime`（old→new）与复合游标，不与 `userFills`（new→old）混用。⑧ hub 代码在 CryptoPlus 上线前不改。

## 5. 被审查证伪、已在本轮修正的文档点

| 文档 | 修正 |
|---|---|
| 03-DEV §3 `hl_fills` | 主键 `tid` 不唯一（hub 实测 207,635 条 tid=0）→ 改 `(address, tid, content_hash)`；补 `start_position`、`fee_token`、`oid`、`crossed`、`ingest_ts`、`liquidation_method`、`liquidation_mark_px`；`notional_usd` 标派生；过滤 `@`/`/` 开头的现货 coin。 |
| 03-DEV §3 `whale_events` | `ts_bucket` 列化；补 `src_tid`、`src_hash`（可点验证链接）。 |
| 03-DEV §3 `hl_positions` / `hl_wallet_state` | 补 `max_leverage`（MMR 输入）、`margin_used`、`roe`、`total_ntl_pos`；cross 仓位 `liquidationPx` 不可跨仓位累加。 |
| 03-DEV §3 `wallets` | 分层依据 `perp_equity` 而非榜单 `account_value`（榜单含现货，实测 rank0 报 135.9 亿而永续账户仅 1 万美元）；补 `fills_empty_terminal` 终态标记。 |
| 03-DEV §4.1 HL | 补两种 429（JSON null = 权重限速 → 退避权重；nginx HTML = 连接限速 → 降并发）；并发硬顶 10；AIMD；预算不是常数；出口 IP 必须与 hub worker 不同。 |
| 03-DEV §4.7 | 生产者是 `hlens-hub` 不是 `hlens`；区分 `fill_history`（已去重排序、按成交日分区）与 `fill`（按观测日分区、与 V4 导入重叠 1.1%）；改为持续增量；`fee_token` 换算；`hl.kline` 实质只有 BTC，300 币历史走 Binance data.vision；funding/kline 为 V4 冻结历史（截至 2026-07-25）。 |
| 03-DEV §1 | `packages/hlens-core` 补 `contracts/qa/catalog/ratelimit/preflight/db/adapters`；`scripts/fetch.py` 归 `apps/snapshot`。 |
| 03-DEV §8 / 05-DOCKER | 补 `migrate`、`caddy`、`snapshot`、`api-standby(profile)`；出站 IP 规则；DOCKER-USER 链；compose secrets；logging 限额；资源限制写法。 |
| 00-PROJECT §7 | hub 与 `hlens` 是独立系统，不进 CryptoPlus compose；东京出口 IP 隔离要求。 |
| 02-API §6 | SnapshotFile schema 1 → 2 字段映射表。 |
