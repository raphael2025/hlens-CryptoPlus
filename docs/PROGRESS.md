# 开发进度（由 Claude 维护，每次派活或收工更新）

> 阶段定义见 `00-PROJECT.md` §8（v4.0 起为有序阶段，无日历；每周工时待 raphael 给出）。范围见 §5。角色定义见 `.claude/agents/`。状态：`待办` `进行中` `待审` `完成` `阻塞`。

## 当前阶段：R0 重启地基（2026-09-19 开始）

第一切片 = Binance + Hyperliquid，仅永续。钱包 / 大户范围未定（见下方"待 raphael 决定"），相关任务一律不开工。

| # | 任务 | 角色 / 模型 | 状态 | 更新 | 备注 |
|---|---|---|---|---|---|
| R0-1 | 把 `00-PROJECT.md` 与本文件重写为 v4（新定位、双所第一切片、Phase 1/2/3、策略边界、待定项） | Claude | 完成 | 09-19 | 00 v4.0；v3.1 原文在 commit `e693da7` |
| R0-2 | Hyperliquid 适配器（**仅行情方法**；钱包方法待 §5.4 范围决定后再说） | backend-dev / Opus | 待办 | 09-19 | **最高优先**（承接 09-12"优先做 Hyperliquid"）。`liquidation_*` 能力按 `lower_bound` 声明，`full` 等 R0-4。不依赖 `S0-4a`/`S0-4b` 分支；若将来合并用 rebase 处理基类冲突 |
| R0-3 | 审查 R0-2（契约、限速预算、能力声明、fixtures、符号映射） | reviewer / Opus | 待办 | 09-19 | 按 `AGENTS.md` §3/§4 验收 |
| R0-4 | HL 节点数据验证：拉一天 `node_fills_by_block` 存档与 hub `hl.liquidation` 逐笔比对，得出实测完整度与按币缺口 | data-engineer / Sonnet | 待办 | 09-19 | 原 S0-11。强平覆盖率报告 §8 第 0 条；任何"完整度"对外声明的前提；通过后再评估月度存档回填与自跑节点 |
| R0-5 | 下游文档同步到 v4 范围：`01`、`02` + `api/openapi.yaml`、`03`、`05`、`06`、`README.md`、`AGENTS.md` | Claude | 待办 | 09-19 | 它们仍写六所范围与大户功能；**同步完成前一律以 00 为准**。`whales` 组端点等 §5.4 决定，先不动 |
| R0-6 | Binance WS fixtures 从东京重录（现有为文档推导态） | data-engineer / Sonnet | 阻塞 | 09-19 | 原 S0-1 遗留；卡在 VPS（见下） |

### 待 raphael 决定 / 提供（阻塞，不派 agent）

| # | 事项 | 状态 | 影响 |
|---|---|---|---|
| RB-1 | 两台 VPS 规格、出口 IP、Docker 版本（原 S0-7） | 阻塞 | 容量表、R0-6 fixtures 重录、R1 双机部署 |
| RB-2 | hub 安全修复（Tailscale 密钥轮换、admin 绑 127.0.0.1）（原 S0-8） | 阻塞 | hub 任何对外使用 |
| RB-3 | 域名、CoinGecko Demo key、`config.js` 的 `supportEmail`（原 S0-9 余项） | 阻塞 | 上线与法律页联系方式；TG 群已给并已配置 |
| RB-4 | **钱包 / 大户追踪归属本项目还是另一项目**（00 §5.4） | 待定 | HL 钱包方法、大户引擎与五板、钱包页与 WhaleCard、hub 历史导入、`whales` 端点、静态站鲸鱼透镜 |
| RB-5 | 头像使用 Binance / Hyperliquid 官方 logo 的处理（去 logo / 改文字 / 加 "not affiliated"） | 待定 | 与 `/disclaimer` 的无关联声明冲突；不阻塞开发 |

## 搁置（Parked）

| # | 事项 | 原因 |
|---|---|---|
| S0-4a | 分支 `S0-4a`（`73d89f5`，已推远端）：`bybit.py` 657 行、`okx.py` 773 行、两套 fixtures、`venues.yaml` +14 行。**无测试文件，未跑离线套件，未审查，未合并** | 归入 Phase 3（00 §5.3）。Cursor 当时未提交，09-19 原样存为 WIP |
| S0-4b | 分支 `S0-4b`（`498f174`，已推远端）：`gate.py` 675 行、Gate 与 Bitget fixtures。**Bitget 适配器本体未写**；无测试，未审查，未合并 | 同上 |
| S0-6 | hub 只读导出 + 试导一天数据 | 门控于 RB-4（钱包/大户范围）；另需东京 DB |

## 已完成

