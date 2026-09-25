# BTC 多周期 · 市场机制 · 非对称趋势交易研究框架（V9.0 Candidate）

> 来源：raphael 于 2026-09-25 提供的 7 份提示词，按原意整理、去重、合并，未加入评估意见。
> 状态：**候选版，未冻结、未经验证**。每一层的证据与可行性评估见 `docs/reports/`。
> 本文只记录框架「是什么」；任何一条规则要进入策略，必须走 §20 的研究流程。

## 0. 核心目标与交易三角

**核心目标**：识别趋势 → 避开震荡 → 理解趋势背后的资金行为 → 找非对称位置 → 小亏试错 → 大趋势持有。

```
                 胜率
                  ▲
                 / \
                /   \
               /     \
              ●───────●
          盈亏比       频率
            ↑
         我们偏这里
```

- 主动选择：**低 / 中频 + 较低胜率 + 高盈亏比**；不追求「高胜率 + 高频率 + 高盈亏比」这种不可持续的组合。
- 错误 ≈ **-1R**；正确 = **+3R / +5R / +8R / +10R…**
- 目标：**正期望 + 正偏态 + 右尾收益**；不以胜率最大化为目标。

目标收益分布示意：`-1R, -1R, -1R, +0.5R, -1R, +4R, -1R, +7R, -1R, +3R, …`

## 1. 总体架构

```
【0 策略目标】     低频 / 高盈亏比 / 趋势
      ↓
【1 市场周期】     筑底 → 上升 → 筑顶 → 下降
      ↓
【2 多周期状态】   4H → 1H → 15m
      ↓
【3 价格行为层】   结构 / 位移 / 回调
      ↓
【4 现货层】 ‖ 【5 合约层】
      ↓
【6 市场机制层】   主动攻击 / 被动承接 / Price Impact / 吸收 vs 有效推动
      ↓
【7 状态解释器】   扩张 / 去杠杆 / 回补 / 吸收 / 陷阱 / 换手 / 压缩 / 强平 / 挤压
      ↓
【8 拥挤风险扫描】 止损池 / 清算池 / 拥挤
      ↓
【9 策略路由】     不做 / 等待 / 回调 / Sweep 后进入
      ↓
【10 15m 执行层】  Sweep / Reclaim / BOS
      ↓
【11 风险与仓位】  固定风险 + 结构失效
      ↓
【12 持仓管理】    HOLD / RE-ENTRY / EXIT
      ↓
【13 经验与研究】  回测 / 消融 / Walk-Forward / Monte Carlo
```

### 七棵树

| 树 | 内容 |
|---|---|
| ① 市场树 | 周期 → Regime → State → Structure → Trend Age |
| ② 机制树 | Spot + Futures + Flow + Positioning + Liquidation → Price Impact |
| ③ 状态解释树 | 扩张 / 去杠杆 / 回补 / 吸收 / 换手 / 陷阱 / 挤压 / 压缩 |
| ④ 策略树 | 趋势跟随 → 健康回调 / Sweep 后延续 / 不交易 |
| ⑤ 风控树 | 结构失效 → 单笔风险 → 仓位计算 → 拥挤风险 → 流动性风险 → 账户风险 → 异常熔断 |
| ⑥ 决策树 | WAIT → PREPARE → OPEN → HOLD → RE-ENTRY → EXIT |
| ⑦ 经验 / 研究树 | 状态序列 → Setup → Outcome → MFE/MAE/R → 失败归因 → 相似历史状态 → Hypothesis → 回测 → OOS 验证 → 新版本 |

## 2. 市场周期层

`筑底 → 上涨 → 筑顶 → 下降 → 筑底 …`，不机械照搬均线参数。

| 大周期 | V9 解释 |
|---|---|
| 筑底 | 震荡 / 转换 / 潜在上涨启动 |
| 上涨 | 上涨趋势 |
| 筑顶 | 成熟 / 衰竭 / 转换 |
| 下降 | 下跌趋势 |

它只是最高层 Context，**只定义大环境，不直接产生 LONG / SHORT**。不能说「现在筑底，所以做多」。

## 3. 多周期嵌套状态机

