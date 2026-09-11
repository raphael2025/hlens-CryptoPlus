# hlens · 开发与运维文档（v3）

## 1. 仓库结构（单仓 monorepo）

```
hlens-CryptoPlus/
├─ api/openapi.yaml                 # 契约唯一真相源
├─ apps/
│  ├─ api/                          # FastAPI：REST + WS + MCP
│  │  └─ hlens_api/{main.py, routers/, schemas/generated.py, deps.py, ws.py, mcp.py}
│  ├─ web/                          # Next.js 15 + TS
│  │  └─ src/{app/, components/, api/types.ts (生成), lib/, i18n/}
│  ├─ collector-rest/               # 轮询采集
│  ├─ collector-ws/                 # 常驻 WebSocket 采集
│  ├─ whale-engine/                 # 候选池、fills、事件、回填
│  ├─ analytics/                    # 状态、句子、聚合、热力图、记分板、校准脚本
│  └─ alert-engine/
├─ packages/
│  ├─ hlens-core/                   # Python 共享：交易所适配器、指标、状态规则、句子引擎、DB 访问
│  ├─ hlens-mcp/                    # 发布到 PyPI 的 MCP 客户端包
│  └─ ui/                           # 前端共享组件（SpectrumRow、PrismChart…）
├─ config/
│  ├─ state_rules.v1.yaml
│  ├─ sentence_templates.{zh,en}.yaml
│  ├─ venues.yaml                   # 每所限速、域名、镜像
│  └─ calendar_macro.json
├─ db/migrations/                   # Alembic
├─ deploy/
│  ├─ compose.sg.yml  compose.tokyo.yml  compose.staging.yml
│  ├─ Caddyfile  cloudflared/  grafana/  prometheus/
│  └─ scripts/{deploy.sh, backup.sh, restore.sh, failover.sh}
├─ tests/{unit, contract, golden, integration}
├─ scripts/fetch.py  data/          # v0.1 静态快照层（保留）
└─ docs/
```

## 2. 本地开发

```bash
# 依赖：Docker、Python 3.12（uv）、Node 20（pnpm）
make up            # postgres+timescale, redis, api, web（compose.dev）
make seed          # 导入 tests/fixtures 的 24h 样本数据
make gen           # 从 openapi.yaml 生成 pydantic 与 TS 类型
make test          # unit + contract + golden
make calibrate     # analytics/calibrate_states.py → docs/reports/
```
环境变量见 `.env.example`；本地不需要任何交易所 key。

## 3. 数据模型（DDL 摘要，TimescaleDB）

```sql
-- 时序（hypertable，chunk 1 天，压缩 7 天后，保留策略见注释）
ticks(ts, venue, symbol, mark, index, last, bid, ask)                       -- 5s，保留 7d
funding(ts, venue, symbol, rate, interval_h, rate_8h, next_ts, predicted)  -- 1m，保留 2y
open_interest(ts, venue, symbol, oi_base, oi_usd)                          -- 1m，保留 2y
ls_ratio(ts, venue, symbol, kind, long_share)                              -- 5m，保留 2y
klines(ts, venue, symbol, tf, o,h,l,c,v, taker_buy_v)                       -- 1m/1h/1d，保留 1m:30d 1h:2y 1d:∞
book_depth(ts, venue, symbol, bid1, ask1, bid2, ask2)                      -- 1m，保留 90d
liquidations(ts, venue, symbol, side, price, qty, usd, cascade_id)         -- 实时，保留 2y
options_snapshot(ts, currency, expiry, strike, type, oi, iv, mark, vol)    -- 5m，保留 1y
basis(ts, venue, symbol, expiry, annualized)                               -- 1m，保留 2y
macro(ts, key, value)                                                      -- 1h/1d，保留 ∞
hl_asset_ctx(ts, coin, mark, funding_1h, oi, premium, vlm)                 -- 1m，保留 2y
hl_wallet_state(ts, address, account_value, margin_used)                   -- 1m，保留 1y
hl_positions(ts, address, coin, side, size, notional, entry, liq, lev, upnl)-- 1m，保留 1y
coin_prism_1m(ts, symbol, price, funding_8h, oi_total, retail, top, taker, whale_share, whale_n, crowding, liq24_l, liq24_s)
coin_state_1h(ts, symbol, tf, state, rules_version, hits jsonb)
sentence_log(ts, symbol, page, template_id, inputs_hash, zh, en)
-- 关系表
hl_fills(fill_id PK, address, ts, coin, side, px, sz, fee, closed_pnl, dir, liquidation)
whale_events(id PK, ts, address, source, coin, type, side, notional_delta, px, ret_1h, ret_4h, ret_24h, filled_at)
wallets(address PK, source, name, labels[], first_seen, last_seen, tier)   -- tier: hot(60s)/warm(10m)/cold(1h)
liq_cascades(id PK, symbol, side, start_ts, end_ts, usd, price_move_pct)
market_events(id PK, ts, type, symbol, severity, numbers jsonb, template_id, zh, en)
scorecard_daily(date, group, horizon, n, median, p25, p75, hit_rate, ci_low, ci_high)
users, sessions, api_keys, user_positions, watchlist, alert_rules, alert_deliveries, subscriptions, feature_flags, audit_log, source_health
```
连续聚合：`funding_1h`、`oi_1h`、`liq_1h`、`prism_1h`（由 `coin_prism_1m` 聚合）。

