# V9 回测数据可行性（2026-09-25）

> 目标：BTC 永续 4H / 1H / 15m，2020–2026。「已核实」= 本次直接列出 Binance 公开数据桶（`data.binance.vision`）文件或下载样本确认；其余来自子代理检索。

## 1. Binance 公开档案（免费）

| 数据集 | 路径 | 内容 | 起始（BTCUSDT） | 状态 |
|---|---|---|---|---|
| 现货 K 线 | `spot/monthly/klines` | OHLCV + 主动买入量（可算 CVD 代理） | 2017-08 | 已核实 |
| 现货逐笔（aggTrades） | `spot/monthly/aggTrades` | 价格、数量、主动方 | 2017-08 | 已核实 |
| 永续 K 线 | `futures/um/monthly/klines` | 同上 | 2020-01 | 已核实 |
| 永续逐笔（aggTrades） | `futures/um/monthly/aggTrades` | 同上（逐笔级 CVD） | 2020-01 | 已核实 |
| 资金费率 | `futures/um/monthly/fundingRate` | 每次结算 | 2020-01 | 已核实 |
| 溢价指数 K 线 | `futures/um/monthly/premiumIndexKlines` | 基差代理 | 2020-01 | 已核实 |
| **metrics** | `futures/um/daily/metrics` | OI、OI 价值、大户账户比、大户持仓比、全市场账户比、主动买卖量比；**5 分钟** | **2020-09-01**，更新到 2026-09-24 | 已核实（早期文件有重复行，需去重） |
| 盘口深度 bookDepth | `futures/um/daily/bookDepth` | 按价差档位的深度快照 | 2023-01-01 | 已核实 |
| 最优报价 bookTicker | `futures/um/daily/bookTicker` | 买一卖一 | 2023-05-16 | 已核实 |
| **强平快照** | `futures/um/daily/liquidationSnapshot` | — | **USDT 本位：无** | 已核实 |
| 强平快照（币本位） | `futures/cm/daily/liquidationSnapshot/BTCUSD_PERP` | 限流快照 | 2023-06-25 → 2024-10-14，之后停更 | 已核实 |

REST 接口：`/futures/data/*`（OI 历史、多空比）只给最近 30 天；`/fapi/v1/allForceOrders` 已停用；资金费率可分页取全历史。

## 2. 付费与第三方

| 来源 | 能补什么 | 价格 | 备注 |
|---|---|---|---|
| Tardis.dev | 逐笔、L2/L3 盘口、**强平**、OI，含 Binance 与 Hyperliquid | 自助约 $35–879/月，最低约 $300 | 深度历史的覆盖与价格未核实 |
| CoinGlass API | OI、资金费率、强平、清算热力图（模型估算） | $29 / $79 / $299 / $699 每月 | 历史回看长度随档位，最长约 1 年 |
| Coinalyze | OI、资金费率、强平 | 免费 API | 日内数据只保留约 1500–2000 个点，只适合日线长历史 |
| CryptoQuant | 链上 + 部分衍生品 | API 需 Pro 档（约 $99/月起） | |
| Kaiko / Amberdata / Laevitas / Velo | 机构级 | 无公开自助价格 | 不适合个人 |

Hyperliquid：永续 2023 年才上线；`candleSnapshot` 只返回最近 5000 根；节点数据在请求方付费的 S3 桶，全市场强平需要从成交里重建。

## 3. V9 各层能否回测

| V9 层 | 数据 | 可回测区间 | 结论 |
|---|---|---|---|
| Price Core / 多周期结构 | 现货 + 永续 K 线 | 2020-01 起（现货 2017-08 起） | ✅ 免费 |
| 现货 / 合约 CVD | K 线主动买入量（代理）或 aggTrades（逐笔） | 2020-01 起 | ✅ 免费；逐笔文件很大 |
| OI / ΔOI | metrics（5 分钟） | 2020-09-01 起 | ✅ 免费 |
| Funding / Basis | fundingRate + premiumIndexKlines | 2020-01 起 | ✅ 免费 |
| 大户 / 全市场多空比 | metrics | 2020-09-01 起 | ✅ 免费 |
| 盘口流动性 | bookDepth | 2023-01 起 | ⚠️ 只有约 3.7 年 |
| **强平（Binance BTCUSDT）** | 无免费历史 | — | ❌ 需 Tardis（付费）或从现在起自采 |
| Binance Smart Money | 无 API、无历史 | — | ❌ 只能人工观察 / 自采 |
| 清算热力图 | 厂商模型估算 | 付费、回看长度有限 | ❌ 且未经验证 |
| Hyperliquid 鲸鱼 | 只有当前状态 | — | ❌ 从现在起自采 |

## 4. 建议

1. **第一版回测只用 ✅ 数据**：K 线、aggTrades、metrics、资金费率、溢价指数，BTCUSDT 永续 + 现货，**2020-09-01 → 至今**（约 6 年，覆盖 2021 牛市、2022 熊市、2023–25 震荡期以及之后的行情）。全部免费。
2. **强平层**：第一版以 OI 骤降 + 价格方向做「强平代理」，与真实强平的偏差明确标为假设；要不要为真实强平历史买 Tardis，等第一版证明机制层有增量后再决定。
3. **Smart Money / 热力图 / 鲸鱼**：从现在开始自采，不进第一版；样本够了再做消融检验。
4. **盘口层**：只能在 2023 年以后单独检验，不能和全样本结论混在一起。
