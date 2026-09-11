# hlens · API 契约文档（v3）

> **唯一真相源是 `api/openapi.yaml`**。后端 pydantic 模型与前端 TypeScript 类型都从它生成；任何字段改动先改 YAML，再生成，再实现。本文解释约定，不重复字段。

## 1. 一致性机制

| 环节 | 做法 |
|---|---|
| 定义 | `api/openapi.yaml`（OpenAPI 3.1）。字段命名 `snake_case`；时间戳 `ts` 为毫秒整数；金额 `*_usd` 为浮点美元；比例 `*_share` 为 0–1；费率为小数（0.0001 = 0.01%）。 |
| 后端 | `datamodel-codegen` 生成 `apps/api/hlens_api/schemas/generated.py`（pydantic v2）；路由返回这些模型；FastAPI 导出的 `/openapi.json` 在 CI 中与 YAML 做 diff，不一致即失败。 |
| 前端 | `openapi-typescript` 生成 `apps/web/src/api/types.ts`；`openapi-fetch` 作为客户端；禁止手写接口类型。 |
| 契约测试 | `tests/contract/`：对每个端点用录制的真实响应做 schema 校验（schemathesis 随机 + 固定样例）。 |
| 版本 | 路径前缀 `/v1`；只加不删；破坏性变更开 `/v2` 并保留 `/v1` 至少 6 个月；`x-hlens-since` 标注字段引入版本。 |
| 变更流程 | PR 修改 YAML → CI 生成两端代码并跑契约测试 → 变更日志自动追加。 |

## 2. 通用约定

- **Base**：`https://api.hlens.xyz/v1`（静态快照层兼容：`https://raphael2025.github.io/hlens-CryptoPlus/data/latest.json` 的字段是 `Prism` 的子集）。
- **认证**：公共 GET 无需认证；`/me/*` 需 `Authorization: Bearer <session|api_key>`。
- **限流**：匿名 30/min，免费 60/min，Pro 600/min；响应头 `X-RateLimit-Limit/Remaining/Reset`；超限 `429` + `Retry-After`。
- **缓存**：公共 GET 带 `Cache-Control: public, max-age=N` 与 `ETag`；N 见各端点 `x-hlens-cache`。
- **错误**：统一 `Error{code, message, details?, request_id}`；HTTP 4xx/5xx。
- **分页**：`limit`（≤ 500）+ `cursor`（不透明）；响应 `next_cursor`。
- **时间参数**：`from`/`to`/`at`/`since` 均为毫秒或 ISO8601。
- **降级**：任何聚合对象含 `sources: {venue: "ok"|"stale"|"error"}` 与 `as_of`；缺失字段为 `null`，不为 0。
- **国际化**：句子字段成对出现 `sentence: {zh, en, template_id, tags[]}`；前端按语言取。
- **CORS**：公共 GET 允许所有来源。

## 3. 端点总览（详见 YAML）

| 组 | 端点 |
|---|---|
| 市场 | `GET /coins` · `GET /coins/{symbol}/prism` · `GET /coins/{symbol}/context` · `GET /coins/{symbol}/levels` · `GET /coins/{symbol}/history` · `GET /coins/{symbol}/state-history` · `GET /coins/{symbol}/snapshot` · `GET /market/breadth` · `GET /insights` |
| 费率/持仓 | `GET /funding` · `GET /funding/{symbol}/history` · `GET /funding/arb` · `GET /oi/{symbol}/history` · `GET /ls/{symbol}` |
| 爆仓 | `GET /liquidations/recent` · `GET /liquidations/agg` · `GET /liquidations/heatmap/{symbol}` · `GET /liquidations/cascades` |
| 大户 | `GET /whales` · `GET /whales/{address}` · `GET /whales/{address}/fills` · `GET /whales/positions` · `GET /whales/events` · `GET /whales/by-coin` · `GET /whales/boards` |
| 事件/记分 | `GET /events` · `GET /scorecard` · `GET /calendar` |
| 期权/基差/宏观 | `GET /options/{currency}/summary` · `GET /basis` · `GET /macro` |
| 系统 | `GET /status` · `GET /openapi.json` |
| 用户 | `GET/PUT /me` · `GET/POST/DELETE /me/positions` · `GET/POST/DELETE /me/watchlist` · `GET/POST/PATCH/DELETE /me/alerts` · `GET /me/alerts/deliveries` · `GET/POST/DELETE /me/api-keys` |

## 4. WebSocket `/v1/stream`

- 连接后发送 `{"op":"subscribe","channels":["prism:BTC","liq:*","whale:events","state:BTC"]}`。
- 服务端消息：`{"ch":"prism:BTC","seq":123,"ts":1789...,"data":{...Prism 局部更新...}}`；`seq` 单调递增，客户端检测缺口后用 REST 补。
- 心跳：服务端每 15 秒 `{"op":"ping"}`，客户端回 `pong`；60 秒无消息客户端重连并退化为 10 秒轮询。
- 频道：`prism:{symbol}`（10s）、`state:{symbol}`（变更即推）、`liq:*` / `liq:{symbol}`（实时）、`whale:events` / `whale:{address}`（实时）、`funding:{symbol}`（1m）、`events:*`（实时）。
- 认证：匿名 3 个订阅；登录 10；Pro 50。

## 5. MCP（`/mcp`，Streamable HTTP）

工具与 REST 一一映射：`list_coins`、`get_prism`、`get_context`、`get_levels`、`get_history`、`get_snapshot`、`get_funding`、`get_liquidations`、`get_whales`、`get_whale`、`get_whale_events`、`get_events`、`get_scorecard`、`get_macro`、`explain_coin`（返回带标签的中英句子与其输入）、`create_alert`（需 API key）。每个工具的输入输出 schema 直接引用 YAML 的 components。

## 6. 静态快照层兼容

GitHub Pages 的 `data/latest.json` 保持为 `PrismSnapshotFile{schema, generated_at, sources, coins: Prism[], whales: WhaleAgg, macro}`，字段名与 `/v1` 一致；生产上线后由 API 每 30 分钟生成并推送到 Pages，作为免费离线镜像。
