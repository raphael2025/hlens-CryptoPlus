# hlens · 开发与运维文档（v3.2）

> v3.2：按 `04-ARCHITECTURE-REVIEW.md`（仓库级审查）修正：hub 数据生产者、`hl_fills` 主键、HL 限速细节、导入流程、hlens-core 复用包、Docker 服务清单见 `05-DOCKER.md`。

## 1. 仓库结构（monorepo）
```
api/openapi.yaml                    # 契约唯一真相源
apps/api          FastAPI：REST + WS + MCP（新加坡）
apps/web          Next.js 15 + TS（新加坡）
apps/collector    REST 轮询 + WS 常驻（东京；两种进程共用包）
apps/whale-engine 候选池、状态、fills、事件、回填（东京）
apps/analytics    prism/state/sentence/context/levels/cascade/scorecard/calibrate（东京）
apps/alert-engine 规则评估与投递（东京）
apps/importer     hub 历史数据导入（DuckDB → Postgres）
packages/hlens-core  Python 共享：contracts（自 hlens）、qa（自 hlens assertions）、catalog（自 hlens + CI 门禁）、ratelimit（自 hlens hyperliquid/budget）、preflight（自 hlens node）、instruments、db（自 smip database/config）、adapters（自 hlens-dashboard base/cache/registry + fetch.py 六所函数）、指标、状态规则、句子引擎
apps/snapshot     由 scripts/fetch.py 改写：读本地 API → data/latest.json（schema 2）→ 推 Pages（降级层）
packages/hlens-mcp   PyPI 包
packages/ui          前端共享组件
config/  state_rules.v1.yaml · sentence_templates.{zh,en}.yaml · venues.yaml · calendar_macro.json · scorecard_groups.yaml（预注册分组）· evidence.yaml（证据库，`listEvidence` 数据源）
docs/changelog.md                    # `listChangelog` 数据源（构建时解析）
db/migrations（Alembic；CAGG 用 autocommit 迁移）
deploy/  compose.tokyo.yml · compose.sg.yml · compose.staging.yml · Caddyfile · cloudflared/ · prometheus/ · grafana/ · scripts/{deploy,backup,restore,failover,promote}.sh
tests/{unit,contract,golden,integration}
scripts/fetch.py · data/            # v0.1 静态快照层；S1 前保持在线，改造在 apps/snapshot 里做，不原地改
docs/ · docs/runbooks/ · docs/reports/
```

## 2. 本地开发
```bash
make up        # postgres+timescale, redis, api, web
make seed      # fixtures：2 年 1d + 90 天 1h K 线 + 24h 全量样本（状态计算需要 EMA200/ADX14）
make gen       # openapi.yaml → pydantic + TS
make lint      # ruff, mypy, eslint, tsc, redocly lint, oasdiff
make test      # unit + golden + contract（freeze_time + 固定 rules_version）
make calibrate # analytics/calibrate_states.py → docs/reports/
```
本地不需要任何交易所 key。

## 3. 数据模型（TimescaleDB 2.x）

### 3.1 通用规则
- 每张时序表 `UNIQUE(venue, symbol, ts)`（或等价键）+ `ingest_ts timestamptz DEFAULT now()`（记录时钟偏移）。
- 写入：UNLOGGED staging → `INSERT … SELECT … ON CONFLICT DO NOTHING`；禁止裸 `COPY` 到主表。
- 压缩：`compress_segmentby='venue,symbol', compress_orderby='ts DESC'`；`compress_after` 必须早于 `retention`。
- `instruments` 归一化：所有采集经映射（`1000PEPE`/`kPEPE`/`PEPE`、合约乘数、币本位标记、维持保证金分档）。