| 日期 | 事项 |
|---|---|
| 09-11 | 文档 00–05、OpenAPI 1.1.0、三轮审查 |
| 09-12 | 06 数据源文档；币种分层决定（核心 5 + 浅层 200）；架构边界定稿（hub 管历史普查，东京管实时与计算，浏览器管个人视图） |
| 09-12 | **S0-1** `packages/hlens-core` 骨架：contracts、ratelimit（含 HL 权重表、AIMD、硬熔断）、preflight CLI、适配器协议与能力声明、Binance 参考适配器、离线测试。129 离线 + 7 live 测试通过，mypy 干净；reviewer 首轮 7 处限速/重试缺陷已修并加回归测试。遗留：Binance WS fixtures 为文档推导态，待东京重录（→ R0-6） |
| 09-12 | **S0-5** `config/venues.yaml`（从 06 抄常数），含 `source` 标签与 `budget ≤ limit` 校验；随 S0-1 产出 |
| 09-12 | **S0-2** 验证 HL WS `trades` 是否带 `users` 地址、WS 限额与 `userFillsByTime` 计费 → `reports/2026-09-12-hl-ws-trades.md`；方案定案见决定记录 |
| 09-12 | **S0-3** 法律与来源页 `/terms` `/privacy` `/disclaimer` `/sources`（中英）上线静态站；`config.js` 的 `supportEmail` 仍是占位（→ RB-3） |
| 09-12 | hub `hl.liquidation` 覆盖率 / 延迟 / 采集能力测量（`reports/2026-09-12-hl-liquidation-coverage.md`）；修正 00 §6 与 06 §2.5 对 HL 强平来源的描述 |
| 09-12 | **S0-10**（部分）代码审查 S0-1 首轮：Binance 映射与 `venues.yaml` 常数全部对上，缺陷集中在 `budget.py`/`http.py`，已修 |
| 09-19 | 重启前盘点：09-12 之后一周无提交；`S0-4a`/`S0-4b` 的 Cursor 产出一直未提交，09-19 原样提交到各自分支并推远端（未合并 main）；PR #1 已于 09-12 合入 main，但本地 main 与各 worktree 一周未同步，09-19 已对齐并删除合并后的远端分支 |
| 09-19 | **R0-1** 00 重写为 v4.0、PROGRESS 重排为 R0 |

## 决定记录

- 09-12 不用 ccxt，自写适配器（06 §2、adapter 研究）。
- 09-12 hub 不重构、不容器化；只加只读导出、备份、安全修复。
- 09-12 hot 钱包实时成交定案：WS `trades{coin}`（带买卖双方地址）发现 + 每分钟 `clearinghouseState` 对账 + 仓位变化才拉 `userFillsByTime` 补字段；`userFills` WS 只留给 ≤ 8 个钱包。S0-2 实测通过。`03-DEVELOPMENT` §4.1 在 S0-4 时一并改。
- 09-12 **优先做 Hyperliquid**。理由：HL 是唯一能拿到账本级真值的所（节点数据），也是唯一能把多空/拥挤度按公开公式自算、而不是转述交易所黑盒数字的所。顺序：HL 适配器（S0-4c）→ 节点数据验证（S0-11）→ 节点强平链路。CEX 的费率/OI/K 线是精确数据，按原计划；CEX 强平流一律 `lower_bound` 事件流，不做总量、不与 HL 并排比总额。hub 的 `hl.liquidation` 只能做"大户强平事件流"，展示窗口须晚于 P95 延迟（约 33 h）。分工不变：钱包级留 hub 轮询路径，全所级走节点数据。
- 09-19 **定位改写**：从"永续散户的棱镜看板"改为"多所加密情报与量化研究系统"（HLENS CryptoPlus — Multi-Venue Crypto Intelligence & Quant Research System）。棱镜 / 分光隐喻保留为核心思想：把一个市场拆成多个参与者与多个面，**面与面之间的分歧就是信息**。
- 09-19 **第一切片 = Binance + Hyperliquid，仅永续**（一个 CEX + 一个 DEX）。据此确认 **Hyperliquid 行情适配器在范围内且是最高优先编码任务**，与 09-12"优先做 Hyperliquid"一致。
- 09-19 **头像能力表分期**，纵向做透不横向铺开：Phase 1 = 双所永续的价格/标记价、费率、OI、（该所公开时的）多空与主动买卖比、`lower_bound` 爆仓事件流、K 线、合约元数据；Phase 2 = 订单流（逐笔 → CVD）、流动性（盘口深度）、现货——**这两项 v3.1 §5 曾明确砍掉，现恢复但后移**，是数据量与限速最重的一类；Phase 3 = 更多交易所（Bybit/OKX/Gate/Bitget，半成品停在 `S0-4a`/`S0-4b`，未合并）、多资产。
- 09-19 **"策略"的边界**：在 hlens，策略 = 可复现的研究与回测，结论必带样本量 n 与证据标签，公式与窗口公开可重跑；**永远不是**交易信号、喊单、目标价或建议。标语 "See more · Think deeper · Trade wiser" 只是品牌语，产品文案一律遵守原则 1 的禁止词表。原则 1 与证据标签、样本规则不变，该边界写入 00 §4.2。
- 09-19 **待定：钱包 / 大户追踪（Trader Behavior / On-Chain）归属**——属于 CryptoPlus，还是留在 raphael 的另一个项目、CryptoPlus 只消费其产出？历史：09-12 raphael 先说 HL / 钱包相关工作属于另一个项目，同日文档又记下"优先做 Hyperliquid"；09-19 以 Binance + HL 第一切片确认 **HL 行情数据在范围内**，**钱包 / 大户范围仍未决**。依赖项见 RB-4。决定前不开工、不改相关文档。
- 09-19 线上静态站（GitHub Pages、`scripts/fetch.py` 每 30 分钟、六所、鲸鱼透镜、法律页）保持原样运行，不属于本次重启范围。
