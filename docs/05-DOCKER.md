# hlens · Docker 落地方案（按真实代码，2026-09-11）

> 前提：`04-ARCHITECTURE-REVIEW.md` 的结论。CryptoPlus 自己的服务容器化；`hlens-hub` 与 GitHub `hlens` 保持原样在 hub 机器上运行，不进本方案的 compose。两台 VPS（东京、新加坡）的规格待核实，资源限制按 4 vCPU / 8 GB 假设，S0 核实后改数。

## 1. 硬规则（写进 compose 与 CI）

1. **端口只绑 tailnet**：所有 `ports` 写 `${TAILNET_IP}:port:port`；`TAILNET_IP` 来自 `.env`。Docker 的 DOCKER-USER 链绕过 ufw，必须加规则：`iptables -I DOCKER-USER -i eth0 -j DROP`（公网口一律拒绝进入容器）。
2. **出站 IP**：交易所限速按公网出口 IP 记账。同一宿主机上的 collector、whale-engine 共用宿主出口，这是预期的（预算在 `venues.yaml` 里按宿主机算）；**东京的出口 IP 不得与 hub 任一 worker 相同**，否则两套限速器互不知情。容器不使用 Tailscale exit node（`forbid_tailnet_egress`）。
3. **密钥**：compose `secrets:`（文件）+ 应用读 `*_FILE`；禁止密码进 `command`、`healthcheck`、`environment`。Tunnel 用 credentials 文件。
4. **镜像**：`python:3.12-slim` + uv（不用 alpine，zstandard/orjson 要编译）；多阶段；非 root；每个镜像有 `HEALTHCHECK`；前端 `node:20-alpine` 三阶段 standalone，lockfile 必须提交。
5. **日志**：全局 `x-logging: json-file, max-size 10m, max-file 3`。免费 VPS 的盘会被日志吃满。
6. **资源限制**：单机 compose 用 `mem_limit` / `cpus`（不是 swarm 的 `deploy.resources`）。
7. **优雅停机**：采集类 `stop_grace_period: 120s`（防丢正在写的批）。
8. **依赖**：`depends_on: condition: service_healthy`，不是 started。
9. **迁移**：`migrate` 一次性 init 容器跑 Alembic（CAGG 用 autocommit）；API 与分析服务 `depends_on: migrate: service_completed_successfully`。
10. **构建上下文**：每个 app 目录各自一份 `.dockerignore`；`hlens` 相关包构建时排除 docs/ 与 test/（20 MB 文档）。

## 2. 东京 `deploy/compose.tokyo.yml`（数据产生地）

| 服务 | 镜像 / 来源 | 端口 | 卷 | 健康检查 | 依赖 | 限额 |
|---|---|---|---|---|---|---|
| `postgres` | `timescale/timescaledb-ha:pg16` | `${TAILNET_IP}:5432` | `pgdata`（bind 到真实盘）、`pgbackrest` | `pg_isready` | – | 2C / 3G |
| `migrate` | `apps/api` 镜像，`alembic upgrade head` | – | – | 退出码 | postgres healthy | – |
| `redis` | `redis:7-alpine`，`requirepass` 走 secret 文件 + ACL | `${TAILNET_IP}:6379` | `redisdata` | `redis-cli ping` | – | 1G maxmemory |
| `collector` | `apps/collector`（WS + REST；hlens-core adapters/ratelimit/preflight） | – | – | `/healthz`（最近 60 秒有写入） | postgres、redis、migrate | 1C / 1G |
| `whale-engine` | `apps/whale-engine`（HL WS userFills hot 200 + clearinghouseState + 事件） | – | – | `/healthz` | 同上 | 1C / 1G |
| `analytics` | `apps/analytics`（prism/state/sentence/context/levels/cascade/scorecard） | – | – | `/healthz` | 同上 | 1C / 2G |
| `alert-engine` | `apps/alert-engine` | – | – | `/healthz` | redis、migrate | 0.5C / 512M |
| `api-standby` | 与新加坡同 `apps/api` 镜像，`profiles: [standby]` | `${TAILNET_IP}:8000` | – | `GET /v1/status` | postgres | 1C / 1G |
| `cloudflared-a` | `cloudflare/cloudflared`，第二连接器 | – | creds secret | `tunnel info` | api-standby | 0.2C / 128M |
| `pgbackrest`（sidecar） | `pgbackrest` 镜像或 cron 容器：周全量 + 日增量 + WAL 归档 → R2 | – | `pgdata`（只读）、`pgbackrest` | 上次成功时间 | postgres | 0.5C / 512M |

