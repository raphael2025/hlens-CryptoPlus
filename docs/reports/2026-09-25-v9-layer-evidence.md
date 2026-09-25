# V9 逐层证据评估（2026-09-25）

> 对 `docs/01-FRAMEWORK-V9.md` 各层做的第二轮网络检索（4 个子代理）。多数论文全文被拦截，只核对到摘要 / 二手来源，未核实处已标注。
> 证据强度：强 / 中 / 弱 / 无。「非加密」= 证据来自外汇、股票或传统期货，未在 BTC 上验证。

## 1. 逐层结论

| V9 层 | 证据 | 关键来源 | 强度 |
|---|---|---|---|
| 0 正偏态 / 低胜率 | 趋势跟随长期正收益、危机中表现好 | Hurst, Ooi & Pedersen, *J. Portfolio Mgmt*（1880–2016，67 个市场） | 强（非加密） |
| 0 止损 | 止损在动量存在时能增值；10% 止损使动量最大月亏约减半 | Kaminski & Lo 2014, *J. Financial Markets* 18；Han, Zhou & Zhu, SSRN 2407199 | 中～强；移动止损是否保住正偏态未核实 |
| 1 市场周期 / 趋势阶段 | 快慢动量是否一致 → 牛 / 回调 / 熊 / 反弹四阶段 | Goulding, Harvey & Mazzoleni, *JFE* 149(3) 2023；「Breaking Bad Trends」*FAJ* 80(1) 2024 | 强（非加密） |
| 2 4H 门控 | ADX、效率比、Hurst、方差比、HMM 做门控 → 样本外改善未见严谨证据，普遍承认有滞后 | 从业者材料；arXiv 2507.15876 | 弱 / 无 |
| 3 止损聚集 | 止损集中在整数关口，被击穿后引发价格瀑布 | Osler 2000 FRBNY；Osler 2003 *JF* 58(5)；Osler 2005 *JIMF* 24(2) | 中（外汇；加密无） |
| 3 SMC / ICT（BOS、CHOCH、订单块、FVG、Sweep） | 无严谨检验；唯一系统回测 648 组 0 组跑赢买入持有 | StatOasis（从业者） | 无 |
| 3 结构识别形式化 | Directional Change：按阈值事件确认拐点，天然无前视 | Glattfelder, Dupuis & Olsen 2011, *Quant. Finance* 11(4) | 中（方法）；加密交易优势弱 |
| 3 主动段 vs 回调段 | 效率比等度量工具成立，「强主动 + 弱回调 → 延续」无预测证据 | Kaufman（著作） | 弱 / 无 |
| 3 假突破 / Reclaim | 失败率数字各说各的，无学术来源 | Bulkowski（从业者） | 弱 |
| 5A/5B 现货 vs 合约 | 期货领先现货定价 | Alexander et al. 2020 *JFM*；Aleti & Mizrach 2021 *JFM* 41(2)（CME） | 中 |
| 5A/5B 杠杆驱动更脆弱 | 无严谨检验；与高 carry 预示崩盘方向一致 | BIS WP 1087 | 弱～中 |
| 6 Price Impact / 吸收 | 高成交量后收益反转（做市方要求补偿） | Kyle 1985；Amihud 2002；Campbell, Grossman & Wang 1993 *QJE* 108(4) | 中（概念）；加密证据薄 |
| 6 订单流与 BTC 收益 | 订单流共同成分解释大部分收益方差，次日反转 | Makarov & Schoar 2020 *JFE* 135(2)（「约 80%」未核实） | 中 |
| 7 Price×OI×CVD 表 | 未找到任何检验；Bessembinder & Seguin 1993 只研究成交量 / OI 与波动率 | 从业者材料 | 无 |
| 8 状态序列 | 加密收益的 HMM 状态分类有文献；持仓 / 订单流序列无 | 贝叶斯 HMM（IRFA 系）；Jeon 2026 预印本 | 弱～中 |
| 9 强平 | Binance `forceOrder` 每币每 1000ms 只推最新一条 → 下界；Hyperliquid 无全市场强平频道；强平后走势无学术研究 | Binance / Hyperliquid 官方文档 | 数据质量已确认；预测力无 |
| 10 散户 / 大户仓位 | 散户倾向逆势且亏钱 | Dunbar & Owusu-Amoako 2023（ScienceDirect）；Baule, Frijns & Schlie 2024 *JFM* | 中；「逆向散户极值能赚钱」无直接回测 |
| 10 Binance Smart Money | 产品存在（Live Trading + Smart Signal），**无官方 API、无历史** | Binance 官网 / FAQ | 无法回测 |
| 10 Hyperliquid 鲸鱼 | 当前仓位公开可查，无现成历史；Hyperdash 只保留 7 天 | Hyperliquid 文档 | 无；只能从现在起自采 |
| 11 清算热力图 | 厂商用 OI + 假设杠杆分布**模型估算**，无外部验证 | CoinGlass 文档（API $29–699/月） | 无 |

## 2. 熔断阈值的参考数字

蒙特卡洛（20 万次，100 笔交易）得到的「最长连亏」分布：

| 胜率 | P50 | P90 | P95 | P99 |
|---|---|---|---|---|
| 30% | 10 | 15 | 17 | 21 |
| 35% | 9 | 13 | 14 | 18 |
| 40% | 7 | 11 | 12 | 15 |

即胜率 30% 时连亏 10 笔是常态，「三连亏熔断」在这类系统里毫无意义，印证了 §19。其余治理统计量：Deflated Sharpe Ratio 与最短样本长度 MinTRL（Bailey & López de Prado 2014）。

## 3. 对 V9 的含义

1. **有证据的只是框架外壳**：趋势跟随、止损、正偏态、波动率目标化、高 carry 预示崩盘。框架特有的机制层（Price×OI×CVD、状态序列、Price Impact、SMC 结构、Sweep 防御）**全部是未经检验的假设**，只能按 §20 的流程逐条检验。
2. **三处可以直接借用成熟定义，减少自由参数**：
   - 市场周期 / 趋势阶段 → Goulding–Harvey–Mazzoleni 的快慢动量四阶段；
   - 摆动结构（HH / HL / BOS）→ Directional Change 事件，只在确认后生效，不会重绘；
   - Price Impact → 滚动的「价格变动 ÷ 带方向的主动成交量」（Kyle's λ 代理）。
3. **Price×OI×CVD 表先当分类器检验**：用状态哑变量回归未来 1h / 4h / 1d 收益与波动，样本外成立再用于决策。
4. **不能回测的层不进第一版**：Smart Money、清算热力图、Hyperliquid 鲸鱼。第一版回测只用有历史的数据，这几层只能从现在开始自采，攒够样本后再做消融检验。