- **4H = 父周期 / 市场环境**：趋势 / 回调 / 转换 / 压缩 / 震荡。回答「市场现在是否适合趋势策略」：震荡（CHOP）→ 不交易；转换 / 压缩（TRANSITION）→ 等待；趋势（TRENDABLE）→ 进入 1H 分析。
- **1H = 主交易周期**：方向 / 结构 / 趋势阶段 / Setup。
  - 上涨结构 HH → HL、BOS↑；下跌结构 LL → LH、BOS↓；无方向结构 = 压缩 / 震荡。
  - 趋势阶段：刚启动 → 扩张 → 健康 → 回调 → 成熟 → 衰竭。
  - 主动段 vs 回调段：主趋势力量明显更强 → 健康趋势候选；否则 → 趋势恶化 / 等待。
- **15m = 子周期 / 执行**：Sweep / Reclaim / BOS / CHOCH / Entry。

核心：不是「4H 多、1H 空、15m 多，信号冲突」，而是 **15m 状态 ∈ 1H 状态 ∈ 4H 状态**。示例：

```
4H  强势上涨
      └── 当前发生回调
1H        4H 回调内部的整理
15m           1H 整理内部向上挤压
```

## 4. Price Core（价格事实层，最高优先级）

- 结构：HH / HL / LH / LL，BOS / CHOCH，EQH / EQL
- 事件：Break / Acceptance / Rejection，Sweep / Reclaim
- **Impulse**：位移 / 速度 / ATR 标准化位移 / 斜率 / K 线实体效率 / 持续时间
- **Correction**：深度 / 速度 / 重叠（Overlap）/ K 线效率 / Wick / 持续时间

核心：**趋势段强、回调段弱 = 健康**；而不是「回调了 20%，所以可以买」。

## 5. 市场内部机制：现货与合约严格分层

### 5A 现货市场
Spot Price / Spot Volume（参与度）/ Spot CVD（真实现货主动买卖）/ Spot VWAP、Anchored VWAP（平均成交成本）/ Spot Flow。

回答：是否存在真实现货需求？是现货驱动还是杠杆驱动？

```
Price↑ + Spot CVD↑ + Futures CVD↑ + OI↑
→ 现货 + 合约共同推动 → 趋势质量较高

Price↑ + Spot CVD→ + Futures CVD↑↑ + OI↑↑ + Funding↑
→ 杠杆主导上涨 → 脆弱性更高
```

### 5B 合约市场（七个模块，各答一个问题）

| 模块 | 回答的问题 | 不负责 |
|---|---|---|
| ① Futures Volume | 参与强度 | |
| ② OI / ΔOI | 新未平仓风险在增加还是减少？ | 判断方向 |
| ③ Futures CVD | 谁在主动攻击？ | 判断开仓 / 平仓 |
| ④ Funding | 哪边越来越拥挤？ | |
| ⑤ Basis | 合约相对现货的定价状态 | |
| ⑥ Liquidations | 谁正在被强制退出？ | 预测 |
| ⑦ Participant / Smart Money | 参与者证据 | 产生方向信号 |

## 6. 市场机制解释层（Price Impact）

提问顺序：
1. **OI**：新风险进入还是旧仓退出？
2. **CVD**：谁在主动攻击？
3. **Volume**：参与强度有多大？
4. **Liquidation**：谁被强制退出？
5. **Price Response（最重要）**：这么大的力量进来以后，价格到底移动了多少？
   - 推得动 → 主动方有效 → Acceptance → 控制权增强
   - 推不动 → 被动方吸收 → Absorption → 攻击效率下降

核心：**Flow ≠ Direction；Flow → Price Response → Control**。

```
Impact ≈ Price Displacement / Aggressive Flow
```

示例：`CVD -1000 → Price -3%`、`CVD -1000 → Price -1%`、`CVD -1000 → Price -0.2%` 说明卖方效率持续下降；若最后 `CVD↓↓↓ 但 Price↑`，被动买方的控制力非常值得关注。

## 7. Price × OI × CVD 状态解释器