### 3.2 表（保留 / 压缩 / 估算）
| 表 | 频率 | 压缩后 / 保留 | 估算（300 币 × 4 所：Binance/Bybit/OKX/HL；v1.1 加 Gate/Bitget 约 ×1.5） | 备注 |
|---|---|---|---|---|
| `instruments(venue, venue_symbol, symbol, mult, tick, is_inverse, mmr_tiers jsonb, listed_ts, delisted_ts)` | – | – | – | PK(venue, venue_symbol)，UNIQUE(venue, symbol) |
| `ticks_1m`（CAGG，由 WS 标记价流生成 OHLC） | 1m | 1d / 2y | 1.7M 行/天 ≈ 40 MB/天压缩前 | 不存 5s 原始 tick；原始只进 Redis 60 秒环 |
| `funding(ts, venue, symbol, rate, interval_h, rate_8h, next_ts)` | 变化时写 | 1d / 2y | 约 300 所-币 × 3/天 | 预测费率单独 `funding_pred` |
| `open_interest(ts, venue, symbol, oi_base, oi_usd)` | 1m | 1d / 2y | 1.7M 行/天 | 全市场端点一次拉全 |
| `ls_ratio(ts, venue, symbol, kind, long_share)` | 5m | 1d / 2y | 0.35M 行/天 | kind 分开存，不做无权重平均 |
| `klines(ts, venue, symbol, tf, o,h,l,c,v, taker_buy_v)` | 1m 参考所 | 1m:30d，1h/1d 由层级 CAGG 永久 | 0.4M 行/天 | 参考所 = Binance（美区外） |
| `liquidations(ts, venue, symbol, side, price, size, notional_usd, cascade_id, throttled_source)` | 实时 | 1d / 2y | 峰值日 1M 行 | Binance/Bybit 为限流下界 |
| `hl_asset_ctx(ts, coin, mark, funding_1h, oi, premium, vlm)` | 1m | 1d / 2y | 0.34M 行/天 | |
| `hl_wallet_state(ts, address, account_value, margin_used, total_ntl_pos, withdrawable)` | hot 1m / warm 10m | 1d / 1y | 0.4M 行/天 | 权益曲线来源 |
| `hl_positions(ts, address, symbol, side, size, notional_usd, entry, liq, lev, lev_type, max_leverage, margin_used, upnl_usd, roe, is_cross, liq_px_reliable)` | 同上 | 1d / 1y | 1M 行/天 | 对账用；事件以 fills 为准。cross 仓位的 `liq` 语义是"其他仓位不变时"，不可跨仓位累加；`liq_px_reliable=false` 的仓位不进 RektSoonBoard |
| `hl_fills(address, tid, content_hash, ts, ingest_ts, symbol, side, dir, start_position, price, size, notional_usd, fee, fee_token, fee_usd, closed_pnl_usd, oid, crossed, hash, liquidation, liquidation_method, liquidation_mark_px)` | WS + 导入 | 7d / 永久 | 导入 5 亿行 ≈ 40 GB 压缩后 | **PK(address, tid, content_hash)**：hub 实测 207,635 条 `tid=0`，`tid` 单独不唯一；`content_hash` = sha1(ts, symbol, side, price, size, dir)。`notional_usd = price × size` 派生；`fee_usd` 按 `fee_token` 换算；过滤 `@` 开头或含 `/` 的现货 coin；`liquidation = liquidation_user IS NOT NULL`（注意 `"liquidation": null` 键的陷阱，不能用字符串匹配） |
| `whale_events(id, ts, ts_bucket, address, source, symbol, type, side, size_delta, notional_delta_usd, price, src_tid, src_hash, ret_1h, ret_4h, ret_24h, ret_ref_venue, filled_at)` | 事件 | – / 永久 | – | UNIQUE(address, symbol, type, ts_bucket)；`src_tid`/`src_hash` 供页面"可点验证链接"回链 |
| `coin_prism_1m(symbol, ts PK, as_of, sources jsonb, venues jsonb, price, chg24h_pct, vol24h_usd, funding_8h, funding_apr_pct, funding_spread, oi_total_usd, retail_long_share, top_trader_long_share, taker_buy_share, whale_long_share, whale_long_usd, whale_short_usd, gap_pts, crowding, crowding_pctl_30d, liq_24h_long_usd, liq_24h_short_usd, facets jsonb)` | 1m | 1d / 2y | 0.43M 行/天 | `facets` = 每个分面的 `{value, pctl_30d, n, as_of, tag}`（YAML `Facet`）；`/snapshot` 回放的来源 |
| `coin_state(symbol, tf, rules_version, ts PK, state, state_start_ts, confirmed, hits jsonb, features jsonb)` | K 线收盘 | – / 永久 | 小 | |
| `sentence_log(symbol, page, ts PK, inputs_hash, inputs jsonb, template_id, contradiction_template_id, tags text[], rules_version)` | 变化时 | – / 1y | 小 | UNIQUE(symbol, page, inputs_hash)；不存文本 |
| `market_events(id, ts, type, symbol, severity, vars jsonb, template_id, replay_at)` | 事件 | – / 2y | 小 | |
| `liq_cascades(id, symbol, side, start_ts, end_ts, notional_usd, price_move_pct, pctl_30d)` | 事件 | – / 永久 | 小 | |
| `scorecard_daily(date, group_key, horizon, rules_version, n, n_effective, median_ret, p25, p75, hit_rate, wilson_low, wilson_high, q_value, p_break_low, tag)` | 日 | – / 永久 | 小 | `group_key` 来自 `config/scorecard_groups.yaml`；`p_break_low` 供 `StateStats` |
| `coin_meta(symbol, mcap_usd, circulating, updated_ts)` | 10m | – / 当前 | 小 | CoinGecko 免 key（5–15 次/分），供 `oi_to_mcap` |
| `source_health_1m(ts, venue, kind, ok bool, latency_ms)` | 1m | 1d / 90d | 小 | `uptime_30d` 与状态页来源 |
| `macro(ts, key, value)` | 1h/1d | – / 永久 | 小 | |
| `ingest_watermark(venue, symbol, kind, tf, last_closed_ts, gaps jsonb)` | – | – | – | 断线补齐依据 |
| `source_health(venue, kind, last_ok_ts, latency_ms, http_status, consecutive_fail)` | – | – | – | 当前态 |
| `alert_state(rule_id PK, last_value, last_fired_ts, hour_bucket, fires_this_hour)` | – | – | – | |
| 业务表 | `users, sessions, api_keys(hash), user_positions(client_id UNIQUE per user), watchlist, alert_rules, alert_deliveries, push_subscriptions, wallets(address PK, source, name, labels[], discovered_by, tier, perp_equity_usd, has_perp, has_position, fills_empty_terminal, first_seen, last_seen), wallet_optout, feature_flags, audit_log, analytics_events(event, path, ref, ts)`。派生：`Wallet.followers` = watchlist 中该地址计数；`Wallet.style_tags` 移植 hlens_V3 `style-tagger`（whale/veteran/high_freq/stable/concentrated，high_freq 用于剔除做市商）；钱包评分移植 hlens_V4 `backtest/pit.py`（PIT 六维，含无前视断言），**不用** V3 `grade-calculator`。分层依据是 `perp_equity_usd`（`clearinghouseState`），不是榜单 `account_value`（含现货，实测偏差可达四个数量级）；`fills_empty_terminal=true` 的地址不再重试 fills。 |
容量：正常日增约 1.5 GB 压缩前、约 0.3 GB 压缩后；年增约 110 GB 含 fills 导入；两台各需 ≥ 200 GB 盘（S0 核实）。

