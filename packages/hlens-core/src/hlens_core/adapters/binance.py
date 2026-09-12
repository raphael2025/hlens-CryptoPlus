"""Binance USDⓈ-M futures -- the reference adapter.

Copy this file to add a venue. What makes it the reference:

* every REST call declares its endpoint and weight so the shared
  :class:`~hlens_core.ratelimit.Budget` can account for it;
* one request feeds every field it can (``premiumIndex`` produces both marks and
  funding) rather than one request per output type;
* raw venue numbers are parsed as ``Decimal`` straight from the JSON strings;
* what the venue does not say stays ``None`` -- the 8 h funding interval is read from
  ``fundingInfo``, never assumed;
* the liquidation stream is marked ``throttled_source`` because Binance pushes at most
  one forced order per symbol per second, so the rows are a lower bound.

Endpoints and weights: docs/06-DATA-SOURCES.md §3.1.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from ..contracts import (
    Candle,
    Completeness,
    FundingRate,
    Instrument,
    InstrumentStatus,
    Liquidation,
    LongShortRatio,
    LSKind,
    MarkPrice,
    OpenInterest,
    Quality,
    Semantic,
    Side,
    Ticker24h,
    Transport,
    now_ms,
)
from ..ratelimit import Budget
from ..ratelimit import venue as venue_spec
from .base import (
    AdapterHealth,
    Capability,
    CapabilityInfo,
    CapabilitySet,
    Mode,
    SymbolMapper,
    VenueHttp,
)
from .errors import AdapterError, InvalidSymbol
from .symbols import split_multiplier

VENUE = "binance"

EP_PING = "/fapi/v1/ping"
EP_TIME = "/fapi/v1/time"
EP_EXCHANGE_INFO = "/fapi/v1/exchangeInfo"
EP_FUNDING_INFO = "/fapi/v1/fundingInfo"
EP_PREMIUM_INDEX = "/fapi/v1/premiumIndex"
EP_OPEN_INTEREST = "/fapi/v1/openInterest"
EP_TICKER_24H = "/fapi/v1/ticker/24hr"
EP_KLINES = "/fapi/v1/klines"

#: /futures/data/* endpoints are weight 0 but still count against the IP request rate,
#: so the budget charges them a floor of 1.
LS_ENDPOINTS: dict[LSKind, tuple[str, str]] = {
    LSKind.account: ("/futures/data/globalLongShortAccountRatio", "longAccount"),
    LSKind.top_account: ("/futures/data/topLongShortAccountRatio", "longAccount"),
    LSKind.top_position: ("/futures/data/topLongShortPositionRatio", "longAccount"),
    LSKind.taker: ("/futures/data/takerlongshortRatio", ""),
}

#: Binance bills klines by the number of bars asked for.
KLINE_WEIGHT_TIERS: tuple[tuple[int, int], ...] = ((100, 1), (500, 2), (1000, 5))
KLINE_WEIGHT_MAX = 10

STATUS_MAP = {
    "TRADING": InstrumentStatus.trading,
    "PENDING_TRADING": InstrumentStatus.pending,
    "SETTLING": InstrumentStatus.halted,
    "PRE_SETTLE": InstrumentStatus.halted,
    "DELIVERING": InstrumentStatus.halted,
    "DELIVERED": InstrumentStatus.delisted,
    "CLOSE": InstrumentStatus.delisted,
    "BREAK": InstrumentStatus.halted,
}

STREAM_MARK_PRICE = "!markPrice@arr@1s"
STREAM_FORCE_ORDER = "!forceOrder@arr"
#: Binance force-closes a WS connection at 24 h; rotate before it does, with jitter so a
#: fleet of collectors does not reconnect in lockstep.
WS_ROTATE_AFTER_S = 23 * 3600


def dec(v: Any) -> Decimal | None:
    """Parse a venue number. Strings go to ``Decimal`` untouched; junk becomes ``None``."""
    if v is None or v == "":
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def kline_weight(limit: int) -> int:
    for bound, w in KLINE_WEIGHT_TIERS:
        if limit < bound or (bound == 1000 and limit <= bound):
            return w
    return KLINE_WEIGHT_MAX


class BinanceFutures:
    """USDⓈ-M perpetuals. Public market data only -- no key, no signed endpoints."""

    venue = VENUE
    product = "usdt_perpetual"

    def __init__(
        self,
        *,
        egress_ip: str = "unknown",
        budget: Budget | None = None,
        http: VenueHttp | None = None,
        bases: list[str] | None = None,
        ws_base: str | None = None,
        client: Any = None,
        quote_assets: Sequence[str] = ("USDT", "USDC"),
        contract_types: Sequence[str] = ("PERPETUAL",),
    ) -> None:
        spec = venue_spec(VENUE)
        self.spec = spec
        self.budget = budget or Budget.for_venue(egress_ip, VENUE)
        self.http = http or VenueHttp(
            VENUE,
            bases or spec.rest_bases,
            budget=self.budget,
            client=client,
            used_weight_header=spec.raw.get("used_weight_header"),
        )
        self.ws_base = ws_base or spec.raw.get("ws_base", "wss://fstream.binance.com")
        self.quote_assets = tuple(quote_assets)
        self.contract_types = tuple(contract_types)
        self.symbols = SymbolMapper(VENUE)
        self._funding_intervals: dict[str, Decimal] = {}
        self._instruments: dict[str, Instrument] = {}

    async def aclose(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------ caps

    def capabilities(self) -> CapabilitySet:
        full = Completeness.full
        return CapabilitySet(
            venue=VENUE,
            product=self.product,
            capabilities={
                Capability.discover_instruments: CapabilityInfo(
                    supported=True, mode=Mode.rest, completeness=full
                ),
                Capability.batch_ticker: CapabilityInfo(
                    supported=True, mode=Mode.ws_and_rest, completeness=full
                ),
                Capability.mark_price: CapabilityInfo(
                    supported=True, mode=Mode.ws_and_rest, completeness=full
                ),
                Capability.funding_current: CapabilityInfo(
                    supported=True, mode=Mode.ws_and_rest, completeness=full
                ),
                Capability.funding_history: CapabilityInfo(
                    supported=False,
                    mode=Mode.rest,
                    note="/fapi/v1/fundingRate exists on its own 500/5min bucket; "
                    "bulk history comes from data.binance.vision instead (06 §3.1)",
                ),
                Capability.open_interest: CapabilityInfo(
                    supported=True, mode=Mode.rest, completeness=full, per_symbol=True,
                    note="no all-market form: 1 weight per symbol per poll",
                ),
                Capability.open_interest_history: CapabilityInfo(
                    supported=False, mode=Mode.rest, completeness=Completeness.unknown,
                    note="openInterestHist keeps only 30 days",
                ),
                Capability.long_short_ratio: CapabilityInfo(
                    supported=True, mode=Mode.rest, completeness=full, per_symbol=True,
                    note="4 kinds, 30-day history, weight 0 but IP-rate limited",
                ),
                Capability.taker_flow: CapabilityInfo(
                    supported=True, mode=Mode.rest, completeness=full, per_symbol=True
                ),
                Capability.klines: CapabilityInfo(
                    supported=True, mode=Mode.rest, completeness=full, per_symbol=True
                ),
                Capability.liquidation_stream: CapabilityInfo(
                    supported=True,
                    mode=Mode.ws,
                    completeness=Completeness.lower_bound,
                    note="!forceOrder@arr pushes at most one order per symbol per second",
                ),
                Capability.liquidation_history: CapabilityInfo(supported=False),
                Capability.trade_stream: CapabilityInfo(supported=True, mode=Mode.ws),
                Capability.wallet_fills: CapabilityInfo(
                    supported=False, note="requires a signed endpoint; out of scope"
                ),
            },
        )

    # ----------------------------------------------------------- instruments

    async def discover_instruments(self) -> list[Instrument]:
        """``exchangeInfo`` (w 1) + ``fundingInfo`` (separate 500/5min bucket).

        ``fundingInfo`` lists only the symbols whose settlement period is *not* the
        8 h default, so the absence of a symbol there is information, not a gap.
        """
        info = await self.http.get(
            EP_EXCHANGE_INFO, weight=1, capability=Capability.discover_instruments.value
        )
        intervals = await self.fetch_funding_intervals()
        ts = int(info.get("serverTime") or now_ms())
        out: list[Instrument] = []
        for s in info.get("symbols", []):
            if s.get("contractType") not in self.contract_types:
                continue
            if s.get("quoteAsset") not in self.quote_assets:
                continue
            venue_symbol = s["symbol"]
            base_raw = s.get("baseAsset") or venue_symbol
            base, mult = split_multiplier(base_raw)
            filters = {f["filterType"]: f for f in s.get("filters", [])}
            canonical = base.upper()
            self.symbols.register(venue_symbol, canonical)
            out.append(
                Instrument(
                    venue=VENUE,
                    symbol=canonical,
                    venue_symbol=venue_symbol,
                    ts=ts,
                    source=EP_EXCHANGE_INFO,
                    base=base_raw,
                    quote=s.get("quoteAsset", ""),
                    mult=mult,
                    tick=dec(filters.get("PRICE_FILTER", {}).get("tickSize")),
                    qty_step=dec(filters.get("LOT_SIZE", {}).get("stepSize")),
                    is_inverse=False,
                    funding_interval_h=intervals.get(venue_symbol, Decimal(8)),
                    listed_ts=s.get("onboardDate") or None,
                    status=STATUS_MAP.get(s.get("status", ""), InstrumentStatus.unknown),
                )
            )
        self._instruments = {i.venue_symbol: i for i in out}
        return out

    async def fetch_funding_intervals(self) -> dict[str, Decimal]:
        """Settlement period per symbol. Only non-8 h symbols are listed by the venue."""
        rows = await self.http.get(
            EP_FUNDING_INFO, weight=1, capability=Capability.discover_instruments.value
        )
        out: dict[str, Decimal] = {}
        for r in rows or []:
            h = dec(r.get("fundingIntervalHours"))
            if h and h > 0:
                out[r["symbol"]] = h
        self._funding_intervals = out
        return out

    # ------------------------------------------------------- premium / marks

    async def fetch_premium_index(
        self, symbols: Sequence[str] | None = None
    ) -> tuple[list[MarkPrice], list[FundingRate]]:
        """One ``premiumIndex`` call -> marks *and* funding.

        Weight 10 for the whole market, 1 for a single symbol -- so asking for two or
        more symbols individually is never worth it; pull the market.
        """
        single = symbols[0] if symbols and len(symbols) == 1 else None
        params = {"symbol": self._venue_symbol(single)} if single else None
        rows = await self.http.get(
            EP_PREMIUM_INDEX,
            params=params,
            weight=1 if single else 10,
            capability=Capability.mark_price.value,
        )
        if isinstance(rows, dict):
            rows = [rows]
        wanted = {self._venue_symbol(s) for s in symbols} if symbols else None
        ingest = now_ms()
        marks: list[MarkPrice] = []
        fundings: list[FundingRate] = []
        for r in rows or []:
            vs = r["symbol"]
            if wanted is not None and vs not in wanted:
                continue
            sym = self.symbols.canonical(vs)
            ts = int(r.get("time") or ingest)
            mark = dec(r.get("markPrice"))
            if mark is not None and mark > 0:
                marks.append(
                    MarkPrice(
                        venue=VENUE, symbol=sym, ts=ts, ingest_ts=ingest,
                        source=EP_PREMIUM_INDEX, mark=mark, index=dec(r.get("indexPrice")),
                    )
                )
            rate = dec(r.get("lastFundingRate"))
            if rate is not None:
                interval = self._funding_intervals.get(vs, Decimal(8))
                nxt = r.get("nextFundingTime") or None
                fundings.append(
                    FundingRate(
                        venue=VENUE, symbol=sym, ts=ts, ingest_ts=ingest,
                        source=EP_PREMIUM_INDEX, rate=rate, interval_h=interval,
                        next_ts=int(nxt) if nxt else None,
                    )
                )
        return marks, fundings

    async def fetch_mark_prices(self, symbols: Sequence[str] | None = None) -> list[MarkPrice]:
        return (await self.fetch_premium_index(symbols))[0]

    async def fetch_funding_rates(self, symbols: Sequence[str] | None = None) -> list[FundingRate]:
        """Current funding, normalized to 8 h using the real interval from ``fundingInfo``.

        Call :meth:`fetch_funding_intervals` (or :meth:`discover_instruments`) at least
        once an hour first; without it every symbol is assumed to settle 8-hourly, which
        is wrong for the short-interval listings.
        """
        return (await self.fetch_premium_index(symbols))[1]

    # -------------------------------------------------------- open interest

    async def fetch_open_interest(
        self, symbols: Sequence[str], marks: dict[str, Decimal] | None = None
    ) -> list[OpenInterest]:
        """Per-symbol only (weight 1 each) -- Binance has no all-market OI endpoint.

        ``marks`` turns the base-unit figure into USD; the result is then
        ``semantic=derived``, never ``exchange_reported``.
        """
        async def one(sym: str) -> OpenInterest | None:
            vs = self._venue_symbol(sym)
            r = await self.http.get(
                EP_OPEN_INTEREST,
                params={"symbol": vs},
                weight=1,
                capability=Capability.open_interest.value,
            )
            oi = dec(r.get("openInterest"))
            if oi is None:
                return None
            instrument = self._instruments.get(vs)
            mult = instrument.mult if instrument else Decimal(1)
            mark = (marks or {}).get(sym)
            # openInterest is quoted in venue contract units; 1000PEPE contracts are
            # 1000 base units each, so convert before anything multiplies by a price.
            oi_base = oi * mult
            return OpenInterest(
                venue=VENUE, symbol=sym, ts=int(r.get("time") or now_ms()),
                source=EP_OPEN_INTEREST, oi_base=oi_base,
                oi_usd=(oi_base * mark) if mark else None,
                semantic=Semantic.derived if mark else Semantic.exchange_reported,
            )

        results = await asyncio.gather(*(one(s) for s in symbols), return_exceptions=True)
        out: list[OpenInterest] = []
        for r in results:
            if isinstance(r, BaseException):
                if isinstance(r, AdapterError):
                    continue
                raise r
            if r is not None:
                out.append(r)
        return out

    # ------------------------------------------------------- long/short ratio

    async def fetch_long_short_ratios(
        self,
        symbol: str,
        kinds: Sequence[LSKind] | None = None,
        *,
        period: str = "5m",
        limit: int = 1,
    ) -> list[LongShortRatio]:
        """All four ratio flavours. Weight 0, but each call still burns IP request rate.

        History is 30 days only; anything older comes from ``data.binance.vision``.
        """
        kinds = tuple(kinds or LS_ENDPOINTS)
        vs = self._venue_symbol(symbol)
        out: list[LongShortRatio] = []
        for kind in kinds:
            path, field = LS_ENDPOINTS[kind]
            rows = await self.http.get(
                path,
                params={"symbol": vs, "period": period, "limit": limit},
                weight=0,
                capability=Capability.long_short_ratio.value,
            )
            for r in rows or []:
                share = (
                    self._taker_share(r) if kind is LSKind.taker else dec(r.get(field))
                )
                if share is None or not (0 <= share <= 1):
                    continue
                out.append(
                    LongShortRatio(
                        venue=VENUE, symbol=symbol, ts=int(r["timestamp"]),
                        source=path, kind=kind, long_share=share, period=period,
                        semantic=(
                            Semantic.derived if kind is LSKind.taker
                            else Semantic.exchange_reported
                        ),
                    )
                )
        return out

    @staticmethod
    def _taker_share(row: dict[str, Any]) -> Decimal | None:
        buy, sell = dec(row.get("buyVol")), dec(row.get("sellVol"))
        if buy is None or sell is None or (buy + sell) <= 0:
            return None
        return buy / (buy + sell)

    # ------------------------------------------------------------- klines

    async def fetch_klines(
        self,
        symbol: str,
        tf: str = "1m",
        *,
        limit: int = 500,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[Candle]:
        """OHLCV with taker buy volume. Weight is tiered by ``limit`` (1/2/5/10)."""
        params: dict[str, Any] = {
            "symbol": self._venue_symbol(symbol),
            "interval": tf,
            "limit": limit,
        }
        if start_ts is not None:
            params["startTime"] = start_ts
        if end_ts is not None:
            params["endTime"] = end_ts
        rows = await self.http.get(
            EP_KLINES, params=params, weight=kline_weight(limit),
            capability=Capability.klines.value,
        )
        ingest = now_ms()
        out: list[Candle] = []
        for r in rows or []:
            close_ts = int(r[6])
            o, h, low, c, v = (dec(r[i]) for i in (1, 2, 3, 4, 5))
            if o is None or h is None or low is None or c is None or v is None:
                # A malformed bar is dropped, not coerced to zero, and it must not take
                # the rest of the page with it.
                continue
            out.append(
                Candle(
                    venue=VENUE, symbol=symbol, ts=int(r[0]), ingest_ts=ingest,
                    source=EP_KLINES, tf=tf, close_ts=close_ts,
                    o=o, h=h, l=low, c=c, v=v,
                    quote_v=dec(r[7]), trades=int(r[8]), taker_buy_v=dec(r[9]),
                    closed=close_ts < ingest,
                    quality=Quality.fresh if close_ts < ingest else Quality.partial,
                )
            )
        return out

    # ------------------------------------------------------------- ticker

    async def fetch_ticker_24h(self, symbols: Sequence[str] | None = None) -> list[Ticker24h]:
        """Weight 40 for the whole market, 1 for one symbol. Poll the market, not the list."""
        single = symbols[0] if symbols and len(symbols) == 1 else None
        params = {"symbol": self._venue_symbol(single)} if single else None
        rows = await self.http.get(
            EP_TICKER_24H, params=params, weight=1 if single else 40,
            capability=Capability.batch_ticker.value,
        )
        if isinstance(rows, dict):
            rows = [rows]
        wanted = {self._venue_symbol(s) for s in symbols} if symbols else None
        ingest = now_ms()
        out: list[Ticker24h] = []
        for r in rows or []:
            vs = r["symbol"]
            if wanted is not None and vs not in wanted:
                continue
            out.append(
                Ticker24h(
                    venue=VENUE, symbol=self.symbols.canonical(vs),
                    ts=int(r.get("closeTime") or ingest), ingest_ts=ingest,
                    source=EP_TICKER_24H, last=dec(r.get("lastPrice")),
                    chg24h_pct=dec(r.get("priceChangePercent")),
                    vol24h_usd=dec(r.get("quoteVolume")),
                    high24h=dec(r.get("highPrice")), low24h=dec(r.get("lowPrice")),
                )
            )
        return out

    # ------------------------------------------------------------ websocket

    def stream_mark_price(
        self, symbols: Sequence[str] | None = None
    ) -> AsyncIterator[MarkPrice]:
        """``!markPrice@arr@1s`` -- the whole market's marks and funding, once a second."""
        wanted = {self._venue_symbol(s) for s in symbols} if symbols else None
        return self._stream(STREAM_MARK_PRICE, lambda d: self._parse_mark_event(d, wanted))

    def stream_liquidations(
        self, symbols: Sequence[str] | None = None
    ) -> AsyncIterator[Liquidation]:
        """``!forceOrder@arr``. Throttled to one order per symbol per second: a LOWER BOUND."""
        wanted = {self._venue_symbol(s) for s in symbols} if symbols else None
        return self._stream(STREAM_FORCE_ORDER, lambda d: self._parse_force_order(d, wanted))

    async def _stream(self, stream: str, parse) -> AsyncIterator[Any]:
        """Combined-stream reader with reconnect, 24 h rotation and ping/pong.

        The server pings every 3 minutes and drops us if no pong comes back inside 10;
        ``websockets`` answers ping frames itself, and ``ping_interval`` adds our own
        keepalive so a half-open TCP connection surfaces as an error instead of silence.
        """
        import websockets

        url = f"{self.ws_base}/stream?streams={stream}"
        attempt = 0
        while True:
            deadline = asyncio.get_running_loop().time() + WS_ROTATE_AFTER_S * (
                0.9 + 0.1 * random.random()
            )
            try:
                async with websockets.connect(
                    url, ping_interval=20, ping_timeout=20, max_queue=4096
                ) as ws:
                    attempt = 0
                    while asyncio.get_running_loop().time() < deadline:
                        raw = await ws.recv()
                        msg = json.loads(raw)
                        data = msg.get("data", msg)
                        for item in parse(data):
                            yield item
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- any failure means "reconnect"
                attempt += 1
                await asyncio.sleep(min(60.0, 2**attempt) * (0.5 + random.random()))
                continue

    def _parse_mark_event(
        self, data: Any, wanted: set[str] | None
    ) -> list[MarkPrice]:
        rows = data if isinstance(data, list) else [data]
        ingest = now_ms()
        out: list[MarkPrice] = []
        for r in rows:
            if r.get("e") != "markPriceUpdate":
                continue
            vs = r.get("s")
            if wanted is not None and vs not in wanted:
                continue
            mark = dec(r.get("p"))
            if mark is None or mark <= 0:
                continue
            out.append(
                MarkPrice(
                    venue=VENUE, symbol=self.symbols.canonical(vs), ts=int(r["E"]),
                    ingest_ts=ingest, source=f"ws:{STREAM_MARK_PRICE}",
                    transport=Transport.ws, mark=mark, index=dec(r.get("i")),
                )
            )
        return out

    def mark_event_funding(self, data: Any) -> list[FundingRate]:
        """``!markPrice@arr@1s`` also carries funding (``r``) and next settlement (``T``).

        Kept separate from the mark stream so a collector can route the two record types
        to different tables without re-parsing.
        """
        rows = data if isinstance(data, list) else [data]
        ingest = now_ms()
        out: list[FundingRate] = []
        for r in rows:
            rate = dec(r.get("r"))
            if r.get("e") != "markPriceUpdate" or rate is None:
                continue
            vs = r["s"]
            out.append(
                FundingRate(
                    venue=VENUE, symbol=self.symbols.canonical(vs), ts=int(r["E"]),
                    ingest_ts=ingest, source=f"ws:{STREAM_MARK_PRICE}",
                    transport=Transport.ws, rate=rate,
                    interval_h=self._funding_intervals.get(vs, Decimal(8)),
                    next_ts=int(r["T"]) if r.get("T") else None,
                )
            )
        return out

    def _parse_force_order(self, data: Any, wanted: set[str] | None) -> list[Liquidation]:
        rows = data if isinstance(data, list) else [data]
        ingest = now_ms()
        out: list[Liquidation] = []
        for msg in rows:
            o = msg.get("o") or {}
            vs = o.get("s")
            if not vs or (wanted is not None and vs not in wanted):
                continue
            price = dec(o.get("ap")) or dec(o.get("p"))
            size = dec(o.get("z")) or dec(o.get("q"))
            if price is None or size is None or price <= 0:
                continue
            instrument = self._instruments.get(vs)
            size_base = size * (instrument.mult if instrument else Decimal(1))
            # The order side is the exchange's, not the victim's: a SELL forceOrder is
            # the venue closing a LONG position.
            side = Side.long if str(o.get("S", "")).upper() == "SELL" else Side.short
            ts = int(o.get("T") or msg.get("E") or ingest)
            out.append(
                Liquidation(
                    venue=VENUE, symbol=self.symbols.canonical(vs), ts=ts, ingest_ts=ingest,
                    source=f"ws:{STREAM_FORCE_ORDER}", transport=Transport.ws,
                    side=side, price=price, size=size_base,
                    notional_usd=price * size_base,
                    throttled_source=True, completeness=Completeness.lower_bound,
                    event_id=f"{vs}:{ts}:{o.get('ap')}:{o.get('z')}",
                )
            )
        return out

    # ------------------------------------------------------------- health

    async def healthcheck(self) -> list[AdapterHealth]:
        """Cheapest probe (``ping``, w 1) plus a clock-skew read from ``time``."""
        out: list[AdapterHealth] = []
        try:
            res = await self.http.request(
                "GET", EP_PING, weight=1, capability="healthcheck"
            )
            out.append(
                AdapterHealth(
                    venue=VENUE, capability="reachability", ok=True,
                    latency_ms=res.latency_ms, last_ok_ts=now_ms(),
                    last_http_status=res.status,
                )
            )
        except AdapterError as exc:
            out.append(
                AdapterHealth(
                    venue=VENUE, capability="reachability", ok=False,
                    consecutive_fail=1, last_error_class=type(exc).__name__,
                    last_http_status=exc.status, freshness_state="error",
                )
            )
            return out
        try:
            t = await self.http.get(EP_TIME, weight=1, capability="healthcheck")
            skew = now_ms() - int(t["serverTime"])
            out.append(
                AdapterHealth(
                    venue=VENUE, capability="clock", ok=abs(skew) < 1000,
                    latency_ms=float(skew), last_ok_ts=now_ms(),
                    freshness_state="fresh" if abs(skew) < 1000 else "stale",
                )
            )
        except (AdapterError, KeyError, ValueError) as exc:
            out.append(
                AdapterHealth(
                    venue=VENUE, capability="clock", ok=False,
                    last_error_class=type(exc).__name__, freshness_state="error",
                )
            )
        return out

    # ------------------------------------------------------------ internals

    def _venue_symbol(self, symbol: str | None) -> str:
        if symbol is None:
            raise InvalidSymbol("symbol is required", venue=VENUE)
        mapped = self.symbols.venue_symbol(symbol)
        if mapped:
            return mapped
        if symbol.endswith(self.quote_assets):
            return symbol  # caller already passed a venue symbol
        return f"{symbol}{self.quote_assets[0]}"

