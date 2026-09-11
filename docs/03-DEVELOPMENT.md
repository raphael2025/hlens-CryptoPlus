# hlens · 开发与运维文档（v3.1）

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
packages/hlens-core  Python 共享：适配器、指标、状态规则、句子引擎、DB
packages/hlens-mcp   PyPI 包
packages/ui          前端共享组件
config/  state_rules.v1.yaml · sentence_templates.{zh,en}.yaml · venues.yaml · calendar_macro.json · scorecard_groups.yaml（预注册分组）· evidence.yaml（证据库，`listEvidence` 数据源）
docs/changelog.md                    # `listChangelog` 数据源（构建时解析）
db/migrations（Alembic；CAGG 用 autocommit 迁移）
deploy/  compose.tokyo.yml · compose.sg.yml · compose.staging.yml · Caddyfile · cloudflared/ · prometheus/ · grafana/ · scripts/{deploy,backup,restore,failover,promote}.sh
tests/{unit,contract,golden,integration}
scripts/fetch.py · data/            # v0.1 静态快照层（降级模式）
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
| `hl_wallet_state(ts, address, account_value, margin_used)` | hot 1m / warm 10m | 1d / 1y | 0.4M 行/天 | |
| `hl_positions(ts, address, coin, side, size, notional, entry, liq, lev, upnl, is_cross, liq_px_reliable)` | 同上 | 1d / 1y | 1M 行/天 | 对账用；事件以 fills 为准 |
| `hl_fills(tid PK, address, ts, symbol, side, dir, price, size, notional_usd, fee_usd, closed_pnl_usd, liquidation, hash)` | WS + 导入 | 7d / 永久 | 导入 5 亿行 ≈ 40 GB 压缩后 | UNIQUE(address, tid) |
| `whale_events(id, ts, address, source, coin, type, side, size_delta, notional_delta, px, ret_1h, ret_4h, ret_24h, ret_ref_venue, filled_at)` | 事件 | – / 永久 | – | UNIQUE(address, coin, type, ts_bucket) |
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
| 业务表 | `users, sessions, api_keys(hash), user_positions(client_id UNIQUE per user), watchlist, alert_rules, alert_deliveries, push_subscriptions, wallets(address PK, source, name, labels[], discovered_by, tier, first_seen, last_seen), wallet_optout, feature_flags, audit_log, analytics_events(event, path, ref, ts)`。派生：`Wallet.followers` = watchlist 中该地址计数；`Wallet.style_tags` 由 `whale_events` 每日派生（持仓时长、杠杆偏好、方向偏好）。 |
容量：正常日增约 1.5 GB 压缩前、约 0.3 GB 压缩后；年增约 110 GB 含 fills 导入；两台各需 ≥ 200 GB 盘（S0 核实）。

## 4. 服务设计

### 4.1 采集（东京）
- **WS 优先**：Binance `!markPrice@arr@1s`（全市场标记价与费率）、`!forceOrder@arr`；Bybit `tickers` + `allLiquidation`；OKX `mark-price` + `liquidation-orders`；HL `allMids` + `trades`（大额发现）。心跳、指数退避重连（1→60s）、序号与时间校验、断线后按 `ingest_watermark` 用 REST 补齐。
- **REST 全市场端点**：Bybit `/v5/market/tickers?category=linear`、OKX `/market/tickers?instType=SWAP`、Binance `/fapi/v1/premiumIndex`（无 symbol 参数返回全部）、OI 用各所批量端点或按币每分钟；多空比逐币 5 分钟。
- **限速预算（`venues.yaml`，取官方上限 40%）**：Binance 2400 权重/分 → 预算 960（全市场 premiumIndex 10 + OI 300 币 × 1/min × 1 = 300 + 多空比 300 × 3 kinds / 5min × 1 ≈ 180 + K 线补齐预留 200 ≈ 690 ✓；不采集盘口深度）；宏观：CoinGecko `global` 与市值每 10 分钟 2 次、DefiLlama 与 F&G 每小时；Bybit 120/min → 48；OKX 20/2s → 8/2s；HL 1200 权重/分 → 600（`metaAndAssetCtxs` 20/min + hot 200 钱包 `clearinghouseState` 2 × 200 = 400 + warm 800 / 10 min × 2 = 160 = 580 ✓；fills 不走 REST）。
- **HL fills**：WS `userFills` 订阅 hot 200（WS 上限 1000 订阅 / 100 连接，留余量）；warm 钱包每 10 分钟 `userFillsByTime`（权重 20，≤ 20 个/分钟）。
- 每源熔断：连续 5 次失败停 5 分钟；写 `source_health`；`venues.yaml` 维护每所域名列表（生产用规范域名，非美 VPS）。
- 符号上下架、费率周期变更（Binance/Bybit/Bitget 有 1h/4h 符号）由 `instruments` 每小时刷新驱动。

