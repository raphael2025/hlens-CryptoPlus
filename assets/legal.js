/* hlens-CryptoPlus static legal/info pages (terms, privacy, disclaimer, sources).
   Vanilla JS, no build step. Shares the "hlens.lang" localStorage key with
   assets/app.js so the language choice is consistent across the whole site,
   but is otherwise independent — it does not touch index.html. */
(function () {
  "use strict";
  const CFG = window.HLENS_CONFIG || {};
  const $ = (s, el) => (el || document).querySelector(s);

  const I18N = {
    en: {
      "nav.home": "Home",
      "foot.disclaimer": "hlens is a free research tool. Nothing here is investment advice. Perpetual futures with leverage can lose more than your deposit. Data comes from public exchange APIs and can be delayed or wrong; verify before acting.",
      "foot.refresh": "Refreshed every {n} min by GitHub Actions.",
      "foot.nav.home": "Home", "foot.nav.terms": "Terms", "foot.nav.privacy": "Privacy", "foot.nav.disclaimer": "Disclaimer", "foot.nav.sources": "Data sources",
      "back.home": "← Back to hlens",
      "contact.h": "Contact",
      "contact.p": "Questions, or an address-removal request:",

      "terms.title": "Terms of use",
      "terms.h1": "Terms of use",
      "terms.updated": "Last updated: 2026-09-12",
      "terms.s1.h": "What hlens is",
      "terms.s1.p": "hlens is a free, open-source research tool that describes the current state of perpetual-futures markets — funding, open interest and positioning — across several exchanges and Hyperliquid. It only describes. It does not recommend anything, and it contains no entry or exit signals, price targets or trade calls.",
      "terms.s2.h": "Not financial advice",
      "terms.s2.p": "Nothing on this site, in its community channels, or in anything it publishes is investment, legal or tax advice. Leveraged perpetual futures are high-risk instruments: you can lose more than the amount you deposited. Any decision you make is your own responsibility.",
      "terms.s3.h": "Eligibility and local law",
      "terms.s3.p": "hlens is for adults (18+) only. Derivatives market-data services are restricted or prohibited in some countries. You are responsible for checking and following the laws that apply to you, and must not use this site where doing so would break them.",
      "terms.s4.h": "No accounts today; no warranty on data",
      "terms.s4.p": "The site is currently static and does not require an account — see Privacy for details. Market data is pulled from public exchange and Hyperliquid APIs and remains the property of those providers; it is shown as-is and can be delayed, incomplete or wrong. We give no warranty of accuracy and no service-level agreement: the site or its data can be unavailable or stale without notice.",
      "terms.s5.h": "License",
      "terms.s5.p": "The hlens code is open source under the MIT license (see the LICENSE file in the repository). Market data itself is not covered by that license and remains the property of the exchanges and services that publish it.",
      "terms.s6.h": "Changes to these terms",
      "terms.s6.p": "These terms may change as the project grows, for example when accounts are introduced. Material changes will update the date at the top of this page.",
      "terms.s7.h": "Contact",
      "terms.s7.p": "Questions about these terms:",

      "privacy.title": "Privacy policy",
      "privacy.h1": "Privacy policy",
      "privacy.updated": "Last updated: 2026-09-12",
      "privacy.s1.h": "Today: no accounts, no personal data",
      "privacy.s1.p": "hlens is a static website. It does not require sign-up, does not use cookies, and does not run analytics or advertising trackers. We do not collect names, emails or IP addresses through the site.",
      "privacy.s2.h": "Local-only features",
      "privacy.s2.p": "Planned features such as \"my position\" will let you type in a position to see numbers relevant to it. That data is written only to your browser's localStorage; it stays on your device and is never uploaded to hlens or anyone else. Clearing your browser's site data deletes it.",
      "privacy.s3.h": "Public blockchain data",
      "privacy.s3.p": "Hyperliquid wallet addresses and their positions are public on-chain data, visible to anyone through Hyperliquid itself. hlens displays addresses and, where known, labels for large or notable wallets, solely to describe market positioning. If you own an address shown here and want it removed from display, email us below and we will act on the request.",
      "privacy.s4.h": "If accounts are introduced (GDPR)",
      "privacy.s4.p": "Should hlens later offer accounts — for saved alerts or API keys, for example — you will be able to request access to, or deletion of, the personal data we hold about you, in line with GDPR. This page will be updated with the process before accounts launch.",
      "privacy.s5.h": "Third-party links",
      "privacy.s5.p": "Links to exchanges or other outside sites may appear on the Data sources page, clearly marked as such. We do not control those sites' privacy practices; check their own policies before using them.",
      "privacy.s6.h": "Contact",
      "privacy.s6.p": "Privacy questions or address-removal requests:",

      "disc.title": "Disclaimer",
      "disc.h1": "Disclaimer",
      "disc.updated": "Last updated: 2026-09-12",
      "disc.s1.h": "Research tool, not advice",
      "disc.s1.p": "hlens describes the current state of a market — funding, open interest, and who is long or short — using public data. It does not tell you to buy, sell, enter or exit anything, and it does not predict prices. Any trading decision you make after reading it is your own.",
      "disc.s2.h": "Leverage risk",
      "disc.s2.p": "Perpetual futures are leveraged derivatives. Losses can exceed your original deposit, and positions can be liquidated automatically. Only trade with money and leverage you can afford to lose entirely.",
      "disc.s3.h": "Data quality",
      "disc.s3.p": "Data is fetched from public exchange and Hyperliquid APIs on a best-effort schedule — see Data sources. It can be delayed, incomplete or wrong; some figures, such as liquidation totals, are known lower bounds rather than exact totals. Verify anything important before acting on it, and do not rely on hlens as your only source.",
      "disc.s4.h": "Eligibility",
      "disc.s4.p": "This site is intended for users aged 18 or over. It is not directed at, and must not be used in, any place where access to derivatives market-data services is restricted by law; you are responsible for complying with the rules of your own jurisdiction.",
      "disc.s5.h": "Community",
      "disc.s5.p": "Community channels linked from hlens are for discussing public data and past trades, not for signals or paid trading calls. No one associated with hlens will ever ask you to send funds, share private keys, or follow a specific trade.",
      "disc.s6.h": "No affiliation",
      "disc.s6.p": "hlens is an independent project. It is not affiliated with, endorsed by, or acting on behalf of Binance, OKX, Gate, Bitget, Bybit, Hyperliquid, or any other exchange named on this site.",

      "src.title": "Data sources",
      "src.h1": "Data sources",
      "src.updated": "Last updated: 2026-09-12",
      "src.intro": "Every number on hlens comes from a public API, fetched by a script that runs on a schedule (not continuously). This page lists what we currently pull, how often, and each source's known limits.",
      "src.th.source": "Source", "src.th.take": "What we take", "src.th.refresh": "Refresh", "src.th.limits": "Known limits",
      "src.binance.take": "Mark price, funding rate, open interest, long/short account ratios, liquidation stream (via the www.binance.com mirror, since fapi.binance.com blocks US IP ranges — including GitHub Actions' default runners)",
      "src.binance.limits": "The liquidation WebSocket throttles to at most one print per symbol per second, so our liquidation totals are a lower bound, not an exact count.",
      "src.bybit.take": "Mark price, funding rate, open interest, tickers",
      "src.bybit.limits": "Bybit's API returns 403 to US IP ranges, including GitHub Actions' default runners. When that happens, Bybit figures are shown as unavailable rather than guessed or estimated.",
      "src.okx.take": "Mark price, funding rate, open interest, long/short account ratios",
      "src.okx.limits": "Some long/short-ratio and taker-volume endpoints return no data for lower-volume coins.",
      "src.gate.take": "Mark price, funding rate, open interest, long/short ratios, hourly liquidation totals",
      "src.gate.limits": "Ratio and liquidation coverage varies by coin; some lower-volume coins are missing data.",
      "src.bitget.take": "Mark price, funding rate, open interest, long/short ratios",
      "src.bitget.limits": "Ratio endpoints are missing for some lower-volume coins.",
      "src.hl.take": "Positions, funding, open interest and asset context from the public /info endpoint; candidate wallets from the public leaderboard",
      "src.hl.limits": "The leaderboard endpoint is not documented by Hyperliquid; it is the same one their own front-end uses, and is read only.",
      "src.fng.take": "Fear & Greed index",
      "src.fng.limits": "Third-party sentiment index; the methodology behind it is alternative.me's, not ours.",
      "src.cadence": "every 30 min (GitHub Actions schedule)",
      "src.prod.h": "Production, later",
      "src.prod.p": "This is what today's static site fetches. A future production backend (see the roadmap) is planned to use a wider set of endpoints — including WebSocket feeds, longer history and more fields — documented in full in 06-DATA-SOURCES.md in the GitHub repository.",
      "src.ref.h": "Referral / affiliate links",
      "src.ref.p": "hlens does not currently run any exchange referral or affiliate links. If that changes, any such link will be clearly marked \"referral link\" and will only ever appear on this page — never in the dashboard, whale lens, or community channels.",
      "src.repo.h": "Repository",
      "src.repo.p": "Full endpoint list, rate-limit budgets and fetch code:",
    },
    zh: {
      "nav.home": "首页",
      "foot.disclaimer": "hlens 是免费研究工具，不构成任何投资建议。带杠杆的永续合约可能亏损超过本金。数据来自交易所公开接口，可能延迟或出错，行动前请自行核实。",
      "foot.refresh": "由 GitHub Actions 每 {n} 分钟刷新。",
      "foot.nav.home": "首页", "foot.nav.terms": "服务条款", "foot.nav.privacy": "隐私政策", "foot.nav.disclaimer": "免责声明", "foot.nav.sources": "数据来源",
      "back.home": "← 返回 hlens",
      "contact.h": "联系方式",
      "contact.p": "问题或地址移除请求，请联系：",

      "terms.title": "服务条款",
      "terms.h1": "服务条款",
      "terms.updated": "最后更新：2026-09-12",
      "terms.s1.h": "hlens 是什么",
      "terms.s1.p": "hlens 是一个免费、开源的研究工具，描述永续合约市场当前的状态——资金费率、持仓量与多空持仓——覆盖多家交易所与 Hyperliquid。它只做描述，不提出任何建议，不包含开仓、平仓信号、目标价或喊单内容。",
      "terms.s2.h": "不构成投资建议",
      "terms.s2.p": "本网站、社群频道及其发布的任何内容都不构成投资、法律或税务建议。带杠杆的永续合约风险很高，亏损可能超过本金。你做出的任何决定都由你自己负责。",
      "terms.s3.h": "使用资格与当地法律",
      "terms.s3.p": "hlens 仅供 18 岁以上成年人使用。部分国家限制或禁止衍生品市场数据服务。你需自行确认并遵守适用于你的法律，若使用本站会违反当地法律，请不要使用。",
      "terms.s4.h": "目前无账户，数据不作担保",
      "terms.s4.p": "本站目前是静态网站，不需要注册账户，详见隐私政策。行情数据取自交易所与 Hyperliquid 的公开接口，归属于这些服务方；数据按原样展示，可能延迟、不完整或有误。我们不对准确性作任何担保，也不提供服务等级承诺（SLA）：网站或数据可能在无通知的情况下不可用或过时。",
      "terms.s5.h": "许可协议",
      "terms.s5.p": "hlens 代码以 MIT 协议开源（见仓库中的 LICENSE 文件）。行情数据本身不在该协议范围内，仍归发布方所有。",
      "terms.s6.h": "条款变更",
      "terms.s6.p": "随着项目发展（例如引入账户功能），本条款可能更新。重大变更会更新本页顶部日期。",
      "terms.s7.h": "联系方式",
      "terms.s7.p": "关于本条款的问题，请联系：",

      "privacy.title": "隐私政策",
      "privacy.h1": "隐私政策",
      "privacy.updated": "最后更新：2026-09-12",
      "privacy.s1.h": "现状：无账户，不收集个人数据",
      "privacy.s1.p": "hlens 是一个静态网站，不需要注册，不使用 Cookie，也不运行任何分析或广告跟踪脚本。我们不通过本站收集姓名、邮箱或 IP 地址。",
      "privacy.s2.h": "仅存于本地的功能",
      "privacy.s2.p": "计划中的“我的仓位”等功能会让你输入仓位信息以查看相关数字。这些数据只写入你浏览器的 localStorage，只留在你的设备上，绝不会上传给 hlens 或任何第三方。清除浏览器网站数据即可删除。",
      "privacy.s3.h": "公开链上数据",
      "privacy.s3.p": "Hyperliquid 钱包地址及其仓位是公开的链上数据，任何人都可以在 Hyperliquid 官方渠道看到。hlens 展示地址，以及已知的大户或知名钱包标签，目的仅在于描述市场持仓。如果你是本站展示的某个地址的所有者，希望将其从展示中移除，请通过下方邮箱联系我们，我们会处理该请求。",
      "privacy.s4.h": "若未来引入账户（GDPR）",
      "privacy.s4.p": "如果 hlens 未来提供账户功能（例如保存告警或 API key），你将可以按 GDPR 要求访问或删除我们持有的与你相关的个人数据。账户功能上线前，本页会更新具体流程。",
      "privacy.s5.h": "第三方链接",
      "privacy.s5.p": "指向交易所或其他外部网站的链接可能出现在“数据来源”页面，并会明确标注。我们不控制这些网站的隐私做法，使用前请查阅它们各自的政策。",
      "privacy.s6.h": "联系方式",
      "privacy.s6.p": "隐私问题或地址移除请求，请联系：",

      "disc.title": "免责声明",
      "disc.h1": "免责声明",
      "disc.updated": "最后更新：2026-09-12",
      "disc.s1.h": "研究工具，不是建议",
      "disc.s1.p": "hlens 用公开数据描述市场当前的状态——资金费率、持仓量，以及谁在做多、谁在做空。它不会告诉你买入、卖出、开仓或平仓，也不预测价格。你阅读后做出的任何交易决定都由你自己负责。",
      "disc.s2.h": "杠杆风险",
      "disc.s2.p": "永续合约是带杠杆的衍生品，亏损可能超过本金，仓位可能被自动强平。只用你能完全承受损失的资金和杠杆倍数交易。",
      "disc.s3.h": "数据质量",
      "disc.s3.p": "数据按尽力而为的节奏从交易所与 Hyperliquid 的公开接口抓取，详见“数据来源”页。数据可能延迟、不完整或有误；部分数字（如爆仓总量）是已知的下界，而非精确总量。行动前请自行核实重要信息，不要把 hlens 作为唯一信息来源。",
      "disc.s4.h": "使用资格",
      "disc.s4.p": "本站面向 18 岁以上用户。若某地法律限制访问衍生品市场数据服务，本站不面向该地区，也不应在该地区使用；你需自行遵守所在司法辖区的规定。",
      "disc.s5.h": "社群",
      "disc.s5.p": "hlens 链接的社群频道用于讨论公开数据与过往交易，不提供信号或付费带单。任何以 hlens 名义要求你转账、分享私钥或跟随特定交易的行为都不是我们所为。",
      "disc.s6.h": "无关联声明",
      "disc.s6.p": "hlens 是独立项目，与本站提及的 Binance、OKX、Gate、Bitget、Bybit、Hyperliquid 或任何其他交易所均无关联，不受其背书，也不代表其行事。",

      "src.title": "数据来源",
      "src.h1": "数据来源",
      "src.updated": "最后更新：2026-09-12",
      "src.intro": "hlens 上的每一个数字都来自公开接口，由一个按计划定时运行（而非持续运行）的脚本抓取。本页列出我们目前抓取的内容、刷新频率，以及每个来源的已知限制。",
      "src.th.source": "来源", "src.th.take": "我们获取的内容", "src.th.refresh": "刷新频率", "src.th.limits": "已知限制",
      "src.binance.take": "标记价、资金费率、持仓量、多空账户比、爆仓数据流（通过 www.binance.com 镜像，因为 fapi.binance.com 屏蔽美国 IP 段，包括 GitHub Actions 的默认运行器）",
      "src.binance.limits": "爆仓 WebSocket 每个交易对每秒最多推送一条，因此我们的爆仓总量是下界，不是精确计数。",
      "src.bybit.take": "标记价、资金费率、持仓量、行情",
      "src.bybit.limits": "Bybit 接口对美国 IP 段（包括 GitHub Actions 默认运行器）返回 403。遇到这种情况时，Bybit 相关数字会显示为不可用，而不是猜测或估算。",
      "src.okx.take": "标记价、资金费率、持仓量、多空账户比",
      "src.okx.limits": "部分多空比与主动买卖量接口对成交量较低的币种不返回数据。",
      "src.gate.take": "标记价、资金费率、持仓量、多空比、每小时爆仓总量",
      "src.gate.limits": "多空比与爆仓数据的覆盖因币种而异，部分低成交量币种缺数据。",
      "src.bitget.take": "标记价、资金费率、持仓量、多空比",
      "src.bitget.limits": "部分低成交量币种缺少多空比数据。",
      "src.hl.take": "通过公开 /info 接口获取仓位、资金费率、持仓量与资产上下文；通过公开排行榜获取候选钱包",
      "src.hl.limits": "排行榜接口 Hyperliquid 官方未作文档说明，与其官方前端使用的是同一个接口，仅作只读使用。",
      "src.fng.take": "恐惧贪婪指数",
      "src.fng.limits": "第三方情绪指数，背后的计算方法属于 alternative.me，与 hlens 无关。",
      "src.cadence": "每 30 分钟（GitHub Actions 定时任务）",
      "src.prod.h": "生产环境（后续阶段）",
      "src.prod.p": "以上是当前静态网站实际抓取的内容。计划中的生产后端（见路线图）将使用更完整的接口集合——包括 WebSocket 数据流、更长的历史与更多字段——完整列表见 GitHub 仓库中的 06-DATA-SOURCES.md。",
      "src.ref.h": "返佣 / 联盟链接",
      "src.ref.p": "hlens 目前没有运行任何交易所返佣或联盟链接。如果未来有变化，此类链接会明确标注“返佣链接”，且只会出现在本页——绝不会出现在看板、大户透视或社群频道中。",
      "src.repo.h": "代码仓库",
      "src.repo.p": "完整接口列表、限速预算与抓取代码：",
    },
  };

  let lang = "en";
  try {
    lang = (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
    const saved = localStorage.getItem("hlens.lang");
    if (saved) lang = saved;
  } catch (e) { /* ignore */ }

  const t = (k, vars) => {
    let s = (I18N[lang] && I18N[lang][k]) || I18N.en[k] || k;
    if (vars) for (const v in vars) s = s.replace(new RegExp("\\{" + v + "\\}", "g"), vars[v]);
    return s;
  };

  function applyI18n() {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.getAttribute("data-i18n")); });
    const langBtn = $("#lang");
    if (langBtn) langBtn.textContent = lang === "zh" ? "EN" : "中文";
    const refresh = $("#foot-refresh");
    if (refresh) refresh.textContent = t("foot.refresh", { n: CFG.refreshMinutes || 30 });
  }

  const langBtn = $("#lang");
  if (langBtn) {
    langBtn.addEventListener("click", () => {
      lang = lang === "zh" ? "en" : "zh";
      try { localStorage.setItem("hlens.lang", lang); } catch (e) { /* ignore */ }
      applyI18n();
    });
  }

  function wireLinks() {
    document.querySelectorAll('[data-href="github"]').forEach((a) => { a.href = CFG.github || "#"; });
    document.querySelectorAll('[data-href="github-sources-doc"]').forEach((a) => {
      a.href = (CFG.github || "#") + "/blob/main/docs/06-DATA-SOURCES.md";
    });
    document.querySelectorAll('[data-email]').forEach((a) => {
      const email = CFG.supportEmail || "support@example.com";
      a.href = "mailto:" + email;
      a.textContent = email;
    });
  }

  applyI18n();
  wireLinks();
})();
