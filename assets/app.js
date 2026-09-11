/* hlens-CryptoPlus front-end. Vanilla JS, no build step. */
(function () {
  "use strict";
  const CFG = window.HLENS_CONFIG || {};
  const $ = (s, el) => (el || document).querySelector(s);
  const h = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ------------------------------------------------------------------ i18n
  const I18N = {
    en: {
      "tagline": "the perp positioning prism",
      "nav.prism": "Prism", "nav.whales": "Whale lens", "nav.method": "Method",
      "join.cta": "Join the community",
      "hero.h1": "One coin. Every angle.",
      "hero.lede": "A prism splits white light into a spectrum. hlens splits a perpetual market into the people inside it: retail, top traders and on-chain whales, across Binance, OKX, Gate, Bitget, Bybit and Hyperliquid. When they disagree, that is the signal.",
      "status.loading": "loading data…", "status.updated": "updated", "status.ago": "ago", "status.whales": "whales tracked",
      "prism.h2": "The prism",
      "prism.sub": "Pick a coin. Each row is one facet of the same market. Bars are centred at 50% (or zero); the further from centre, the more one-sided the crowd.",
      "whales.h2": "Hyperliquid whale lens",
      "whales.sub": "Every position on Hyperliquid is on-chain and public. We scan the leaderboard for the largest and best-performing wallets, then read their live positions. No API key, no guessing.",
      "whales.bycoin": "Where the whale money sits", "whales.bycoin.hint": "Notional long vs short per coin, summed over tracked wallets.",
      "whales.toppos": "Largest open positions", "whales.toppos.hint": "Single positions ranked by notional. Liquidation price is the exchange's own estimate.",
      "whales.wallets": "Tracked wallets", "whales.wallets.hint": "Sorted by live account value. PnL and ROI come from the official leaderboard windows.",
      "join.h2": "Read the spectrum together",
      "join.p": "The dashboard shows the numbers. The community is where we argue about what they mean: whale flips, crowded funding, liquidation clusters, and honest post-mortems on trades that went wrong. No signals, no paid calls.",
      "join.empty": "Community links are being set up. Star the GitHub repo to get notified.",
      "method.h2": "Method, in plain words",
      "m1.h": "Funding, normalised", "m1.p": "Exchanges settle funding every 1h, 4h or 8h. We rescale everything to an 8-hour rate and weight the cross-exchange average by open interest, so a small venue cannot skew the number.",
      "m2.h": "Three kinds of positioning", "m2.p": "Retail = share of accounts net long (Binance, OKX, Gate, Bitget, Bybit). Top traders = Binance and Gate top accounts by position size. Whales = notional-weighted long share of tracked Hyperliquid wallets. These usually disagree; the gap is the point.",
      "m3.h": "Crowding score", "m3.p": "Average of three clipped terms: funding ÷ 0.05%, (retail long share − 50%) × 4, (taker buy share − 50%) × 6. Range −1 to +1. It is a description of the crowd, not a forecast.",
      "m4.h": "Whale selection", "m4.p": "Union of the top 90 wallets by account value, top 40 by monthly PnL, top 30 by weekly PnL and top 25 by monthly ROI among wallets over $1M. Positions under $100k are ignored.",
      "foot.disclaimer": "hlens is a free research tool. Nothing here is investment advice. Perpetual futures with leverage can lose more than your deposit. Data comes from public exchange APIs and can be delayed or wrong; verify before acting.",
      "foot.sources": "Sources: Binance USDⓈ-M, OKX swap, Gate and Bitget USDT futures, Bybit linear, Hyperliquid info API and leaderboard, alternative.me.",
      "foot.refresh": "Refreshed every {n} min by GitHub Actions.",
      // dynamic
      "f.retail": "Retail long share", "f.retail.s": "accounts net long · 5 venues avg",
      "f.top": "Top-trader long share", "f.top.s": "Binance + Gate top accounts",
      "f.taker": "Taker buy share", "f.taker.s": "aggressive buy ÷ total · 1h · 4 venues",
      "f.whale": "Whale long share", "f.whale.s": "Hyperliquid, notional-weighted",
      "f.funding": "Funding (8h)", "f.funding.s": "OI-weighted across venues",
      "f.oi": "Open interest", "f.oi.s": "sum of all venues, USD",
      "crowd": "Crowding", "crowd.short": "crowded short", "crowd.long": "crowded long", "crowd.neutral": "balanced",
      "div.title": "Retail vs whales",
      "div.text": "Retail accounts are {r} long; tracked whales are {w} long on Hyperliquid. Gap: {g} pts. Funding is {fs}, so right now {payer} are paying to hold.",
      "div.pos": "positive", "div.neg": "negative", "div.longs": "longs", "div.shorts": "shorts",
      "div.nowhale": "No tracked whale has a position ≥ $100k in this coin right now.",
      "ex.exchange": "Venue", "ex.price": "Price", "ex.funding": "Funding", "ex.f8": "8h-norm", "ex.apr": "APR", "ex.oi": "OI", "ex.vol": "24h vol", "ex.retail": "Retail long",
      "sp.price": "price · 7d", "sp.funding": "funding 8h · 7d", "sp.crowd": "crowding · 7d", "sp.wait": "history builds up over the next hours",
      "ins.div": "Biggest retail/whale gap", "ins.crowd": "Most crowded", "ins.whale": "Whale net bias", "ins.fund": "Highest funding",
      "ins.div.d": "retail {r} long vs whales {w} long",
      "ins.whale.d": "{l} long · {s} short across {n} wallets",
      "ins.fund.d": "{a}% annualised · {oi} OI",
      "tp.coin": "Coin", "tp.side": "Side", "tp.notional": "Notional", "tp.entry": "Entry", "tp.mark": "Mark", "tp.liq": "Liq. px", "tp.lev": "Lev", "tp.upnl": "uPnL", "tp.wallet": "Wallet",
      "w.rank": "#", "w.wallet": "Wallet", "w.acct": "Account", "w.pnl1d": "1d PnL", "w.pnl7d": "7d PnL", "w.pnl30d": "30d PnL", "w.roi30d": "30d ROI", "w.bias": "Bias", "w.pos": "Positions",
      "join.telegram": "Telegram", "join.discord": "Discord", "join.x": "X / Twitter", "join.wechat": "WeChat group",
      "mood.h": "Crowd mood", "mood.tag": "one lens, not a fact", "mood.fng": "Fear & Greed index (alternative.me)",
      "mood.hint": "A reading of how the crowd tends to behave in each regime. It fits some traders' experience and not others'. Use it as a mirror, not a forecast.",
      "mood.0": "Extreme fear (0–25): capitulation. Retail sells the lows, funding often goes negative, shorts get crowded.",
      "mood.1": "Fear (25–45): anxiety. Bounces get sold, position sizes shrink, whales quietly accumulate or wait.",
      "mood.2": "Neutral (45–55): indecision. Chop, low conviction, funding near zero, breakouts fail more often.",
      "mood.3": "Greed (55–75): FOMO builds. Retail leans long, funding turns positive, leverage climbs.",
      "mood.4": "Extreme greed (75–100): euphoria. Leverage stacks on one side; liquidation cascades become the main risk.",
      "err.load": "Could not load data. If you are running locally, serve the folder over HTTP (python3 -m http.server) and run scripts/fetch.py first.",
    },
    zh: {
      "tagline": "合约持仓棱镜",
      "nav.prism": "棱镜", "nav.whales": "大户透视", "nav.method": "方法说明",
      "join.cta": "加入社群",
      "hero.h1": "一个币，所有角度。",
      "hero.lede": "棱镜把一束白光分解成光谱。hlens 把一个合约市场分解成里面的人：散户、大账户、链上大户，横跨 Binance、OKX、Gate、Bitget、Bybit 和 Hyperliquid。当他们意见相左，那就是信号。",
      "status.loading": "加载数据中…", "status.updated": "更新于", "status.ago": "前", "status.whales": "个大户在追踪",
      "prism.h2": "棱镜",
      "prism.sub": "选一个币。每一行是同一个市场的一个切面。条形以 50%（或零）为中心，离中心越远，人群越一边倒。",
      "whales.h2": "Hyperliquid 大户透视",
      "whales.sub": "Hyperliquid 上每一笔仓位都在链上公开。我们扫描排行榜找出资金最大、表现最好的钱包，再读取它们的实时仓位。不需要 API key，不靠猜。",
      "whales.bycoin": "大户的钱在哪些币上", "whales.bycoin.hint": "按币种汇总追踪钱包的多空名义价值。",
      "whales.toppos": "最大的单笔持仓", "whales.toppos.hint": "按名义价值排序。强平价为交易所自己的估算。",
      "whales.wallets": "追踪中的钱包", "whales.wallets.hint": "按实时账户价值排序。盈亏和 ROI 来自官方排行榜窗口。",
      "join.h2": "一起读光谱",
      "join.p": "看板给你数字，社群是讨论数字含义的地方：大户翻转、拥挤的资金费率、爆仓密集区，以及对亏损交易的诚实复盘。不喊单，不收费带单。",
      "join.empty": "社群入口正在准备中。先 star GitHub 仓库，上线会通知。",
      "method.h2": "方法，说人话",
      "m1.h": "资金费率归一化", "m1.p": "各交易所每 1、4 或 8 小时结算一次资金费。我们把所有费率折算成 8 小时口径，并按持仓量加权求跨所平均，小平台无法扭曲结果。",
      "m2.h": "三种持仓视角", "m2.p": "散户 = 净多账户占比（Binance、OKX、Gate、Bitget、Bybit）。大账户 = Binance 与 Gate 按仓位排名靠前的账户。大户 = 追踪的 Hyperliquid 钱包按名义价值加权的多头占比。三者经常不一致，差距本身就是信息。",
      "m3.h": "拥挤度", "m3.p": "三项截断后取平均：资金费率 ÷ 0.05%，(散户多头占比 − 50%) × 4，(主动买入占比 − 50%) × 6。范围 −1 到 +1。它描述人群，不预测价格。",
      "m4.h": "大户筛选", "m4.p": "取账户价值前 90、月盈亏前 40、周盈亏前 30、以及百万美元以上账户中月 ROI 前 25 的并集。低于 10 万美元的仓位忽略。",
      "foot.disclaimer": "hlens 是免费研究工具，不构成任何投资建议。带杠杆的永续合约可能亏损超过本金。数据来自交易所公开接口，可能延迟或出错，行动前请自行核实。",
      "foot.sources": "数据源：Binance U 本位、OKX 永续、Gate 与 Bitget U 本位、Bybit 线性、Hyperliquid info 接口与排行榜、alternative.me。",
      "foot.refresh": "由 GitHub Actions 每 {n} 分钟刷新。",
      "f.retail": "散户多头占比", "f.retail.s": "净多账户占比 · 五所均值",
      "f.top": "大账户多头占比", "f.top.s": "Binance + Gate 大账户",
      "f.taker": "主动买入占比", "f.taker.s": "主动买量 ÷ 总量 · 1 小时",
      "f.whale": "大户多头占比", "f.whale.s": "Hyperliquid，名义价值加权",
      "f.funding": "资金费率（8h）", "f.funding.s": "各所按持仓量加权",
      "f.oi": "持仓量", "f.oi.s": "各所合计，美元",
      "crowd": "拥挤度", "crowd.short": "空头拥挤", "crowd.long": "多头拥挤", "crowd.neutral": "均衡",
      "div.title": "散户 vs 大户",
      "div.text": "散户账户 {r} 做多，Hyperliquid 追踪大户 {w} 做多，差距 {g} 个百分点。资金费率为{fs}，此刻是{payer}在付费持仓。",
      "div.pos": "正", "div.neg": "负", "div.longs": "多头", "div.shorts": "空头",
      "div.nowhale": "当前没有追踪大户在这个币上持有 ≥ 10 万美元的仓位。",
      "ex.exchange": "交易所", "ex.price": "价格", "ex.funding": "费率", "ex.f8": "8h 口径", "ex.apr": "年化", "ex.oi": "持仓量", "ex.vol": "24h 成交", "ex.retail": "散户多头",
      "sp.price": "价格 · 7 天", "sp.funding": "资金费率 8h · 7 天", "sp.crowd": "拥挤度 · 7 天", "sp.wait": "历史曲线将在接下来几小时逐步积累",
      "ins.div": "散户/大户分歧最大", "ins.crowd": "最拥挤", "ins.whale": "大户净方向", "ins.fund": "资金费率最高",
      "ins.div.d": "散户 {r} 做多 vs 大户 {w} 做多",
      "ins.whale.d": "多 {l} · 空 {s} · {n} 个钱包",
      "ins.fund.d": "年化 {a}% · 持仓 {oi}",
      "tp.coin": "币种", "tp.side": "方向", "tp.notional": "名义价值", "tp.entry": "开仓价", "tp.mark": "标记价", "tp.liq": "强平价", "tp.lev": "杠杆", "tp.upnl": "浮盈亏", "tp.wallet": "钱包",
      "w.rank": "#", "w.wallet": "钱包", "w.acct": "账户价值", "w.pnl1d": "1 日盈亏", "w.pnl7d": "7 日盈亏", "w.pnl30d": "30 日盈亏", "w.roi30d": "30 日 ROI", "w.bias": "净方向", "w.pos": "持仓",
      "join.telegram": "Telegram", "join.discord": "Discord", "join.x": "X / Twitter", "join.wechat": "微信群",
      "mood.h": "散户心理", "mood.tag": "一种视角，不是事实", "mood.fng": "恐惧贪婪指数（alternative.me）",
      "mood.hint": "对人群在不同情绪区间典型行为的一种解读。对一部分交易者来说很准，对另一部分未必。把它当镜子，不要当预测。",
      "mood.0": "极度恐惧（0–25）：投降。散户在低点割肉，资金费率常转负，空头开始拥挤。",
      "mood.1": "恐惧（25–45）：焦虑。反弹被卖，仓位缩小，大户悄悄吸筹或观望。",
      "mood.2": "中性（45–55）：犹豫。震荡、低信念、费率接近零，假突破更多。",
      "mood.3": "贪婪（55–75）：FOMO 累积。散户偏多，费率转正，杠杆升高。",
      "mood.4": "极度贪婪（75–100）：亢奋。杠杆堆在同一边，爆仓连锁成为主要风险。",
      "err.load": "数据加载失败。本地运行请先执行 scripts/fetch.py，并用 HTTP 方式打开目录（python3 -m http.server）。",
    },
  };
  let lang = "en";
  try {
    lang = localStorage.getItem("hlens.lang") || (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
    if (localStorage.getItem("hlens.lang")) lang = localStorage.getItem("hlens.lang");
  } catch (e) { /* ignore */ }
  const t = (k, vars) => {
    let s = (I18N[lang] && I18N[lang][k]) || I18N.en[k] || k;
    if (vars) for (const v in vars) s = s.replace(new RegExp("\\{" + v + "\\}", "g"), vars[v]);
    return s;
  };
  function applyI18n() {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.getAttribute("data-i18n")); });
    $("#lang").textContent = lang === "zh" ? "EN" : "中文";
    $("#foot-refresh").textContent = t("foot.refresh", { n: CFG.refreshMinutes || 30 });
  }
  $("#lang").addEventListener("click", () => {
    lang = lang === "zh" ? "en" : "zh";
    try { localStorage.setItem("hlens.lang", lang); } catch (e) { /* ignore */ }
    applyI18n(); if (STATE.data) renderAll();
  });

  // ------------------------------------------------------------------ format
  const fmtUsd = (v) => {
    if (v == null || isNaN(v)) return "–";
    const a = Math.abs(v), s = v < 0 ? "-" : "";
    if (a >= 1e9) return s + "$" + (a / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return s + "$" + (a / 1e6).toFixed(1) + "M";
    if (a >= 1e3) return s + "$" + (a / 1e3).toFixed(0) + "K";
    return s + "$" + a.toFixed(0);
  };
  const fmtPx = (v) => {
    if (v == null || isNaN(v)) return "–";
    if (v >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
    if (v >= 1) return v.toFixed(2);
    return v.toPrecision(3);
  };
  const fmtPct = (v, d) => (v == null || isNaN(v)) ? "–" : (v * 100).toFixed(d == null ? 1 : d) + "%";
  const fmtRate = (v) => (v == null || isNaN(v)) ? "–" : (v >= 0 ? "+" : "") + (v * 100).toFixed(4) + "%";
  const fmtSigned = (v, d) => (v == null || isNaN(v)) ? "–" : (v >= 0 ? "+" : "") + v.toFixed(d == null ? 1 : d);
  const short = (a) => a ? a.slice(0, 6) + "…" + a.slice(-4) : "–";
  const walletUrl = (a) => "https://hypurrscan.io/address/" + a;
  const cls = (v) => v > 0 ? "long" : v < 0 ? "short" : "";
  const ago = (ms) => {
    const m = Math.round((Date.now() - ms) / 60000);
    if (m < 1) return lang === "zh" ? "刚刚" : "just now";
    if (m < 60) return m + (lang === "zh" ? " 分钟" : " min");
    const hh = Math.floor(m / 60);
    return hh + (lang === "zh" ? " 小时 " : "h ") + (m % 60) + (lang === "zh" ? " 分钟" : "m");
  };

  // ------------------------------------------------------------------ pieces
  function centeredBar(share) { // share in 0..1, centre 0.5
    if (share == null) return '<div class="bar"><div class="mid"></div></div>';
    const d = (share - 0.5) * 100; // -50..50
    const w = Math.min(50, Math.abs(d));
    return `<div class="bar"><div class="mid"></div><div class="fill ${d >= 0 ? "pos" : "neg"}" style="width:${w}%"></div></div>`;
  }
  function zeroBar(v, scale) { // v in -scale..scale
    if (v == null) return '<div class="bar"><div class="mid"></div></div>';
    const w = Math.min(50, Math.abs(v) / scale * 50);
    return `<div class="bar"><div class="mid"></div><div class="fill ${v >= 0 ? "pos" : "neg"}" style="width:${w}%"></div></div>`;
  }
  function absBar(v, max) {
    const w = (v == null || !max) ? 0 : Math.min(100, v / max * 100);
    return `<div class="bar abs"><div class="fill" style="width:${w}%"></div></div>`;
  }
  function facet(label, sub, bar, val, valCls) {
    return `<div class="facet"><div class="lbl">${h(label)}<small>${h(sub)}</small></div>${bar}<div class="val ${valCls || ""}">${val}</div></div>`;
  }
  function spark(vals, color) {
    const xs = vals.filter((v) => v != null && !isNaN(v));
    if (xs.length < 3) return `<div class="hint">${t("sp.wait")}</div>`;
    const min = Math.min(...xs), max = Math.max(...xs), rng = (max - min) || 1;
    const W = 200, H = 44, pts = [];
    vals.forEach((v, i) => { if (v == null || isNaN(v)) return; pts.push((i / (vals.length - 1) * W).toFixed(1) + "," + (H - 4 - (v - min) / rng * (H - 8)).toFixed(1)); });
    return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><polyline fill="none" stroke="${color}" stroke-width="1.8" points="${pts.join(" ")}"/></svg>`;
  }

  // ------------------------------------------------------------------ render
  const STATE = { data: null, history: [], coin: null };

  function renderStatus(d) {
    const s = d.sources || {};
    const pills = Object.keys(s).map((k) => `<span class="pill ${String(s[k]).startsWith("ok") ? "pill-ok" : "pill-err"}" title="${h(s[k])}">${h(k)}</span>`);
    pills.unshift(`<span class="pill">${t("status.updated")} ${ago(d.generated_at)} ${t("status.ago")}</span>`);
    pills.push(`<span class="pill">${d.whales.count} ${t("status.whales")}</span>`);
    $("#status").innerHTML = pills.join("");
  }

  function renderInsights(d) {
    const coins = d.coins.filter((c) => c.price);
    const withWhale = coins.filter((c) => c.whale_long_share != null && c.retail_long_share != null && c.whale_n >= 2);
    const div = withWhale.slice().sort((a, b) => Math.abs(b.retail_long_share - b.whale_long_share) - Math.abs(a.retail_long_share - a.whale_long_share))[0];
    const crowd = coins.filter((c) => c.crowding != null).slice().sort((a, b) => Math.abs(b.crowding) - Math.abs(a.crowding))[0];
    const fund = coins.filter((c) => c.funding_8h != null).slice().sort((a, b) => Math.abs(b.funding_8h) - Math.abs(a.funding_8h))[0];
    let L = 0, S = 0; (d.whales.by_coin || []).forEach((c) => { L += c.long_usd; S += c.short_usd; });
    const cards = [];
    if (div) cards.push({ k: t("ins.div"), v: div.coin + " · " + fmtPct(Math.abs(div.retail_long_share - div.whale_long_share), 0), d: t("ins.div.d", { r: fmtPct(div.retail_long_share, 0), w: fmtPct(div.whale_long_share, 0) }) });
    if (crowd) cards.push({ k: t("ins.crowd"), v: crowd.coin + " · " + (crowd.crowding > 0 ? t("crowd.long") : t("crowd.short")), d: `${t("crowd")} ${fmtSigned(crowd.crowding, 2)}` });
    if (L + S > 0) cards.push({ k: t("ins.whale"), v: `<span class="${L >= S ? "long" : "short"}">${fmtPct(L / (L + S), 0)} ${lang === "zh" ? "多" : "long"}</span>`, d: t("ins.whale.d", { l: fmtUsd(L), s: fmtUsd(S), n: d.whales.count }) });
    if (fund) cards.push({ k: t("ins.fund"), v: fund.coin + " · " + fmtRate(fund.funding_8h), d: t("ins.fund.d", { a: fund.funding_annualized_pct.toFixed(1), oi: fmtUsd(fund.oi_total_usd) }) });
    $("#insight-grid").innerHTML = cards.map((c) => `<div class="ins"><div class="k">${c.k}</div><div class="v">${c.v}</div><div class="d">${c.d}</div></div>`).join("");
    $("#insight").classList.toggle("hidden", cards.length === 0);
  }

  function renderTabs(d) {
    $("#coin-tabs").innerHTML = d.coins.map((c) => `<button class="tab" role="tab" data-coin="${h(c.coin)}" aria-selected="${c.coin === STATE.coin}">${h(c.coin)}<span class="chg ${cls(c.chg24h_pct)}">${fmtSigned(c.chg24h_pct, 1)}%</span></button>`).join("");
    $("#coin-tabs").querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => { STATE.coin = b.dataset.coin; renderTabs(d); renderPrism(d); }));
  }

  function renderPrism(d) {
    const c = d.coins.find((x) => x.coin === STATE.coin) || d.coins[0];
    if (!c) return;
    const crowdTxt = c.crowding == null ? "–" : Math.abs(c.crowding) < 0.15 ? t("crowd.neutral") : c.crowding > 0 ? t("crowd.long") : t("crowd.short");
    const maxOi = Math.max(...d.coins.map((x) => x.oi_total_usd || 0));
    let divergence = "";
    if (c.whale_long_share != null && c.retail_long_share != null && c.whale_n >= 1) {
      const pos = (c.funding_8h || 0) >= 0;
      divergence = t("div.text", { r: fmtPct(c.retail_long_share, 0), w: fmtPct(c.whale_long_share, 0), g: Math.round(Math.abs(c.retail_long_share - c.whale_long_share) * 100), fs: t(pos ? "div.pos" : "div.neg"), payer: t(pos ? "div.longs" : "div.shorts") });
    } else divergence = t("div.nowhale");

    const hist = STATE.history.map((s) => (s.c || {})[c.coin]).filter(Boolean);
    const left = `
      <div class="card">
        <div class="price-row"><span class="price">${fmtPx(c.price)}</span><span class="${cls(c.chg24h_pct)}">${fmtSigned(c.chg24h_pct, 2)}% 24h</span><span class="muted">${fmtUsd(c.vol24h_total_usd)} vol</span></div>
        ${facet(t("f.retail"), t("f.retail.s"), centeredBar(c.retail_long_share), fmtPct(c.retail_long_share, 1), cls((c.retail_long_share || 0.5) - 0.5))}
        ${facet(t("f.top"), t("f.top.s"), centeredBar(c.top_trader_long_share), fmtPct(c.top_trader_long_share, 1), cls((c.top_trader_long_share || 0.5) - 0.5))}
        ${facet(t("f.taker"), t("f.taker.s"), centeredBar(c.taker_buy_share), fmtPct(c.taker_buy_share, 1), cls((c.taker_buy_share || 0.5) - 0.5))}
        ${facet(t("f.whale"), t("f.whale.s"), centeredBar(c.whale_long_share), c.whale_long_share == null ? "–" : fmtPct(c.whale_long_share, 1) + ` <span class="muted">(${c.whale_n})</span>`, cls((c.whale_long_share == null ? 0.5 : c.whale_long_share) - 0.5))}
        ${facet(t("f.funding"), t("f.funding.s"), zeroBar(c.funding_8h, 0.0005), fmtRate(c.funding_8h), cls(c.funding_8h))}
        ${facet(t("f.oi"), t("f.oi.s"), absBar(c.oi_total_usd, maxOi), fmtUsd(c.oi_total_usd))}
        <div class="gauge">
          <div style="display:flex;justify-content:space-between"><strong>${t("crowd")}</strong><span class="${cls(c.crowding)}">${crowdTxt} · ${fmtSigned(c.crowding, 2)}</span></div>
          <div class="track"><div class="needle" style="left:${((c.crowding == null ? 0 : c.crowding) + 1) / 2 * 100}%"></div></div>
          <div class="scale"><span>−1 ${t("crowd.short")}</span><span>0</span><span>+1 ${t("crowd.long")}</span></div>
        </div>
        <div class="divergence"><strong>${t("div.title")}</strong> · ${divergence}</div>
        <div class="sparks">
          <div class="spark-box"><div class="k">${t("sp.price")}</div>${spark(hist.map((x) => x[0]), "#22d3ee")}</div>
          <div class="spark-box"><div class="k">${t("sp.funding")}</div>${spark(hist.map((x) => x[1]), "#fbbf24")}</div>
          <div class="spark-box"><div class="k">${t("sp.crowd")}</div>${spark(hist.map((x) => x[5]), "#8b5cf6")}</div>
        </div>
      </div>`;
    const rows = c.exchanges.map((e) => `<tr><td class="l">${h(e.exchange)}</td><td class="mono">${fmtPx(e.price)}</td><td class="mono ${cls(e.funding)}">${fmtRate(e.funding)} <span class="muted">/${e.funding_interval_h}h</span></td><td class="mono ${cls(e.funding_8h)}">${fmtRate(e.funding_8h)}</td><td class="mono">${e.funding_8h == null ? "–" : (e.funding_8h * 1095 * 100).toFixed(1) + "%"}</td><td class="mono">${fmtUsd(e.oi_usd)}</td><td class="mono">${fmtUsd(e.vol24h_usd)}</td><td class="mono">${fmtPct(e.retail_long_share, 1)}</td></tr>`).join("");
    const right = `
      <div class="card">
        <h3>${lang === "zh" ? "分交易所" : "By venue"}</h3>
        <div class="table-wrap"><table><thead><tr><th>${t("ex.exchange")}</th><th>${t("ex.price")}</th><th>${t("ex.funding")}</th><th>${t("ex.f8")}</th><th>${t("ex.apr")}</th><th>${t("ex.oi")}</th><th>${t("ex.vol")}</th><th>${t("ex.retail")}</th></tr></thead><tbody>${rows}</tbody></table></div>
        <p class="hint" style="margin-top:10px">${lang === "zh" ? "Hyperliquid 每小时结算，表中已折算成 8 小时口径以便比较。年化 = 8h 费率 × 1095。" : "Hyperliquid settles hourly; the 8h column rescales it for comparison. APR = 8h rate × 1095."}</p>
      </div>`;
    const fng = (d.macro || {}).fng;
    let mood = "";
    if (fng) {
      const idx = fng.value <= 25 ? 0 : fng.value <= 45 ? 1 : fng.value <= 55 ? 2 : fng.value <= 75 ? 3 : 4;
      const items = [0, 1, 2, 3, 4].map((i) => `<li class="${i === idx ? "now" : ""}">${t("mood." + i)}</li>`).join("");
      mood = `
      <div class="card">
        <h3>${t("mood.h")}<span class="tag">${t("mood.tag")}</span></h3>
        <div class="mood"><div class="big ${fng.value <= 45 ? "short" : fng.value >= 55 ? "long" : "warn"}">${fng.value}</div><div><div class="lab">${h(fng.label || "")}</div><div class="hint">${t("mood.fng")}</div></div></div>
        <div class="mood-track"><div class="needle" style="left:${fng.value}%"></div></div>
        ${spark(fng.history || [], "#fbbf24")}
        <ul class="mood-list">${items}</ul>
        <p class="hint" style="margin-top:10px">${t("mood.hint")}</p>
      </div>`;
    }
    $("#prism-panel").innerHTML = left + "<div>" + right + mood + "</div>";
  }

  function renderWhales(d) {
    const w = d.whales;
    const by = (w.by_coin || []).slice(0, 14);
    const max = Math.max(...by.map((c) => c.total_usd), 1);
    $("#whale-bycoin").innerHTML = by.map((c) => {
      const tw = c.total_usd / max * 100, lw = c.long_usd / c.total_usd * 100;
      return `<div class="wc"><strong>${h(c.coin)}</strong><div class="stack" style="width:${tw}%"><div class="l" style="width:${lw}%"></div><div class="s" style="width:${100 - lw}%"></div></div><div class="num"><span class="long">${fmtUsd(c.long_usd)}</span> / <span class="short">${fmtUsd(c.short_usd)}</span></div></div>`;
    }).join("") || `<p class="hint">–</p>`;

    const markOf = {}; d.coins.forEach((c) => { const hl = c.exchanges.find((e) => e.exchange === "hyperliquid"); if (hl) markOf[c.coin] = hl.price; });
    $("#whale-toppos").innerHTML = `<thead><tr><th>${t("tp.coin")}</th><th>${t("tp.side")}</th><th>${t("tp.notional")}</th><th>${t("tp.entry")}</th><th>${t("tp.liq")}</th><th>${t("tp.lev")}</th><th>${t("tp.upnl")}</th><th>${t("tp.wallet")}</th></tr></thead><tbody>` +
      (w.top_positions || []).slice(0, 20).map((p) => `<tr><td class="l"><strong>${h(p.coin)}</strong></td><td><span class="side ${p.side}">${p.side}</span></td><td class="mono">${fmtUsd(p.notional)}</td><td class="mono">${fmtPx(p.entry)}</td><td class="mono">${fmtPx(p.liq)}</td><td class="mono">${p.lev || "–"}x${p.lev_type === "isolated" ? ' <span class="muted">iso</span>' : ""}</td><td class="mono ${cls(p.upnl)}">${fmtUsd(p.upnl)}</td><td><a class="addr" href="${walletUrl(p.address)}" target="_blank" rel="noopener">${p.name ? h(p.name) : short(p.address)}</a></td></tr>`).join("") + "</tbody>";

    $("#whale-wallets").innerHTML = `<thead><tr><th>${t("w.rank")}</th><th>${t("w.wallet")}</th><th>${t("w.acct")}</th><th>${t("w.pnl1d")}</th><th>${t("w.pnl7d")}</th><th>${t("w.pnl30d")}</th><th>${t("w.roi30d")}</th><th>${t("w.bias")}</th><th>${t("w.pos")}</th></tr></thead><tbody>` +
      (w.wallets || []).slice(0, 40).map((x, i) => {
        const tot = x.long_notional + x.short_notional;
        const bias = tot ? x.long_notional / tot : null;
        const chips = x.positions.slice(0, 6).map((p) => `<span class="chip ${p.side}">${h(p.coin)} ${p.side === "long" ? "▲" : "▼"} ${fmtUsd(p.notional)}</span>`).join("") + (x.positions.length > 6 ? `<span class="chip">+${x.positions.length - 6}</span>` : "");
        return `<tr><td class="l muted">${i + 1}</td><td class="l"><a class="addr" href="${walletUrl(x.address)}" target="_blank" rel="noopener">${x.name ? h(x.name) : short(x.address)}</a></td><td class="mono">${fmtUsd(x.account_value)}</td><td class="mono ${cls(x.pnl_day)}">${fmtUsd(x.pnl_day)}</td><td class="mono ${cls(x.pnl_week)}">${fmtUsd(x.pnl_week)}</td><td class="mono ${cls(x.pnl_month)}">${fmtUsd(x.pnl_month)}</td><td class="mono ${cls(x.roi_month)}">${fmtPct(x.roi_month, 1)}</td><td>${bias == null ? '<span class="muted">flat</span>' : `<span class="${bias >= 0.5 ? "long" : "short"}">${fmtPct(bias, 0)} L</span>`}</td><td class="l">${chips || '<span class="muted">–</span>'}</td></tr>`;
      }).join("") + "</tbody>";
  }

  function renderJoin() {
    const c = CFG.community || {};
    const links = [];
    if (c.telegram) links.push(`<a class="btn btn-primary" href="${h(c.telegram)}" target="_blank" rel="noopener">${t("join.telegram")}</a>`);
    if (c.discord) links.push(`<a class="btn" href="${h(c.discord)}" target="_blank" rel="noopener">${t("join.discord")}</a>`);
    if (c.x) links.push(`<a class="btn" href="${h(c.x)}" target="_blank" rel="noopener">${t("join.x")}</a>`);
    if (c.wechat) links.push(`<div><div class="hint">${t("join.wechat")}</div><img src="${h(c.wechat)}" alt="WeChat QR"></div>`);
    $("#join-links").innerHTML = links.join("");
    $("#join-empty").classList.toggle("hidden", links.length > 0);
    ["#gh-link", "#gh-link-2"].forEach((s) => { $(s).href = CFG.github || "#"; });
  }

  function renderAll() {
    const d = STATE.data;
    renderStatus(d); renderInsights(d); renderTabs(d); renderPrism(d); renderWhales(d); renderJoin();
  }

  // ------------------------------------------------------------------ boot
  applyI18n(); renderJoin();
  const base = CFG.dataBase || "data/";
  const bust = "?t=" + Math.floor(Date.now() / 300000); // 5-min cache bust
  Promise.all([
    fetch(base + "latest.json" + bust).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }),
    fetch(base + "history.json" + bust).then((r) => r.ok ? r.json() : []).catch(() => []),
  ]).then(([latest, history]) => {
    STATE.data = latest; STATE.history = Array.isArray(history) ? history : [];
    STATE.coin = latest.coins[0] && latest.coins[0].coin;
    renderAll();
  }).catch((e) => {
    console.error(e);
    $("#status").innerHTML = `<span class="pill pill-err">${t("err.load")}</span>`;
  });
})();