## 4. 服务设计

### 4.1 采集（东京）
- **WS 优先**：Binance `!markPrice@arr@1s`（全市场标记价与费率）、`!forceOrder@arr`；Bybit `tickers` + `allLiquidation`；OKX `mark-price` + `liquidation-orders`；HL `allMids` + `trades`（大额发现）。心跳、指数退避重连（1→60s）、序号与时间校验、断线后按 `ingest_watermark` 用 REST 补齐。
- **REST 全市场端点**：Bybit `/v5/market/tickers?category=linear`、OKX `/market/tickers?instType=SWAP`、Binance `/fapi/v1/premiumIndex`（无 symbol 参数返回全部）、OI 用各所批量端点或按币每分钟；多空比逐币 5 分钟。
- **限速预算（`venues.yaml`，取官方上限 40%）**：Binance 2400 权重/分 → 预算 960（全市场 premiumIndex 10 + OI 300 币 × 1/min × 1 = 300 + 多空比 300 × 3 kinds / 5min × 1 ≈ 180 + K 线补齐预留 200 ≈ 690 ✓；不采集盘口深度）；宏观：CoinGecko `global` 与市值每 10 分钟 2 次、DefiLlama 与 F&G 每小时；Bybit 120/min → 48；OKX 20/2s → 8/2s；HL 1200 权重/分 → 600（`metaAndAssetCtxs` 20/min + hot 200 钱包 `clearinghouseState` 2 × 200 = 400 + warm 800 / 10 min × 2 = 160 = 580 ✓；fills 不走 REST）。
- **HL fills**：WS `userFills` 订阅 hot 200（WS 上限 1000 订阅 / 100 连接，留余量；WS 不消耗 /info 权重）；warm 钱包每 10 分钟 `userFillsByTime`（权重 20，≤ 20 个/分钟）。断线缺口用 `userFillsByTime`（old→new）+ 复合游标（时间 + 该毫秒已见 tid 集合，语义抄 hlens-hub `wallet_cursor`），**不与 `userFills`（new→old）混用**。
- **HL 限速的三条实测事实（来自 hlens-hub）**：① 真瓶颈是请求数不是权重：`max_inflight` 硬顶 10，超过撞连接限速器吞吐反降；单出口约 200 请求/分。② 两种 429 处置相反：响应体 JSON null = 权重限速 → 退避权重；nginx HTML = 连接限速 → 降并发。③ 预算不是常数：AIMD，撞 429 砍到 75% 并冻结 1 小时；`venues.yaml` 的 1200 是记账口径。**东京出口 IP 必须与 hub 任一 worker 不同**，否则两套限速器互不知情会超发。
- 每源熔断：连续 5 次失败停 5 分钟；写 `source_health`；`venues.yaml` 维护每所域名列表（生产用规范域名，非美 VPS）。
- 符号上下架、费率周期变更（Binance/Bybit/Bitget 有 1h/4h 符号）由 `instruments` 每小时刷新驱动。