| Price | OI | CVD | 状态候选 |
|---|---|---|---|
| ↑ | ↑ | ↑ | 买方主导的新仓扩张 |
| ↓ | ↑ | ↓ | 卖方主导的新仓扩张 |
| ↑ | ↓ | ↑ | 空头回补候选 |
| ↓ | ↓ | ↓ | 多头去杠杆候选 |
| → / ↑ | ↑ | ↓ | 卖盘被吸收 / 被动买方累积候选 |
| → / ↓ | ↑ | ↑ | 买盘被吸收 / 被动卖方累积候选 |

- 以上全部是**状态候选**，不能根据单个时间点下结论。
- 语言规范：禁止说「OI↑ + CVD↓ = 一定是新空」；正确说法是「新增风险建立过程中主动卖方占优势」。

状态解释器的完整状态集：买方 / 卖方主导新仓扩张、多头去杠杆、空头回补、买方 / 卖方吸收、多头 / 空头陷阱、多头 / 空头强平、Short / Long Squeeze、换手、压缩、波动扩张、控制权转换。

**禁止单点判断**：必须识别 T0 → T1 → T2 → T3 的状态转换序列。

## 8. 状态序列引擎

不研究单点指标，研究状态如何连续转换。示例：

```
T0  Price↓ + OI↑ + CVD↓                    → 卖方主导的新仓建立
T1  CVD 继续↓ + OI 继续↑，BUT Price 跌不动   → 卖方价格冲击下降 / 买方吸收候选
T2  Price↑ + CVD↑ + OI↓                    → 空头回补候选
T3  Short Liquidation↑↑                    → 空头退出假设获得进一步验证 / 空头挤压
T4  Price↑ + OI 重新↑ + Spot CVD↑ + Futures CVD↑ → 新多接力 → 真正上涨趋势扩张
```

完整状态链：新空建立 → 卖盘被吸收 → 卖方失败 → 空头回补 → 空头强平 → 空头挤压 → 新多接力。**这比任何单一指标重要。**

## 9. 爆仓层

Liquidation 不负责预测，只负责验证**谁被迫认输**。

```
Price↓ + OI↓ + CVD↓ + Long Liquidation↑↑  → 多头强平 / 去杠杆
Price↑ + OI↓ + CVD↑ + Short Liquidation↑↑ → 空头回补 / 挤压
```

OI 告诉我们仓位消失；CVD 告诉我们退出方向；Liquidation 告诉我们其中多少属于强制退出。

## 10. 参与者证据层（Smart Money，降权）

对象：Binance Smart Money / Top Trader / Whale / Retail。
观察：当前多 / 空仓位、平均开仓成本、未实现盈亏、盈利比例、30m / 1H / 24H 净买入 / 净卖出。

- 输出只允许：**SUPPORTIVE / NEUTRAL / CONFLICTING**。
- 禁止：Smart Money 买 → LONG；禁止直接跟单。
- 示例：`Futures CVD↓ + Price 不跌 + Smart Money 净买入 → 被动承接假设增强`，但不能产生方向信号。

## 11. 拥挤 / 流动性脆弱性 / 猎杀防御层

不需要预测「机构准备猎杀谁」，只判断：**这个位置是否存在低成本触发大量止损 / 强平的条件？**

输入：Funding、OI / ΔOI、Basis、Retail L/S、Top Trader、Smart Money、Liquidation Map、Actual Liquidations、EQH / EQL、Swing High / Low、Stop Cluster、Order-book Liquidity（如果有）。

输出（各为 低 / 中 / 高）：拥挤风险、止损集中风险、清算链风险、流动性猎杀风险。

**原则：拥挤风险不改变方向，只改变执行。**「猎杀风险高」不能把 LONG 改成 SHORT，只能选择：NORMAL ENTRY / WAIT FOR SWEEP / REDUCE RISK / NO TRADE。

### Sweep Defense 示例（本应做多）

```
4H 上涨 ✓   1H 趋势 ✓   Setup ✓
BUT 下方 EQL + 大量止损 + 多头清算密集 + 流动性薄
→ 不立即做多，等待扫低点（让猎杀先发生）
→ Long Liquidation↑↑ + OI↓↓ + CVD↓↓↓
→ 观察 Price Impact：
   ├─ 快速 Reclaim → 卖方攻击失败 / Impact 衰减 → 等 15m 重新 BOS → LONG
   └─ 下方 Acceptance → 真正跌破 → 放弃多头 Setup
```

