# 开发进度（由 Claude 维护，每次派活或收工更新）

> 阶段定义见 `00-PROJECT.md` §5。角色定义见 `.claude/agents/`。状态：`待办` `进行中` `待审` `完成` `阻塞`。

## 当前阶段：S0 地基（第 1–2 周，2026-09-12 开始）

| # | 任务 | 角色 / 模型 | 状态 | 更新 | 备注 |
|---|---|---|---|---|---|
| S0-1 | `packages/hlens-core` 骨架：contracts、ratelimit（含 HL 权重表）、preflight、适配器协议与能力声明、Binance 参考适配器、离线测试 | backend-dev / Opus | 完成 | 09-12 | 129 离线 + 7 live 测试通过，mypy 干净；reviewer 首轮 7 处限速/重试缺陷已修并加回归测试；Binance WS fixtures 为文档态，待东京重录 |
| S0-2 | 验证 HL WS `trades` 是否带 `users` 地址、WS 限额与 `userFillsByTime` 计费 | data-engineer / Sonnet | 完成 | 09-12 | 报告 `docs/reports/2026-09-12-hl-ws-trades.md`；方案定案见决定记录 |
| S0-3 | 法律与来源页：`/terms` `/privacy` `/disclaimer` `/sources`，中英 | frontend-dev / Sonnet | 完成 | 09-12 | 已上线静态站；`config.js` 的 `supportEmail` 是占位，待 raphael 填 |
| S0-4a | Bybit、OKX 适配器 | Cursor CLI / auto | 进行中 | 09-12 | 独立 worktree，分支 S0-4a |
| S0-4b | Gate、Bitget 适配器 | Cursor CLI / auto | 进行中 | 09-12 | 独立 worktree，分支 S0-4b |
| S0-4c | Hyperliquid 适配器（含钱包方法） | backend-dev / Opus | 待办 | – | 等 4a/4b 合并后做，避免基类冲突 |
| S0-5 | `config/venues.yaml`（从 06 抄常数） | backend-dev | 完成 | 09-12 | 随 S0-1 产出，含 source 标签与 budget ≤ limit 校验 |
| S0-6 | hub 只读导出 + 试导一天数据 | backend-dev | 阻塞 | – | 需东京 DB；先用本机 Postgres 试 |
| S0-7 | 两台 VPS 规格、出口 IP、Docker 版本 | raphael | 阻塞 | – | 等信息或登录授权 |
| S0-8 | hub 安全修复（Tailscale 密钥轮换、admin 绑 127.0.0.1） | raphael | 阻塞 | – | |
| S0-9 | 域名、Telegram 群、CoinGecko Demo key | raphael | 进行中 | 09-12 | TG 群已给并配置进 `config.js`（HLENS CryptoPlus 中文社区）；域名与 CoinGecko key 待给 |
| S0-10 | 代码审查 S0-1、S0-4 | reviewer / Opus | 进行中 | 09-12 | S0-1 首轮审完：Binance 映射与 venues.yaml 常数全部对上，缺陷集中在 budget.py/http.py |

## 已完成

| 日期 | 事项 |
|---|---|
| 09-11 | 文档 00–05、OpenAPI 1.1.0、三轮审查 |
| 09-12 | 06 数据源文档；币种分层决定（核心 5 + 浅层 200）；架构边界定稿（hub 管历史普查，东京管实时与计算，浏览器管个人视图） |

## 决定记录

- 09-12 不用 ccxt，自写适配器（06 §2、adapter 研究）。
- 09-12 hub 不重构、不容器化；只加只读导出、备份、安全修复。
- 09-12 hot 钱包实时成交定案：WS `trades{coin}`（带买卖双方地址）发现 + 每分钟 `clearinghouseState` 对账 + 仓位变化才拉 `userFillsByTime` 补字段；`userFills` WS 只留给 ≤ 8 个钱包。S0-2 实测通过。`03-DEVELOPMENT` §4.1 在 S0-4 时一并改。