### 4.2 大户引擎（东京）
- 候选池：排行榜每小时（种子，落库 `wallets.discovered_by=leaderboard`）+ WS `trades` 单笔 ≥ 100 万美元地址 + 用户关注。分层 hot（≥ 10 万美元仓位或关注）1m、warm 10m、cold 1h。
- 事件以 **fills 为权威源**（`dir` 与 `start_position` 判定 open/add/reduce/close/flip；同一 `oid` 的多笔成交合成一个事件），快照只做对账；`near_liq` 由 `hl_positions` 距强平 < 3% 触发，距离用 `calcLiqDistance`（markPx = |notional / size|，移植 hlens_V3）且必须 `liq_px_reliable=true`；`liquidated` 由 `fills.liquidation`。历史（导入）与实时跑同一套代码。
- 回填：事件后 1h/4h/24h 用 `klines`（`ret_ref_venue` 记录参考所）。
- OKX 带单：v1.1；同一事件模型 `source=okx`。

### 4.3 分析（东京）
- `prism_builder` 每分钟；`state_engine` 每根 K 线收盘（`confirmed` 需 2 根）；`sentence_engine`（模板 + 优先级 + 矛盾消解 + 分位）；`context_builder` 前 50 币每分钟、其余 5 分钟；`levels_model`；`cascade_detector`；`event_builder`；`scorecard`。
- **清算价公式**：多头 `P = E × (1 − 1/L) / (1 − MMR)`，空头 `P = E × (1 + 1/L) / (1 + MMR)`；MMR 取 `instruments.mmr_tiers` 分档（OKX `position-tiers`、Bybit `risk-limit` 公开；Binance 需签名则用 Bybit 档近似；HL ≈ 1/(2 × maxLev)）；1h OI 增量按 taker 净量符号拆多空；cross 仓位不建模；输出标 `model_estimated`。
- **级联**：5 分钟同向爆仓 ≥ 该币 30 天 5 分钟窗口 p99 且价格移动 ≥ 1%。
- **记分板**：分组来自 `scorecard_groups.yaml`（预注册，禁止事后挖）；块 bootstrap（块长 = 前瞻窗口）；按天聚簇；`hit_rate` 用 Wilson 区间；BH-FDR（q = 0.10）；n 上限抽样 5000、重采样 2000；`self_validated` 门槛 = n ≥ 100 且 q ≤ 0.10；n < 30 标 `insufficient`。每日一次，单核数小时内完成。
- **校准**：`calibrate_states.py`，v1 仅价格类特征（EMA/ATR/ADX/结构/量比/RSI），输出报告到 `docs/reports/`。

