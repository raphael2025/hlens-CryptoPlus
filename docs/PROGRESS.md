# 开发进度（由 Claude 维护，每次派活或收工更新）

> 阶段定义见 `00-PROJECT.md` §5。角色定义见 `.claude/agents/`。状态：`待办` `进行中` `待审` `完成` `阻塞`。

## 当前阶段：S0 地基（第 1–2 周，2026-09-12 开始）

| # | 任务 | 角色 / 模型 | 状态 | 更新 | 备注 |
|---|---|---|---|---|---|
| S0-1 | `packages/hlens-core` 骨架：contracts、ratelimit（含 HL 权重表）、preflight、适配器协议与能力声明、Binance 参考适配器、离线测试 | backend-dev / Opus | 进行中 | 09-12 | 先出参考实现，其余五所照此并行 |
| S0-2 | 验证 HL WS `trades` 是否带 `users` 地址、WS 限额与 `userFillsByTime` 计费 | data-engineer / Sonnet | 进行中 | 09-12 | 决定 whale-engine 的实时方案（06 §7-1） |
| S0-3 | 法律与来源页：`/terms` `/privacy` `/disclaimer` `/sources`，中英 | frontend-dev / Sonnet | 完成 | 09-12 | 已上线静态站；`config.js` 的 `supportEmail` 是占位，待 raphael 填 |
| S0-4 | Bybit、OKX、Gate、Bitget、Hyperliquid 适配器 | backend-dev ×2 / Opus | 待办 | – | 等 S0-1 参考实现 |
| S0-5 | `config/venues.yaml`（从 06 抄常数） | backend-dev | 待办 | – | 随 S0-4 |
| S0-6 | hub 只读导出 + 试导一天数据 | backend-dev | 阻塞 | – | 需东京 DB；先用本机 Postgres 试 |
| S0-7 | 两台 VPS 规格、出口 IP、Docker 版本 | raphael | 阻塞 | – | 等信息或登录授权 |
| S0-8 | hub 安全修复（Tailscale 密钥轮换、admin 绑 127.0.0.1） | raphael | 阻塞 | – | |
| S0-9 | 域名、Telegram 频道名、CoinGecko Demo key | raphael | 阻塞 | – | |
| S0-10 | 代码审查 S0-1、S0-4 | reviewer / Opus | 待办 | – | |

## 已完成

| 日期 | 事项 |
|---|---|
| 09-11 | 文档 00–05、OpenAPI 1.1.0、三轮审查 |
| 09-12 | 06 数据源文档；币种分层决定（核心 5 + 浅层 200）；架构边界定稿（hub 管历史普查，东京管实时与计算，浏览器管个人视图） |

## 决定记录

- 09-12 不用 ccxt，自写适配器（06 §2、adapter 研究）。
- 09-12 hub 不重构、不容器化；只加只读导出、备份、安全修复。
- 09-12 hot 钱包实时成交拟用 WS `trades` 频道替代逐用户订阅（待 S0-2 验证）。
