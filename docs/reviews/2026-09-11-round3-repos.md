# 审核记录 · 第三轮：仓库级架构审查（2026-09-11）

三个并行审查（Opus），对象是 raphael 的真实代码，不是 CryptoPlus 文档。结论已合并进 `04-ARCHITECTURE-REVIEW.md` 与 `05-DOCKER.md`，并修正 `00/02/03`。

## 数据平台组（`hlens`、`hlens-crypto`）
判断：`hlens` 是成熟可容器化的原始档案采集系统（无 DB、内容寻址 chunk、87 个离线测试、CI 四门禁）；`hlens-crypto` 不是代码仓库；最大风险是文档误认 `hlens` 产 Parquet，以及限速按出口 IP 记账在 Docker NAT 下会被静默破坏。
关键发现：systemd `hlens-hub.service` 的 `hlens hub` 命令不存在；两套 `NodeConfig`；`offline`/`hub` extras 零引用；`ARCHITECTURE-BASELINE.md` 明言容器可选；四台云机装了 docker 但零容器。
复用：contract、assertions（QA）、catalog + 门禁、`hyperliquid/budget.py`、node/preflight、symbolmap；venues 只抄常数；spool/shipper/hub 不复用不动。

## Hyperliquid 采集组（`hlens-hub`、`hlens_V3`、`hlens_V4`）
判断：`hlens-hub` 是把 HL 限速踩透并写进注释的分布式控制面（纯 REST 单槽、零 WS、零 Docker、配额按出口 IP + AIMD）；与 CryptoPlus 的真实交集只有历史数据；`hl_fills` 用 `tid` 做主键会静默删 207,635 条 tid=0 的真数据。
关键发现：submit 与 claim 合并、5 类任务、`wallet_cursor` 复合游标、`collection_log` 是配额唯一依据、单出口天花板约 200 请求/分、`max_inflight=10` 硬顶、两种 429 处置相反、`liq_px_reliable` 门、榜单 `account_value` 含现货不可作永续分层、`fills_empty_terminal` 终态、`hl.kline` 实质只有 BTC、V4 `hl_client` 分页 `max(time)+1` 会跳过边界毫秒、V3 `grade-calculator` 会前视、`"liquidation": null` 键陷阱。**安全**：`hub.env` 明文 Tailscale OAuth secret、admin 端点无鉴权绑 0.0.0.0。
决策：数据走 (a) 只读 Parquet（全量 + 增量），引擎走 (c) 东京重写，否决 (b) 容器化 hub。

## 服务与部署组（`smip`、`hlens-dashboard`、`hlens-CryptoPlus`）
判断：能复用的只有 smip 的 compose/前端构建骨架与三层目录、hlens-dashboard 的 adapter + 缓存 + 测试基座、fetch.py 的适配函数；smip 的表结构、collector、前端数据层不可复用；smip 的 Docker 是公网直连假设的产物，与 tailnet + Tunnel 模型在端口、限流键、密钥三处冲突。
关键发现：smip Dockerfile 单阶段 root 无 HEALTHCHECK、`.dockerignore` 位置无效、端口绑 0.0.0.0、Redis 密码进 command、nginx 限流键在 Tunnel 后失效、前端无 lockfile；hlens-dashboard/web 已是 Next 15 + React 19 + TanStack Query 5 + lightweight-charts；hlens-dashboard 偷读隔壁仓库 `.env`。
服务清单：东京 9、新加坡 10；03-DEV 漏了 migrate、caddy、snapshot、api-standby profile、logging 限额、资源限制写法。

## 未采纳
- 数据平台组建议把 `hlens` 五个服务写成 `compose.hub.yml` 立即容器化：改为"可选、将来"，因 hub 上的生产采集不应在 CryptoPlus 上线前被改动。