## 3. 新加坡 `deploy/compose.sg.yml`（对外服务）

| 服务 | 镜像 / 来源 | 端口 | 卷 | 健康检查 | 依赖 | 限额 |
|---|---|---|---|---|---|---|
| `postgres-replica` | 同东京镜像，流复制自东京（tailnet） | `${TAILNET_IP}:5432` | `pgdata` | `pg_isready` | – | 2C / 3G |
| `redis-replica` | `redis:7-alpine` `replicaof` 东京 | `${TAILNET_IP}:6379` | – | `redis-cli ping` | – | 1G |
| `api` | `apps/api`（FastAPI + WS + MCP；读层 Redis 预序列化） | `${TAILNET_IP}:8000` | – | `GET /v1/status` | replica、redis healthy | 1C / 2G |
| `web` | `apps/web`（Next 15 standalone） | `${TAILNET_IP}:3000` | – | `GET /api/health` | api healthy | 1C / 1G |
| `caddy` | `caddy:2-alpine`（反代 api/web/WS，规则译自 smip nginx；限流键 `CF-Connecting-IP`） | `${TAILNET_IP}:8080` | `Caddyfile`、`caddy_data` | `caddy validate` | api、web | 0.3C / 256M |
| `cloudflared-b` | `cloudflare/cloudflared`，第一连接器 | – | creds secret | `tunnel info` | caddy | 0.2C / 128M |
| `snapshot` | `apps/snapshot`（改写自 `scripts/fetch.py`：读本地 API → `latest.json` schema 2 → push Pages 分支），`profiles: [cron]`，由宿主 cron 每 30 分钟 `compose run --rm snapshot` | – | tmpfs | 退出码 | api | 0.3C / 256M |
| `prometheus` | 官方镜像 | `${TAILNET_IP}:9090` | `promdata` | `/-/healthy` | – | 0.5C / 1G |
| `grafana` | 官方镜像 | `${TAILNET_IP}:3001` | `grafana` | `/api/health` | prometheus | 0.5C / 512M |
| `collector` | 同东京镜像，`profiles: [collect]`，仅故障切换时起 | – | – | `/healthz` | postgres（提升后） | 1C / 1G |

外部拨测（Better Stack）与死人开关不在任何一台机器上。

## 4. hub 机器（不在 CryptoPlus compose 内）

- `hlens-hub` 与 GitHub `hlens` 继续 systemd 运行。若将来容器化 `hlens`（可选），单独 `compose.hub.yml`：`hlens-recorder`、`hlens-poller`、`hlens-hl-addresses`（必须与前两者分进程但同出口 IP）、`hlens-hub`（修 systemd 的 `hlens hub` → `hub-serve`）；spool 卷必须 bind 到真实盘（探针文件检只读挂载）。
- `apps/importer` 以只读方式在 hub 上跑（`compose run --rm importer`，挂载 `/mnt/data1/hlens-hub/data/curated:ro`，DuckDB 限 2 线程 / 3 GB），经 Tailscale `psql \copy` 写东京 staging。不碰 hub 的 Postgres 控制面。

## 5. 故障切换与 profiles

- 东京挂：新加坡 `promote.sh`（fencing → `pg_ctl promote` → 切 API 连接串 → `compose --profile collect up -d collector whale-engine analytics alert-engine`）。RPO ≤ 5 分钟、RTO 1 小时。
- 新加坡挂：东京 `compose --profile standby up -d api-standby`；Tunnel 双连接器自动路由。
- 两处都挂：Cloudflare Worker 回退 GitHub Pages 快照。

## 6. CI 门禁（`.github/workflows/ci.yml`）

`redocly lint` · `oasdiff breaking` · `make gen` 无 diff · ruff/mypy/eslint/tsc · 单测（`-m "not live"`）· `docker compose -f deploy/compose.tokyo.yml config` 与 sg 同 · `hadolint` · 镜像非 root 检查 · `size-limit`。main 合并后推 GHCR，SSH 到东京再新加坡 `deploy.sh`（健康检查后切换，`--rollback` 回上一 tag）。

## 7. S0 前置核实清单

- 两台 VPS 的 CPU / 内存 / 磁盘 / 出口 IP / 是否已装 Docker 与 compose v2。
- 东京出口 IP 不在 hub 的 `exit_ip_budget` 表中。
- 免费 VPS 的回收政策与 Oracle 地域限制。
- Cloudflare 账号可建 Tunnel 与 R2。