## 4. 服务设计

### 4.1 collector-rest（东京）
任务表驱动：`(venue, kind, symbols[], interval)`；每所一个 `RateBudget`（令牌桶，取官方上限的 40%）；适配器接口 `fetch_tickers / fetch_funding / fetch_oi / fetch_ls / fetch_klines / fetch_depth`；返回统一 dataclass；写入用 `COPY` 批量；每次任务写 `source_health`。熔断：连续 5 次失败停 5 分钟；域名镜像列表在 `venues.yaml`（Binance 优先 `www.binance.com`）。

### 4.2 collector-ws（东京）
每所一个连接管理器：订阅爆仓流与标记价；心跳、重连（指数退避 1→60s）、序号/时间校验；消息经 Redis Stream `liq:raw` 进入 analytics 级联检测与 API 推送。

### 4.3 whale-engine（东京）
- 候选：每小时排行榜前 1000；`trades` 流中 ≥ 100 万美元成交的地址；用户关注。分层：hot（有 ≥ 10 万美元仓位或关注）60s，warm 10m，cold 1h。
- 状态：`clearinghouseState` → `hl_wallet_state` + `hl_positions`；与上一快照 diff 生成 `whale_events`（open/add/reduce/close/flip；near_liq 当距强平 < 3%；liquidated 来自 fills.liquidation）。
- 回填：事件后 1h/4h/24h 用 `klines` 填 `ret_*`。
- OKX 带单：P3 加入，同一事件模型，`source=okx`。

### 4.4 analytics（东京，部分定时任务在新加坡）
- `prism_builder`：每分钟合成 `coin_prism_1m`。
- `state_engine`：每根 K 线收盘按 `state_rules.v{n}.yaml` 判定三周期状态，写 `coin_state_1h`；状态变化发 Redis `state:{symbol}`。
- `sentence_engine`：模板 + 优先级 + 矛盾消解（见功能文档 §1）；输出写 `sentence_log`。
- `context_builder`：EMA/ADX/ATR/VWAP/POC/CVD/相关性/OI 象限/突破检测，每分钟前 50 币、每 5 分钟其余。
- `levels_model`：清算堆模型：对每个 1h OI 增量按杠杆权重分配到对应清算价（多头 = 价 × (1 − 1/lev + 维持保证金)，空头对称），随后按已发生价格路径"消耗"，输出密度网格。
- `cascade_detector`：5 分钟窗口同向爆仓 ≥ 2000 万美元且价格移动 ≥ 1%。
- `event_builder`：生成 `market_events`（价格 / 爆仓 / 费率 / 大户 / 拥挤 / 状态）。
- `scorecard`：每日计算各分组前瞻收益分布与 bootstrap CI；n < 30 标 `insufficient`；n ≥ 100 且 CI 不含零升 `self_validated`。
- `calibrate_states.py`：见功能文档 §2。

### 4.5 alert-engine（新加坡）
Redis Stream 消费 `metric:*`、`state:*`、`whale:events`、`liq:cascade`；规则索引按 scope/target 分桶；冷却与每小时上限；投递适配器 telegram/email/push/webhook；失败指数退避 3 次；写 `alert_deliveries`。

### 4.6 api（新加坡）
FastAPI；路由返回生成的 pydantic 模型；读多用 Redis 缓存（key = 路径 + 参数，TTL = `x-hlens-cache`）；WS 网关订阅 Redis Pub/Sub 转发；MCP 用 `fastmcp` 挂载 `/mcp`；限流用 Redis 令牌桶；`/openapi.json` 与 YAML 在 CI diff。

### 4.7 web（新加坡）
Next.js App Router；页面 SSR 调 API（服务端直连内网）；客户端 `openapi-fetch` + TanStack Query；WS 用单例 store（Zustand）；图表懒加载；OG 图 `/og/*` 用 Satori；PWA 与 Web Push。