### 4.4 告警引擎（东京）
Redis Stream（`XADD MAXLEN ~ 100000`，consumer group，`XAUTOCLAIM` 回收孤儿）消费 `metric:*`、`state:*`、`whale:events`、`liq:cascade`；`dedupe_key = rule_id:bucket_ts`；冷却与每小时上限（免费 20、Pro 500）；投递适配器 Telegram（全局 30 msg/s、单 chat 1 msg/s 令牌桶）、Email（Resend 免费 100 封/天 → 摘要不走邮件）、Web Push；失败指数退避 3 次；延迟目标分级：WS 类 ≤ 10 秒、轮询类 ≤ 90 秒。全局熔断开关（告警风暴）。

### 4.5 API（新加坡）
FastAPI；读路径返回 Redis 预序列化 + br 压缩字节（不过 pydantic）；`Cache-Control: s-maxage` + Cloudflare Cache Rule（含查询串）；限流键 `CF-Connecting-IP`（校验对端在 CF 网段）+ API key；WS 网关订阅 Redis Pub/Sub，`seq` 用 Redis `INCR`，`liq` 250ms 批量；MCP `fastmcp` 挂 `/mcp`；`oasdiff` 校验。性能目标：CF 命中后 p95 < 150ms；源站 50 rps。

### 4.6 Web（新加坡）
见 01-FEATURES §6–§7。

### 4.7 导入（hub → 东京）
生产者是 hub 机器上的 **`hlens-hub`**（FastAPI 控制面 + 分布式 worker + DuckDB 转 Parquet），不是 GitHub 的 `hlens`。`apps/importer` 在 hub 上**只读**运行（`compose run --rm importer`，挂载 `/mnt/data1/hlens-hub/data/curated:ro`，DuckDB 限 2 线程 / 3 GB，不碰 hub 的 Postgres 控制面），按 `dt` 分区导出 CSV 流经 Tailscale `psql \copy` 到东京 staging，再 `ON CONFLICT DO NOTHING` 进主表。
- **全量**：`fill_history`（2023-02 → 2026-08，已去重、按成交日分区）、`position`、`account_snapshot`、`liquidation`、`leaderboard`（数值是字符串，`TRY_CAST`）。
- **持续增量**：`fill` 仍在增长且按**观测日**分区、与历史有约 1.1% 重叠；只取 `time_ms >` 水位的行，去重键 `(address, tid, content_hash)`，水位记 `ingest_watermark`。
- 字段映射：`px→price, sz→size, time_ms→ts, observed_at→ingest_ts, coin→symbol（经 instruments，过滤现货）, closed_pnl→closed_pnl_usd, fee+fee_token→fee_usd（按币种换算）, liquidation_user IS NOT NULL→liquidation, liquidation_method/liquidation_mark_px 原样, start_position/oid/crossed/hash 原样`。
- K 线：hub 的 `hl.kline` 实质只有 BTC；300 币历史回填走 Binance data.vision（移植 hlens_V4 `tools/grab_binance_klines.py`）；hub 的 `funding`/`kline` 是 V4 冻结历史（截至 2026-07-25），只作补充。
- 导入后跑同一 `whale-engine` 事件识别与回填；报告写 `docs/reports/import-YYYYMMDD.md`（行数、去重、时间覆盖、按 `exit_ip` 的来源分布）。首批预计 2–3 天。

## 5. 状态规则文件（v1，仅价格类）
```yaml
version: v1
params: { atr_len: 14, adx_len: 14, ema: [20, 50, 200], range_lookback: 48, pullback_atr: [0.5, 2.0], overheat_atr: 2.0, rsi_hot: 75, squeeze_atr_pctl: 0.15, breakout_bars: 4, false_breakout_h: 4, confirm_bars: 2 }
order: [false_breakout, breakout_pending, squeeze, up_overheated, up_exhaustion, up_pullback_shallow, up_impulse, down_exhaustion, down_bounce_shallow, down_impulse, range_top, range_bottom, range_mid]
states:
  up_pullback_shallow:
    all: [ {f: ema20_gt_ema50}, {f: close_gt_ema50}, {f: higher_low_intact}, {f: drawdown_atr, between: [0.5, 2.0]}, {f: pullback_vol_ratio, lt: 1.0} ]
  # up_pullback_leveraged / down_bounce_leveraged / down_capitulation 需要 OI、爆仓、CVD → state_rules.v2（自有数据满 90 天）
```

