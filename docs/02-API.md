# hlens · API 契约文档（v3.1）

> **唯一真相源是 `api/openapi.yaml`（OpenAPI 3.1）**。后端 pydantic 与前端 TypeScript 都从它生成；改字段先改 YAML。本文只写约定，不重复字段。

## 1. 一致性机制
| 环节 | 做法 |
|---|---|
| 定义 | `api/openapi.yaml`。命名：`snake_case`；`ts` 毫秒整数；`*_usd` 美元；`*_share` 0–1；`*_pct` 百分数；费率为小数（0.0001 = 0.01%）；缺失用 `type: [X, "null"]`，绝不填 0。每个 operation 有 `operationId`（= MCP 工具名与前端方法名）。 |
| 校验 | CI 跑 `redocly lint`（阻断）+ `oasdiff breaking`（对上一版判断破坏性变更）。 |
| 后端 | `datamodel-codegen` → `apps/api/hlens_api/schemas/generated.py`；路由只返回生成模型；FastAPI 导出的 `/openapi.json` 与 YAML 用 `oasdiff` 比语义差异而非文本 diff。 |
| 前端 | `openapi-typescript` → `apps/web/src/api/types.ts`；`openapi-fetch` 客户端；WS 负载类型同样来自 YAML 的 `components.schemas.Stream*`；禁止手写接口类型。 |
| 契约测试 | `tests/contract/`：schemathesis（随机）+ 固定 `examples`。 |
| 版本 | `/v1` 只加不删；字段 `x-hlens-since`；废弃 `deprecated: true` + `x-hlens-sunset`；破坏性变更开 `/v2` 并保留 `/v1` ≥ 6 个月。 |
| 阶段 | `x-hlens-stage: v1.1` 标记本期不实现的端点（期权、基差、高级视图），生成器保留类型但路由返回 501。 |

## 2. 通用约定
- Base：`https://api.hlens.xyz/v1`。
- 认证：公共 GET 免认证；`/me/*` 接受 `session`（Bearer）或 `apiKey`（`X-API-Key`）；创建 API key 只允许 session。
- 限流：键 = `CF-Connecting-IP`（校验对端在 Cloudflare 网段）+ API key；匿名 30/min、免费 60/min、Pro 600/min；响应头 `X-RateLimit-Limit/Remaining/Reset`；超限 `429` + `Retry-After`。
- 缓存：公共 GET 带 `Cache-Control: public, max-age=N, s-maxage=N, stale-while-revalidate=600` 与 `ETag`；N 见各端点 `x-hlens-cache`；Cloudflare Cache Rule 覆盖 `/v1/*` 并包含查询串。
- 错误：`Error{code, message, details?, request_id}`；所有端点声明 400/401/429/500。
- 列表：统一封套 `{as_of, sources, items[], next_cursor}`；`limit ≥ 1`；`cursor` 不透明。
- 时间参数：毫秒整数。
- 降级：聚合对象含 `as_of` 与 `sources{venue: ok|stale|error}`。
- 句子：`Sentence{template_id, vars, tags, zh, en, contradiction?}`，前端按 `vars` 本地渲染。
- CORS：公共 GET 允许所有来源。

## 3. 端点总览（`operationId`）
| 组 | 端点 |
|---|---|
| market | `listCoins` GET /coins（`symbols`, `fields`, `preset`, `sort`, `limit`, `cursor`）· `getPrism` · `getContext` · `getLevels` · `getHistory`（`fields` 枚举、`venue` 可选）· `getStateHistory` · `getSnapshot` · `explainCoin` GET /coins/{symbol}/explain · `getBreadth` · `getInsights` |
| funding | `listFunding` · `getFundingHistory` · `listFundingArb` · `getOiHistory` · `listLsRatios` |
| liquidations | `listLiquidations` · `getLiqAgg` · `getLiqHeatmap` · `listCascades` |
| whales | `listWallets` · `getWallet` · `listWalletFills` · `listWhalePositions` · `listWhaleEvents` · `listWhaleByCoin` · `getWhaleBoards` |
| events | `listEvents` · `getScorecard` · `listCalendar` · `listEvidence` · `listChangelog` |
| macro | `getMacro` · `getOptionsSummary`（v1.1）· `listBasis`（v1.1） |
| system | `getStatus` · `getOgImage` GET /og/{template} |
| me | `getMe` `updateMe` · `listMyPositions` `createMyPosition` `updateMyPosition` `deleteMyPosition` · `getWatchlist` `addWatchItem` `removeWatchItem` · `listAlerts` `createAlert` `updateAlert` `deleteAlert` `pauseAllAlerts` `listAlertDeliveries` · `subscribeDigest` · `listPushSubscriptions` `createPushSubscription` `deletePushSubscription` · `listApiKeys` `createApiKey` `deleteApiKey` |

## 4. WebSocket `/v1/stream`
- 客户端：`StreamSubscribe{op: subscribe|unsubscribe, channels[]}`；服务端：`StreamMessage{ch, seq, ts, data}`，`seq` 由 Redis `INCR` 全局单调；心跳 15s `ping`/`pong`；60s 无消息重连并退化 10s 轮询。
- 频道与负载：`prism:{symbol}` → `PrismPatch`（Merge Patch）· `state:{symbol}` → `MarketState` · `liq:*`/`liq:{symbol}` → `LiqBatch`（250ms 聚合，订阅可带 `min_usd`）· `whale:events` / `whale:{address}` → `WhaleEvent` · `events:*` → `Event`。
- 订阅上限：匿名 8、登录 20、Pro 50。

## 5. MCP（`/mcp`，Streamable HTTP）
工具名 = `operationId`；输入输出直接引用 YAML schema。额外工具 `explainCoin`（有 REST 对应）与 `createAlert`（需 API key）。

## 6. 静态快照层
GitHub Pages 的 `data/latest.json` 是**独立 schema** `SnapshotFile`（v0.1 字段名），不是 `Prism` 子集；S1 起由 API 生成并迁移到 `Prism` 字段名，旧字段保留一个版本后删除。