## 5. 状态规则文件示例

```yaml
# config/state_rules.v1.yaml
version: v1
params: { atr_len: 14, adx_len: 14, ema: [20, 50, 200], range_lookback: 48, pullback_atr: [0.5, 2.0], overheat_atr: 2.0, rsi_hot: 75, apr_hot: 30, crowd_hot: 0.4, squeeze_atr_pctl: 0.15, cap_liq_pctl: 0.95, breakout_bars: 4, false_breakout_h: 4 }
order: [down_capitulation, false_breakout, breakout_pending, squeeze, up_overheated, up_exhaustion, up_risky_pullback, up_healthy_pullback, up_impulse, down_exhaustion, down_risky_bounce, down_healthy_bounce, down_impulse, range_top, range_bottom, range_mid]
states:
  up_healthy_pullback:
    all:
      - { f: ema20_gt_ema50 }
      - { f: close_gt_ema50 }
      - { f: higher_low_intact }
      - { f: drawdown_atr, between: [0.5, 2.0] }
      - { f: oi_chg_since_high, lt: 0 }
      - { f: pullback_vol_ratio, lt: 1.0 }
      - { f: spot_cvd_chg_since_high, gte: -0.5 }   # 以 24h CVD 标准差为单位
  up_risky_pullback:
    all:
      - { f: ema20_gt_ema50 }
      - { f: close_gt_ema50 }
      - { f: drawdown_atr, between: [0.5, 3.0] }
    any:
      - { f: oi_chg_since_high, gt: 0.02 }
      - { f: long_liq_ratio_24h, gt: 2.0 }
      - { f: pullback_vol_ratio, gt: 1.3 }
      - { f: spot_cvd_chg_since_high, lt: -1.0 }
  # …其余状态同构
```

## 6. 测试策略
- unit：适配器解析（录制响应快照）、指标、状态规则（构造 K 线序列断言状态）、句子引擎（模板与矛盾消解）。
- golden：`tests/golden/sentences/`（50 快照）、`tests/golden/states/`（30 序列）。
- contract：schemathesis 对运行中的 API 按 YAML 生成请求；固定样例校验。
- integration：compose 起全栈，灌 24h 样本，断言页面 SSR 输出含答案句。
- 性能：k6 对 `/coins`、`/coins/BTC/prism` 200 rps p95 < 150ms。
- 完成定义（DoD）：类型生成无 diff、测试绿、变更日志、文档更新、状态页无新红。

## 7. CI/CD
PR：lint（ruff、eslint、tsc）、`make gen` 无 diff、测试、构建镜像。main：推镜像到 GHCR → SSH 到新加坡与东京执行 `deploy.sh`（`compose pull && up -d`，健康检查后切换）；数据库迁移在 API 启动前由 init 容器执行；staging 为新加坡上的 `compose.staging.yml`（独立数据库 schema）。回滚：`deploy.sh --rollback`（上一镜像 tag）。

## 8. 部署拓扑
- 新加坡：postgres（主）、redis、api、web、alert-engine、prometheus、grafana、caddy、cloudflared。
- 东京：collector-rest、collector-ws、whale-engine、analytics、postgres（流复制备）、backup（每日 pg_dump → R2）。
- 两机 Tailscale 内网；东京写主库走内网；主库故障：`failover.sh` 提升东京为主并切 API 连接串（RTO 15 分钟，RPO ≤ 1 分钟）。
- 对外只经 Cloudflare Tunnel；VPS 防火墙拒绝所有入站。

## 9. 监控与运行手册
- 指标：每源延迟/错误率、Redis 积压、WS 连接数、API p95、告警投递成功率、磁盘。告警到 raphael 的 Telegram。
- 运行手册（`docs/runbooks/`）：源故障、数据库故障切换、磁盘满、限速被封、证书/隧道断、告警风暴（全局熔断开关）。
- 备份：每日全量 + WAL；每月恢复演练并记录。

## 10. 安全
Tunnel 无公网端口；魔法链接 15 分钟；Cookie `HttpOnly Secure SameSite=Lax`；API key 哈希存储只显示一次；CSRF；pydantic 校验；参数化 SQL；Dependabot；秘钥不入库；审计日志；GDPR 导出与删除；隐私与条款页。

## 11. 编码规范
Python：ruff + mypy strict，pydantic v2，async 优先，函数 ≤ 60 行；TS：strict，无 any，组件 ≤ 200 行；命名与 YAML 一致；提交信息 `type(scope): summary`；每个 PR 一个目的。