## 12. 策略路由

市场适配？ 震荡 → NO TRADE；转换 → WAIT；高拥挤 → WAIT SWEEP；趋势 → 找 Setup：**健康趋势回调** 或 **Sweep Continuation**。

## 13. 15m 执行层

- 15m 永远不能决定大方向；只有 4H 环境允许 + 1H Setup 成立，才允许 15m 工作。
- 寻找：Liquidity Sweep → Reclaim → CHOCH / BOS → Micro Pullback / Buyer or Seller Failure → 低风险结构位置 → ENTRY。
- 输出：结构止损（Structural Stop）。
- 15m 只优化执行，不允许改变 4H / 1H 方向。

## 14. 开仓决策树

```
4H 趋势环境允许？          NO → 不交易
1H 趋势结构存在？          NO → 等待
趋势年龄合理？
主动段 > 回调段？          NO → 等待
现货 / 合约机制支持？      明显反对 → 等待
Price Response 支持趋势？  NO → 等待
拥挤 / 猎杀风险？          HIGH → WAIT FOR SWEEP；LOW → 正常等待回调
15m 低风险执行
结构失效位置明确
固定账户风险
→ OPEN
```

## 15. 双层失效模型

- **Execution Invalidation（执行失效）**：15m 这次入场失败 → 约 -1R；1H 趋势可能仍成立 → 允许等待重新入场（Re-entry）。
- **Thesis Invalidation（逻辑失效）**：1H 交易假设失效。多头示例：核心 HL 破坏 + 下方 Acceptance + Reclaim 失败 + OI↑ + CVD↓ + 新卖方持续产生 Price Impact → EXIT / LONG THESIS INVALID。
- 禁止：「可能机构猎杀，我扛一下」。

## 16. 持仓逻辑

开仓以后不再要求指标一直「看多」。核心问题：**反方向力量有没有真正获得价格控制？**

```
多单示例：
CVD↓ BUT Price 不跌                → HOLD
CVD↓↓ + OI↑ BUT Price↑             → 卖方被吸收 → HOLD

真正危险：
Price↓ + OI↑ + CVD↓ + Volume↑ + ATR↑ + 结构破坏 + Reclaim 失败
→ 新空取得控制 → EXIT
```

持仓决策树：

```
1H 结构还有效？
├─ YES → 对手主动攻击？攻击能推动价格吗？
│        ├─ NO  → 被吸收 / 无效 → HOLD（1H Structure Trail）
│        └─ YES → 风险增加 ─┐
└─ NO  → 检查是否只是流动性 / 强平事件（清算扫损 → 按预定义规则处理 / 寻找重新进入）
                             ↓
          对手新仓持续进入 + Reclaim 失败？
          ├─ NO  → HOLD
          └─ YES → EXIT
```

## 17. 平仓原则

禁止单独因为以下任何一项退出：RSI 超买、MACD 死叉、CVD 变红、OI 下降、Funding 变高、Smart Money 变化。

主要退出链：

```
趋势结构失效 → Counter Impulse 增强 → Price Response 转向 → 对手方新仓进入 → Reclaim 失败 → EXIT
```

只要趋势结构和控制权没有真正改变 → HOLD，让 `+2R → +3R → +5R → +8R` 继续发展（1H Structure Trail）。

## 18. 风险 / 仓位模型

```
Position Size = Account Risk / Stop Distance
Structural Invalidation → Stop Distance → Position Size
```

- 每笔风险固定，不凭感觉加仓；不再「这个特别确定，多上点」。
- 策略信号置信度可以影响：是否交易、Entry 方式；但不能破坏账户风险预算。
- 每次错误尽量约 -1R；趋势正确时让右尾自然发展。

## 19. 框架稳定性：五类故障

