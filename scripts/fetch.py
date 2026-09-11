#!/usr/bin/env python3
"""
hlens-CryptoPlus data fetcher.

Pulls public, key-free data from Binance / Bybit / OKX / Gate / Bitget / Hyperliquid and
writes data/latest.json (full snapshot) + data/history.json (compact trend).

Stdlib only. Designed to run inside GitHub Actions every 30 minutes.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "BNB", "SUI"]

# www.binance.com mirrors the futures API and is reachable from US IPs (GitHub runners),
# where fapi.binance.com answers 451. Try the mirror first, then the canonical host.
BINANCE_BASES = ["https://www.binance.com", "https://fapi.binance.com"]

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

HISTORY_MAX = 336  # 7 days at 30-minute cadence
WHALE_MIN_ACCOUNT = 300_000  # USD, live account value floor
WHALE_MIN_POS = 100_000  # USD, ignore dust positions
HL_SLEEP = 0.12  # seconds between clearinghouseState calls

UA = "hlens-CryptoPlus/1.0 (+https://github.com/raphael2025/hlens-CryptoPlus)"


# ---------------------------------------------------------------- helpers
def http(url: str, body: dict | None = None, timeout: int = 20, retries: int = 3):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(0.8 * (i + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def now_ms() -> int:
    return int(time.time() * 1000)


def log(*a):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- exchanges
def binance(coin: str) -> dict | None:
    s = f"{coin}USDT"

    def bget(path):
        last = None
        for base in BINANCE_BASES:
            try:
                return http(base + path, retries=1)
            except RuntimeError as e:
                last = e
        raise RuntimeError(str(last))

    try:
        px = bget(f"/fapi/v1/premiumIndex?symbol={s}")
        oi = bget(f"/fapi/v1/openInterest?symbol={s}")
        tk = bget(f"/fapi/v1/ticker/24hr?symbol={s}")
        gl = bget(f"/futures/data/globalLongShortAccountRatio?symbol={s}&period=1h&limit=1")
        tp = bget(f"/futures/data/topLongShortPositionRatio?symbol={s}&period=1h&limit=1")
        tk_ls = bget(f"/futures/data/takerlongshortRatio?symbol={s}&period=1h&limit=1")
    except RuntimeError as e:
        log("binance", coin, e)
        return None
    mark = f(px.get("markPrice"))
    oi_qty = f(oi.get("openInterest"), 0.0)
    buy = f(tk_ls[0].get("buyVol"), 0.0) if tk_ls else 0.0
    sell = f(tk_ls[0].get("sellVol"), 0.0) if tk_ls else 0.0
    return {
        "exchange": "binance",
        "price": mark,
        "funding": f(px.get("lastFundingRate")),
        "funding_interval_h": 8,
        "next_funding_ms": px.get("nextFundingTime"),
        "oi_usd": oi_qty * mark if mark else None,
        "vol24h_usd": f(tk.get("quoteVolume")),
        "chg24h_pct": f(tk.get("priceChangePercent")),
        "retail_long_share": f(gl[0].get("longAccount")) if gl else None,
        "top_trader_long_share": f(tp[0].get("longAccount")) if tp else None,
        "taker_buy_share": buy / (buy + sell) if (buy + sell) > 0 else None,
    }


def bybit(coin: str) -> dict | None:
    s = f"{coin}USDT"
    base = "https://api.bybit.com"
    try:
        tk = http(f"{base}/v5/market/tickers?category=linear&symbol={s}")
        ar = http(f"{base}/v5/market/account-ratio?category=linear&symbol={s}&period=1h&limit=1")
    except RuntimeError as e:
        log("bybit", coin, e)
        return None
    lst = (tk.get("result") or {}).get("list") or []
    if not lst:
        return None
    t = lst[0]
    ratio = ((ar.get("result") or {}).get("list") or [{}])[0]
    return {
        "exchange": "bybit",
        "price": f(t.get("markPrice")),
        "funding": f(t.get("fundingRate")),
        "funding_interval_h": int(f(t.get("fundingIntervalHour"), 8) or 8),
        "next_funding_ms": int(f(t.get("nextFundingTime"), 0) or 0),
        "oi_usd": f(t.get("openInterestValue")),
        "vol24h_usd": f(t.get("turnover24h")),
        "chg24h_pct": (f(t.get("price24hPcnt"), 0.0) or 0.0) * 100,
        "retail_long_share": f(ratio.get("buyRatio")),
        "top_trader_long_share": None,
        "taker_buy_share": None,
    }


def okx(coin: str) -> dict | None:
    inst = f"{coin}-USDT-SWAP"
    base = "https://www.okx.com/api/v5"
    try:
        fr = http(f"{base}/public/funding-rate?instId={inst}")
        oi = http(f"{base}/public/open-interest?instType=SWAP&instId={inst}")
        tk = http(f"{base}/market/ticker?instId={inst}")
        ls = http(f"{base}/rubik/stat/contracts/long-short-account-ratio?ccy={coin}&period=1H")
        tv = http(f"{base}/rubik/stat/taker-volume?ccy={coin}&instType=CONTRACTS&period=1H")
    except RuntimeError as e:
        log("okx", coin, e)
        return None
    frd = (fr.get("data") or [{}])[0]
    oid = (oi.get("data") or [{}])[0]
    tkd = (tk.get("data") or [{}])[0]
    if not tkd:
        return None
    last = f(tkd.get("last"))
    open24 = f(tkd.get("open24h"))
    ft, pft = f(frd.get("fundingTime")), f(frd.get("prevFundingTime"))
    interval_h = int(round((ft - pft) / 3_600_000)) if ft and pft else 8
    ls_ratio = f((ls.get("data") or [[None, None]])[0][1])
    tvd = (tv.get("data") or [[None, None, None]])[0]
    sell, buy = f(tvd[1], 0.0), f(tvd[2], 0.0)
    return {
        "exchange": "okx",
        "price": last,
        "funding": f(frd.get("fundingRate")),
        "funding_interval_h": interval_h or 8,
        "next_funding_ms": int(ft) if ft else None,
        "oi_usd": f(oid.get("oiUsd")),
        "vol24h_usd": (f(tkd.get("volCcy24h"), 0.0) or 0.0) * (last or 0.0),
        "chg24h_pct": ((last / open24) - 1) * 100 if last and open24 else None,
        "retail_long_share": ls_ratio / (1 + ls_ratio) if ls_ratio else None,
        "top_trader_long_share": None,
        "taker_buy_share": buy / (buy + sell) if (buy + sell) > 0 else None,
    }


def gate(coin: str) -> dict | None:
    c = f"{coin}_USDT"
    base = "https://api.gateio.ws/api/v4/futures/usdt"
    try:
        st = http(f"{base}/contract_stats?contract={c}&interval=1h&limit=1")
        tk = http(f"{base}/tickers?contract={c}")
        ct = http(f"{base}/contracts/{c}")
    except RuntimeError as e:
        log("gate", coin, e)
        return None
    if not st or not tk:
        return None
    st, tk = st[-1], tk[0]
    lsr_a = f(st.get("lsr_account"))
    top = f(st.get("top_lsr_size"))
    lt, stk = f(st.get("long_taker_size"), 0.0), f(st.get("short_taker_size"), 0.0)
    interval_h = int((f(ct.get("funding_interval"), 28800) or 28800) / 3600)
    return {
        "exchange": "gate",
        "price": f(tk.get("mark_price")),
        "funding": f(tk.get("funding_rate")),
        "funding_interval_h": interval_h or 8,
        "next_funding_ms": int(f(ct.get("funding_next_apply"), 0) or 0) * 1000 or None,
        "oi_usd": f(st.get("open_interest_usd")),
        "vol24h_usd": f(tk.get("volume_24h_quote")),
        "chg24h_pct": f(tk.get("change_percentage")),
        "retail_long_share": lsr_a / (1 + lsr_a) if lsr_a else None,
        "top_trader_long_share": top / (1 + top) if top else None,
        "taker_buy_share": lt / (lt + stk) if (lt + stk) > 0 else None,
        "liq_long_usd_1h": f(st.get("long_liq_usd"), 0.0),
        "liq_short_usd_1h": f(st.get("short_liq_usd"), 0.0),
    }


def bitget(coin: str) -> dict | None:
    s = f"{coin}USDT"
    base = "https://api.bitget.com/api/v2/mix/market"
    try:
        tk = http(f"{base}/ticker?symbol={s}&productType=USDT-FUTURES")
    except RuntimeError as e:
        log("bitget", coin, e)
        return None
    ar, tv = {}, {}
    try:
        ar = http(f"{base}/account-long-short?symbol={s}&period=1h", retries=1)
    except RuntimeError:
        pass  # not every symbol has ratio stats
    try:
        tv = http(f"{base}/taker-buy-sell?symbol={s}&period=1h", retries=1)
    except RuntimeError:
        pass
    d = (tk.get("data") or [{}])[0]
    if not d:
        return None
    mark = f(d.get("markPrice"))
    a = (ar.get("data") or [{}])[-1]
    t = (tv.get("data") or [{}])[-1]
    buy, sell = f(t.get("buyVolume"), 0.0), f(t.get("sellVolume"), 0.0)
    return {
        "exchange": "bitget",
        "price": mark,
        "funding": f(d.get("fundingRate")),
        "funding_interval_h": 8,
        "next_funding_ms": None,
        "oi_usd": (f(d.get("holdingAmount"), 0.0) or 0.0) * (mark or 0.0),
        "vol24h_usd": f(d.get("usdtVolume")),
        "chg24h_pct": (f(d.get("change24h"), 0.0) or 0.0) * 100,
        "retail_long_share": f(a.get("longAccountRatio")),
        "top_trader_long_share": None,
        "taker_buy_share": buy / (buy + sell) if (buy + sell) > 0 else None,
    }


def hyperliquid_ctx() -> dict[str, dict]:
    meta, ctxs = http(HL_INFO, {"type": "metaAndAssetCtxs"})
    out = {}
    for u, c in zip(meta["universe"], ctxs):
        mark = f(c.get("markPx"))
        prev = f(c.get("prevDayPx"))
        out[u["name"]] = {
            "exchange": "hyperliquid",
            "price": mark,
            "funding": f(c.get("funding")),  # hourly
            "funding_interval_h": 1,
            "next_funding_ms": None,
            "oi_usd": (f(c.get("openInterest"), 0.0) or 0.0) * (mark or 0.0),
            "vol24h_usd": f(c.get("dayNtlVlm")),
            "chg24h_pct": ((mark / prev) - 1) * 100 if mark and prev else None,
            "premium": f(c.get("premium")),
            "max_leverage": u.get("maxLeverage"),
            "retail_long_share": None,
            "top_trader_long_share": None,
            "taker_buy_share": None,
        }
    return out


# ---------------------------------------------------------------- whales
def fetch_whales() -> dict:
    log("leaderboard: downloading")
    lb = http(HL_LEADERBOARD, timeout=60)["leaderboardRows"]
    log(f"leaderboard: {len(lb)} rows")

    def perf(row, win, key):
        for w, p in row.get("windowPerformances", []):
            if w == win:
                return f(p.get(key), 0.0)
        return 0.0

    by_value = sorted(lb, key=lambda r: f(r.get("accountValue"), 0.0), reverse=True)[:90]
    by_month_pnl = sorted(lb, key=lambda r: perf(r, "month", "pnl"), reverse=True)[:40]
    by_week_pnl = sorted(lb, key=lambda r: perf(r, "week", "pnl"), reverse=True)[:30]
    big = [r for r in lb if f(r.get("accountValue"), 0.0) >= 1_000_000]
    by_month_roi = sorted(big, key=lambda r: perf(r, "month", "roi"), reverse=True)[:25]

    cand: dict[str, dict] = {}
    for r in by_value + by_month_pnl + by_week_pnl + by_month_roi:
        cand[r["ethAddress"].lower()] = r
    log(f"whales: querying {len(cand)} candidate wallets")

    def state_of(addr):
        try:
            st = http(HL_INFO, {"type": "clearinghouseState", "user": addr}, timeout=20, retries=2)
        except RuntimeError as e:
            log("clearinghouseState", addr, e)
            return None
        time.sleep(HL_SLEEP)
        return st

    addrs = list(cand.keys())
    with ThreadPoolExecutor(max_workers=4) as pool:
        states = dict(zip(addrs, pool.map(state_of, addrs)))

    whales = []
    for addr, row in cand.items():
        st = states.get(addr)
        if st is None:
            continue
        acct = f((st.get("marginSummary") or {}).get("accountValue"), 0.0)
        positions = []
        for ap in st.get("assetPositions", []):
            p = ap.get("position") or {}
            szi = f(p.get("szi"), 0.0)
            notional = abs(f(p.get("positionValue"), 0.0))
            if szi == 0 or notional < WHALE_MIN_POS:
                continue
            lev = p.get("leverage") or {}
            positions.append({
                "coin": p.get("coin"),
                "side": "long" if szi > 0 else "short",
                "size": abs(szi),
                "notional": notional,
                "entry": f(p.get("entryPx")),
                "liq": f(p.get("liquidationPx")),
                "lev": lev.get("value"),
                "lev_type": lev.get("type"),
                "upnl": f(p.get("unrealizedPnl"), 0.0),
                "roe": f(p.get("returnOnEquity"), 0.0),
            })
        if acct < WHALE_MIN_ACCOUNT and not positions:
            continue
        positions.sort(key=lambda x: x["notional"], reverse=True)
        whales.append({
            "address": addr,
            "name": row.get("displayName") or None,
            "account_value": acct,
            "lb_account_value": f(row.get("accountValue"), 0.0),
            "pnl_day": perf(row, "day", "pnl"),
            "pnl_week": perf(row, "week", "pnl"),
            "pnl_month": perf(row, "month", "pnl"),
            "roi_month": perf(row, "month", "roi"),
            "pnl_all": perf(row, "allTime", "pnl"),
            "n_pos": len(positions),
            "long_notional": sum(p["notional"] for p in positions if p["side"] == "long"),
            "short_notional": sum(p["notional"] for p in positions if p["side"] == "short"),
            "positions": positions,
        })
    whales.sort(key=lambda w: w["account_value"], reverse=True)
    log(f"whales: kept {len(whales)} wallets with live data")

    # per-coin aggregation
    per_coin: dict[str, dict] = {}
    for w in whales:
        for p in w["positions"]:
            c = per_coin.setdefault(p["coin"], {"coin": p["coin"], "long_usd": 0.0, "short_usd": 0.0,
                                                 "n_long": 0, "n_short": 0, "long_upnl": 0.0, "short_upnl": 0.0})
            if p["side"] == "long":
                c["long_usd"] += p["notional"]; c["n_long"] += 1; c["long_upnl"] += p["upnl"]
            else:
                c["short_usd"] += p["notional"]; c["n_short"] += 1; c["short_upnl"] += p["upnl"]
    for c in per_coin.values():
        tot = c["long_usd"] + c["short_usd"]
        c["total_usd"] = tot
        c["long_share"] = c["long_usd"] / tot if tot else None
    coins_sorted = sorted(per_coin.values(), key=lambda c: c["total_usd"], reverse=True)

    # biggest single positions across all whales
    top_positions = []
    for w in whales:
        for p in w["positions"]:
            top_positions.append({**p, "address": w["address"], "name": w["name"], "account_value": w["account_value"]})
    top_positions.sort(key=lambda p: p["notional"], reverse=True)

    return {
        "count": len(whales),
        "candidates": len(cand),
        "wallets": whales[:60],
        "by_coin": coins_sorted[:40],
        "top_positions": top_positions[:50],
    }


# ---------------------------------------------------------------- composite
def norm8h(ex: dict) -> float | None:
    fr, ih = ex.get("funding"), ex.get("funding_interval_h") or 8
    return fr * (8 / ih) if fr is not None else None


def build_coin(coin: str, exs: list[dict], whale_by_coin: dict[str, dict]) -> dict:
    exs = [e for e in exs if e]
    oi_total = sum((e.get("oi_usd") or 0.0) for e in exs)
    # OI-weighted 8h funding
    num = den = 0.0
    for e in exs:
        r = norm8h(e)
        if r is None:
            continue
        w = e.get("oi_usd") or 1.0
        num += r * w; den += w
    funding_8h = num / den if den else None

    def avg(key):
        vals = [e[key] for e in exs if e.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    retail = avg("retail_long_share")
    top = avg("top_trader_long_share")
    taker = avg("taker_buy_share")
    wc = whale_by_coin.get(coin) or {}
    whale_share = wc.get("long_share")

    # crowding: -1 (crowded short) .. +1 (crowded long); transparent formula shown in UI
    parts = []
    if funding_8h is not None:
        parts.append(max(-1.0, min(1.0, funding_8h / 0.0005)))
    if retail is not None:
        parts.append(max(-1.0, min(1.0, (retail - 0.5) * 4)))
    if taker is not None:
        parts.append(max(-1.0, min(1.0, (taker - 0.5) * 6)))
    crowding = sum(parts) / len(parts) if parts else None

    prices = [e["price"] for e in exs if e.get("price")]
    return {
        "coin": coin,
        "price": sum(prices) / len(prices) if prices else None,
        "chg24h_pct": avg("chg24h_pct"),
        "funding_8h": funding_8h,
        "funding_annualized_pct": funding_8h * 3 * 365 * 100 if funding_8h is not None else None,
        "oi_total_usd": oi_total,
        "vol24h_total_usd": sum((e.get("vol24h_usd") or 0.0) for e in exs),
        "retail_long_share": retail,
        "top_trader_long_share": top,
        "taker_buy_share": taker,
        "whale_long_share": whale_share,
        "whale_long_usd": wc.get("long_usd"),
        "whale_short_usd": wc.get("short_usd"),
        "whale_n": (wc.get("n_long") or 0) + (wc.get("n_short") or 0),
        "crowding": crowding,
        "exchanges": [{**e, "funding_8h": norm8h(e)} for e in exs],
    }


# ---------------------------------------------------------------- macro
def fetch_macro() -> dict:
    """Key-free macro context. Currently: alternative.me Fear & Greed (daily)."""
    out = {}
    try:
        r = http("https://api.alternative.me/fng/?limit=14&format=json")
        rows = r.get("data") or []
        if rows:
            out["fng"] = {
                "value": int(f(rows[0].get("value"), 0)),
                "label": rows[0].get("value_classification"),
                "ts": int(f(rows[0].get("timestamp"), 0)) * 1000,
                "history": [int(f(x.get("value"), 0)) for x in reversed(rows)],
            }
    except Exception as e:
        log("fng failed", e)
    return out


# ---------------------------------------------------------------- main
def main():
    t0 = time.time()
    status = {}

    log("hyperliquid: metaAndAssetCtxs")
    try:
        hl = hyperliquid_ctx(); status["hyperliquid"] = "ok"
    except Exception as e:
        log("hyperliquid ctx failed", e); hl = {}; status["hyperliquid"] = f"error: {e}"

    log("cex: fetching", COINS)
    with ThreadPoolExecutor(max_workers=6) as pool:
        bn = list(pool.map(binance, COINS))
        bb = list(pool.map(bybit, COINS))
        ok = list(pool.map(okx, COINS))
        gt = list(pool.map(gate, COINS))
        bg = list(pool.map(bitget, COINS))
    status["binance"] = "ok" if any(bn) else "error"
    status["bybit"] = "ok" if any(bb) else "error (geo-blocked from US runners)"
    status["okx"] = "ok" if any(ok) else "error"
    status["gate"] = "ok" if any(gt) else "error"
    status["bitget"] = "ok" if any(bg) else "error"

    macro = fetch_macro(); status["fear_greed"] = "ok" if macro.get("fng") else "error"

    try:
        whales = fetch_whales(); status["hl_leaderboard"] = "ok"
    except Exception as e:
        log("whales failed", e)
        whales = {"count": 0, "candidates": 0, "wallets": [], "by_coin": [], "top_positions": []}
        status["hl_leaderboard"] = f"error: {e}"
    whale_by_coin = {c["coin"]: c for c in whales["by_coin"]}

    coins = [build_coin(c, [bn[i], bb[i], ok[i], gt[i], bg[i], hl.get(c)], whale_by_coin) for i, c in enumerate(COINS)]

    ts = now_ms()
    latest = {
        "schema": 1,
        "generated_at": ts,
        "generated_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - t0, 1),
        "sources": status,
        "coins": coins,
        "macro": macro,
        "whales": whales,
    }
    (DATA / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, separators=(",", ":")))

    # compact history for sparklines
    hist_path = DATA / "history.json"
    history = []
    if hist_path.exists():
        try:
            history = json.loads(hist_path.read_text())
        except json.JSONDecodeError:
            history = []
    history.append({
        "t": ts,
        "c": {c["coin"]: [c["price"], c["funding_8h"], c["oi_total_usd"], c["retail_long_share"],
                          c["whale_long_share"], c["crowding"]] for c in coins},
    })
    history = history[-HISTORY_MAX:]
    hist_path.write_text(json.dumps(history, separators=(",", ":")))
    log(f"done in {latest['elapsed_s']}s; whales={whales['count']}; sources={status}")


if __name__ == "__main__":
    main()