### 4.2 大户引擎（东京）
- 候选池：排行榜每小时（种子，落库 `wallets.discovered_by=leaderboard`）+ WS `trades` 单笔 ≥ 100 万美元地址 + 用户关注。分层 hot（≥ 10 万美元仓位或关注）1m、warm 10m、cold 1h。
- 事件以 **fills 为权威源**（`dir` 与 `start_position` 判定 open/add/reduce/close/flip），快照只做对账；`near_liq` 由 `hl_positions` 距强平 < 3% 触发；`liquidated` 由 fills.liquidation。
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
`apps/importer`：在 hub 上用 DuckDB 读 `curated/hl/{fill_history,fill,position,liquidation,leaderboard,account_snapshot}`，按 `dt` 分区导出为 CSV 流经 Tailscale `psql \copy` 到东京 staging，再 `ON CONFLICT DO NOTHING` 进主表；字段映射：`px→price, sz→size, time_ms→ts, coin→symbol(经 instruments), closed_pnl→closed_pnl_usd, liquidation_user IS NOT NULL→liquidation`；导入后跑同一 `whale-engine` 事件识别与回填；导入报告写 `docs/reports/import-YYYYMMDD.md`（行数、去重、时间覆盖）。同时导入 `funding`（多所费率历史）与 `kline`（回填事件后收益的参考价）。首批：fill_history 全量 + fill + position 42 天 + funding + kline，预计 2–3 天完成。

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
- 东京：postgres 主、redis、collector、whale-engine、analytics、alert-engine、cloudflared 连接器 A、待命 api。
- 新加坡：postgres 只读副本（流复制 + WAL 归档到 R2，`archive_timeout=60`）、redis 副本、api、web、prometheus、grafana、cloudflared 连接器 B。
- 网络：Tailscale 内网；所有容器端口 `-p <tailnet_ip>:port:port`；Redis `requirepass` + ACL；PG `hostssl` + scram，`pg_hba` 只放 tailnet 段；VPS 防火墙拒绝入站，Docker 规则复核。
- 故障：东京挂 → `promote.sh` 在新加坡提升副本（先 fencing：关闭东京 PG、删除触发文件）、切 API 连接串、采集器在新加坡按 `compose.sg.yml --profile collect` 拉起；RPO ≤ 5 分钟、RTO 1 小时（人工）。新加坡挂 → 东京待命 api 接管（Tunnel 双连接器自动）。两处都挂 → Worker 回退 GitHub Pages 快照。
- 备份：pgBackRest 或 WAL-G → R2：周全量 + 日增量 + WAL；保留 4 份全量；每月恢复演练记录。

## 9. 监控与运行手册
指标：每源延迟与错误率、`ingest_watermark` 缺口、Stream 积压、WS 连接数、API p95、投递成功率、磁盘（70% 预警）、时钟偏移。告警到 raphael Telegram；Better Stack 心跳与死人开关（监控不自托管在被监控机上）。
运行手册（`docs/runbooks/`）：源故障 · 交易所封 IP · 符号上下架与改名 · 费率周期变更 · 时钟偏移 · 压缩任务卡死与回填解压 · CAGG 刷新积压 · Redis OOM · 磁盘满 · 数据库故障切换与脑裂检查 · 证书与隧道断 · 告警风暴 · API key 泄露 · Telegram/Tunnel 令牌轮换 · hub 导入重跑。

## 10. 安全
Tunnel 无公网端口；魔法链接 15 分钟；Cookie `HttpOnly Secure SameSite=Lax`；API key 哈希存储只显示一次，创建仅限 session；CSRF；pydantic 校验；参数化 SQL；Webhook（v1.1，本期不实现）SSRF 防护：解析后 pin IP、禁私网/回环/链路本地/元数据地址、禁跳转、仅 443、5s 超时、响应 ≤ 64 KB、HMAC 签名；Dependabot；秘钥不入库；审计日志；GDPR 导出与删除（30 天宽限）；隐私与条款页；钱包 opt-out 流程。

## 11. 编码规范
Python：ruff + mypy strict，pydantic v2，async 优先，函数 ≤ 60 行；TS：strict，无 any，组件 ≤ 200 行；命名与 YAML 一致；提交 `type(scope): summary`；一个 PR 一个目的。