| 故障 | 表现 | 修复 |
|---|---|---|
| ① 规则漂移 | 今天一个规则，明天一个规则 | 冻结版本 + Hypothesis 实验；不允许看行情临时改规则 |
| ② 仓位随机 | 凭感觉改变风险 | 固定风险公式 |
| ③ 状态不适配 | 趋势策略进入震荡 | 4H Regime Hard Gate |
| ④ 执行崩溃 | 连续亏损以后随意改策略 | Circuit Breaker + 数据 / 执行 / Regime 审计 |
| ⑤ 样本幻觉 | 看 3～5 次案例就认为有规律 | 历史样本 + Walk-Forward + Ablation |

稳健性检验：Monte Carlo、Walk-Forward、OOS、Parameter Stress、Cost Stress。

### 熔断不能简单「三连亏」

因为选择的是低胜率系统：

```
Monte Carlo → 得到正常连亏分布（P50 / P90 / P95）→ 超过异常阈值 → 暂停
→ 数据审计 + Regime 审计 + 执行审计
```

而不是连亏 3 次就改参数。

## 20. Research Governance

任何新发现的规律（例如 `OI↑ + CVD↓ + Price 不跌`）**不能直接加入策略**，必须：

```
Observation → Research Hypothesis → 历史样本 → 条件分层 → Backtest → Ablation
→ Walk-Forward → Robustness → ACCEPT / REJECT / RESEARCH_GAP → New Candidate Version
```

**研究，而不是自动改规则。**

## 21. 经验层：每笔记录

市场环境；4H / 1H / 15m 状态；Structure；现货状态、合约状态；OI、CVD、Volume、Funding、Basis、Liquidation、Smart Money；Price Impact、Absorption、Crowding Risk；Entry / Stop / Exit；MFE / MAE / R；State Sequence；Failure Reason。

→ Outcome → Experience Store → Historical Similarity → Research Hypothesis → Historical Test → Ablation → Walk-Forward → ACCEPT / REJECT / GAP。

## 22. 交易状态

`NO TRADE / WAIT / PREPARE / OPEN / HOLD / EXIT`，另有 `RE-ENTRY`（见 §23 第 1 项）。

## 23. 实时分析压缩流程

```
【4H 市场背景】 趋势 / 震荡 / 转换？
【1H 主交易状态】 结构 + 趋势阶段；主动段 > 回调段？
【现货】 Spot Volume / CVD / VWAP
【合约】 OI / CVD / Funding / Basis
【参与者】 Smart Money / Whale / Retail
【强制流】 Liquidations
【价格冲击】 谁在主动？谁在承接？投入多少？推动多少价格？
【状态序列】 新仓扩张？去杠杆？回补？吸收？换手？陷阱？挤压？
【拥挤 / 猎杀风险】 止损集中？清算集中？低成本可触发？
   ├─ 高 → 等待 Sweep
   └─ 低 → 正常寻找 Setup
【15m 执行】 Sweep / Reclaim / BOS
【结构止损】→【固定风险仓位】→ OPEN
【控制权仍在我方？】
   ├─ YES → HOLD / Structure Trail
   └─ NO → 只是清算扫损？
            ├─ YES → 按预定义规则处理 / 寻找重新进入
            └─ NO  → 结构失效 + 对手取得控制 → EXIT
【记录 Outcome】 MFE / MAE / R / 状态序列
【研究，而不是自动改规则】
```

## 24. 各版本之间的出入（待 raphael 定，本文不代定）

1. **交易状态数**：V9.0 列「六种」（NO TRADE / WAIT / PREPARE / OPEN / HOLD / EXIT），决策树 ⑥ 与持仓管理层有 RE-ENTRY。RE-ENTRY 是独立状态，还是从 EXIT / WAIT 回到 OPEN 的一条转换？
2. **Thesis Invalidation / 真正危险的条件数**：V9.0 列 5 条（结构破坏 + Acceptance + Reclaim 失败 + 对手新仓 + Price Impact 有效），持仓逻辑列 7 条（另加 Volume↑、ATR↑）。以哪一版为准，是「全部满足」还是「满足 k 条」？
3. **4H 状态集**：第 1 份为「趋势 / 回调 / 转换 / 压缩 / 震荡」五类，V9.0 为「震荡 / 转换·压缩 / 趋势」三类。4H 回调属于「趋势」还是单列？