## 6. 测试
unit（适配器录制快照、指标、状态规则、句子引擎）· golden（合成输入 50 句 + 30 状态序列，`freeze_time`，固定 `rules_version`）· contract（schemathesis + examples）· integration（compose 全栈灌样本，断言 SSR 含答案句）· 前端（Playwright 关键路径、axe、Lighthouse CI、size-limit）· 性能（k6：CF 未命中 50 rps 源站 p95 < 300ms）。DoD：类型生成无 diff、lint 与测试绿、变更日志、文档更新、状态页无新红。

## 7. CI/CD
PR：lint + gen 无 diff + 测试 + 构建镜像。main：推 GHCR → SSH 部署（东京先、新加坡后）；init 容器跑迁移；健康检查后切换；`deploy.sh --rollback`。staging = 新加坡 `compose.staging.yml`（独立 schema）。

## 8. 部署与高可用
服务清单、端口、卷、健康检查、限额与 profiles 以 `05-DOCKER.md` 为准；本节只保留拓扑与切换语义。
- 东京：postgres 主、redis、collector、whale-engine、analytics、alert-engine、cloudflared 连接器 A、待命 api。
- 新加坡：postgres 只读副本（流复制 + WAL 归档到 R2，`archive_timeout=60`）、redis 副本、api、web、prometheus、grafana、cloudflared 连接器 B。
- 网络：Tailscale 内网；所有容器端口 `${TAILNET_IP}:port:port`；Docker 绕过 ufw，必须加 `DOCKER-USER` 链规则拒绝公网口进入容器；Redis `requirepass`（secret 文件）+ ACL；PG `hostssl` + scram，`pg_hba` 只放 tailnet 段。**出站**：容器共用宿主出口 IP（预算按宿主机算），不走 Tailscale exit node；东京出口 IP 不得与 hub worker 相同。
- 故障：东京挂 → `promote.sh` 在新加坡提升副本（先 fencing：关闭东京 PG、删除触发文件）、切 API 连接串、采集器在新加坡按 `compose.sg.yml --profile collect` 拉起；RPO ≤ 5 分钟、RTO 1 小时（人工）。新加坡挂 → 东京待命 api 接管（Tunnel 双连接器自动）。两处都挂 → Worker 回退 GitHub Pages 快照。
- 备份：pgBackRest 或 WAL-G → R2：周全量 + 日增量 + WAL；保留 4 份全量；每月恢复演练记录。

## 9. 监控与运行手册
指标：每源延迟与错误率、`ingest_watermark` 缺口、Stream 积压、WS 连接数、API p95、投递成功率、磁盘（70% 预警）、时钟偏移。告警到 raphael Telegram；Better Stack 心跳与死人开关（监控不自托管在被监控机上）。
运行手册（`docs/runbooks/`）：源故障 · 交易所封 IP · 符号上下架与改名 · 费率周期变更 · 时钟偏移 · 压缩任务卡死与回填解压 · CAGG 刷新积压 · Redis OOM · 磁盘满 · 数据库故障切换与脑裂检查 · 证书与隧道断 · 告警风暴 · API key 泄露 · Telegram/Tunnel 令牌轮换 · hub 导入重跑。

## 10. 安全
Tunnel 无公网端口；魔法链接 15 分钟；Cookie `HttpOnly Secure SameSite=Lax`；API key 哈希存储只显示一次，创建仅限 session；CSRF；pydantic 校验；参数化 SQL；Webhook（v1.1，本期不实现）SSRF 防护：解析后 pin IP、禁私网/回环/链路本地/元数据地址、禁跳转、仅 443、5s 超时、响应 ≤ 64 KB、HMAC 签名；Dependabot；秘钥不入库；审计日志；GDPR 导出与删除（30 天宽限）；隐私与条款页；钱包 opt-out 流程。

## 11. 编码规范
Python：ruff + mypy strict，pydantic v2，async 优先，函数 ≤ 60 行；TS：strict，无 any，组件 ≤ 200 行；命名与 YAML 一致；提交 `type(scope): summary`；一个 PR 一个目的。
