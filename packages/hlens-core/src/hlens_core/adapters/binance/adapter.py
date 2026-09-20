"""The Binance USDⓈ-M market-data adapter — step ⑤ of M1-A.

Satisfies :class:`~hlens_core.adapters.base.MarketDataAdapter` structurally
(``_satisfies_the_protocol`` below is the static proof), and nothing more: this
is market data only. Wallet data has its own protocol from M5 and never appears
here — seam ⑤ in as many words.

What every ``fetch_*`` method does, in this order
-------------------------------------------------
1. ``capabilities.require(...)`` — a call for something the venue does not
   publish must cost **zero** weight. On a shared egress IP a pointless request
   is somebody else's 429.
2. ``admission.acquire(cost_of(...))`` — the ledger answers "may I". A denial
   raises :class:`AdmissionDenied`; nothing is sent. The adapter never looks
   inside the grant and never holds a budget of its own (seam ③).
3. the request.
4. ``admission.settle(...)`` with the status the venue returned, **including**
   the 429 and 418 ones — a 418 bans the whole egress IP and takes the still
   running legacy collector down with it, so it can never be swallowed by a
   retry loop (``03`` §6.1). The response body travels with it because ``04``
   §3 makes a 429's body type the only evidence of which limiter fired.
5. normalization into contract objects (:mod:`.normalize`). No raw JSON
   crosses this boundary, and nothing touches a database.

Two things this adapter is *given* rather than decides
------------------------------------------------------
* the **clock**, as a callable returning UTC milliseconds — ``ingest_ts`` is
  "when we received it", and a test that cannot fix that instant cannot check
  staleness arithmetic;
* the **transports** — an :class:`httpx.AsyncClient` and a WebSocket connector.
  Injecting them is what makes every network-facing function testable offline
  against a recorded fixture, which AGENTS §2.6 requires of all of them.

Nothing in this module names a host. The four base URLs come from
``config/venues.yaml`` via :class:`~.endpoints.BinanceEndpoints`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Final, Protocol, cast

import httpx

from hlens_core.adapters.admission import AnyAdmission, CallCost, LanePriority
from hlens_core.adapters.base import KlineInterval, MarketDataAdapter, StreamPlan
from hlens_core.adapters.capabilities import (
    Capability,
    CapabilitySet,
    capability_of_ls_ratio_kind,
)
from hlens_core.adapters.symbols import SymbolMap, SymbolTable
from hlens_core.contracts import (
    InstrumentRecord,
    LsRatioKind,
    LsRatioPoint,
    MarketRecord,
    Venue,
)

from . import normalize as norm
from .capabilities import binance_capabilities
from .costs import (
    BUCKET_FAPI_WEIGHT,
    BinanceCall,
    call_of_ls_ratio_kind,
    cost_of,
    cost_of_call,
    path_of_call,
    path_of_ls_ratio_kind,
    stream_plan_for,
)
from .endpoints import (
    MAX_FUNDING_RATE_LIMIT,
    MAX_KLINE_LIMIT,
    WS_FORCE_ORDER_ALL,
    WS_MARK_PRICE_ALL,
    BinanceEndpoints,
    ws_mark_price_stream,
)

__all__ = [
    "AdmissionDenied",
    "BinanceApiError",
    "BinanceMarketDataAdapter",
    "MillisClock",
    "WsConnection",
    "WsConnector",
    "system_clock_ms",
]

#: How much of an error body is handed to the ledger. Enough for ``04`` §3's
#: "JSON ``null`` vs nginx HTML page" classification, short enough that a
#: venue's error page cannot end up in a log line in full.
_MAX_BODY_CHARS: Final = 512


class BinanceApiError(RuntimeError):
    """Binance answered with a non-2xx status.

    Carries the status so a caller can tell a 451 (``04`` §6: the application
    layer refusing by geography, which means "use the mirror path") from a 418
    (the IP is banned, which means stop) without parsing a message.
    """

    def __init__(self, *, status: int, path: str, body: str) -> None:
        super().__init__(f"binance {path} -> HTTP {status}: {body}")
        self.status = status
        self.path = path
        self.body = body


class AdmissionDenied(RuntimeError):
    """The ledger refused the allowance, so nothing was sent.

    Not an error of the venue's and not a reason to retry immediately: the
    ledger already knows why (a ceiling, a paced lane, a frozen opportunistic
    tier, a halted venue) and the caller's answer is to come back next tick.
    """

    def __init__(self, cost: CallCost) -> None:
        super().__init__(
            f"rate-limit ledger denied {cost.weight} on {cost.bucket} "
            f"({cost.priority.value}); nothing was sent"
        )
        self.cost = cost


MillisClock = Callable[[], int]
"""UTC milliseconds since the epoch. Injected so ``ingest_ts`` is testable."""


def system_clock_ms() -> int:
    """The real clock. ``time.time_ns()`` rather than ``time.time()`` so the
    millisecond truncation happens once, in a known place."""
    import time

    return time.time_ns() // 1_000_000


WsMessage = str | bytes


class WsConnection(Protocol):
    """What this adapter needs of a WebSocket: messages, in order."""

    def __aiter__(self) -> AsyncIterator[WsMessage]: ...


WsConnector = Callable[[str], AbstractAsyncContextManager[WsConnection]]
"""Opens one connection. The real one is :func:`websockets_connector`; a test
passes a replay of a recorded fixture, which is the only kind this repository
can record anywhere but the production host (AGENTS §3.3)."""


def websockets_connector(url: str) -> AbstractAsyncContextManager[WsConnection]:
    """The production connector.

    ``03`` §3 pins ``websockets`` 17.1. The import is local so that importing
    this module — which every offline test does — does not require the library
    to be importable in a context that will never open a socket.
    """
    from websockets.asyncio.client import connect

    return cast(AbstractAsyncContextManager[WsConnection], connect(url))


class BinanceMarketDataAdapter:
    """One venue's market data, normalized, with its capabilities declared."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        endpoints: BinanceEndpoints,
        symbols: SymbolMap | None = None,
        clock: MillisClock = system_clock_ms,
        connector: WsConnector = websockets_connector,
        mirror: bool = False,
    ) -> None:
        """
        ``symbols`` is the mapping the adapter normalizes **with**. It starts
        empty on purpose: :meth:`fetch_instruments` is the bootstrap — it reads
        the unified names straight out of ``exchangeInfo`` and needs no map —
        and ``universe`` (M1-D) owns the decision of which coins the table then
        holds. :meth:`adopt_symbols` is how it hands one back.

        ``mirror`` switches every REST path onto the configured mirror host
        (``04`` §6; the host itself lives in ``config/venues.yaml``, never
        here). It is a **preflight** decision — only preflight knows where the
        egress actually is — so it is a constructor argument and never
        something a failed call flips on its own.
        """
        self._client = client
        self._endpoints = endpoints
        self._symbols: SymbolMap = symbols if symbols is not None else SymbolTable.of(())
        self._clock = clock
        self._connector = connector
        self._mirror = mirror
        self._capabilities = binance_capabilities()
        self._unknown_symbols: list[str] = []
        self._used_weight_1m: int | None = None

    # ------------------------------------------------------------------ #
    # Identity and declarations
    # ------------------------------------------------------------------ #
    @property
    def venue(self) -> Venue:
        return Venue.BINANCE

    @property
    def capabilities(self) -> CapabilitySet:
        return self._capabilities

    @property
    def symbols(self) -> SymbolMap:
        return self._symbols

    def adopt_symbols(self, symbols: SymbolMap) -> None:
        """Replace the mapping used for normalization.

        Called after a universe reconciliation. Deliberately not a setter on
        :attr:`symbols`: swapping the map is an event worth grepping for, not an
        assignment that could happen anywhere.
        """
        self._symbols = symbols

    @property
    def unknown_venue_symbols(self) -> tuple[str, ...]:
        """Venue contract names seen on a market-wide response or stream that
        the current mapping does not know — a coin listed since the last
        reconciliation. Skipped and **counted**, never raised mid-stream."""
        return tuple(self._unknown_symbols)

    @property
    def last_used_weight_1m(self) -> int | None:
        """The last ``X-MBX-USED-WEIGHT-1M`` Binance sent back, or ``None``.

        ``04`` §7 asks preflight to calibrate the local ledger against this
        header. Exposed as a plain number rather than pushed anywhere: the
        adapter has no business writing to the ledger (seam ③), and whoever
        wired the two together can read it.
        """
        return self._used_weight_1m

    def cost_of(
        self,
        capability: Capability,
        *,
        symbols: int | None = None,
        rows: int | None = None,
    ) -> CallCost:
        return cost_of(capability, symbols=symbols, rows=rows)

    def stream_plan(self, capability: Capability, *, symbols: int | None = None) -> StreamPlan:
        self._capabilities.require(capability)
        return stream_plan_for(capability, symbols=symbols)

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #
    async def _get(
        self,
        cost: CallCost,
        path: str,
        params: Mapping[str, str | int],
        *,
        admission: AnyAdmission,
    ) -> Any:
        """One admitted, settled GET. Every REST call in this file goes through
        here, which is why there is exactly one place that can forget to
        settle."""
        grant = admission.acquire(cost)
        if not grant:
            raise AdmissionDenied(cost)

        url = self._endpoints.rest_url(path, mirror=self._mirror)
        try:
            response = await self._client.get(url, params=dict(params))
        except BaseException:
            # The call never happened, so the allowance is not ours to keep.
            admission.release(grant)
            raise

        failed = response.status_code >= 400
        body = response.text[:_MAX_BODY_CHARS] if failed else None
        admission.settle(
            grant,
            actual_cost=cost.weight,
            status=response.status_code,
            retry_after_s=_retry_after(response.headers),
            body=body,
        )
        self._note_used_weight(response.headers)
        if failed:
            raise BinanceApiError(status=response.status_code, path=path, body=body or "")
        return response.json()

    def _note_used_weight(self, headers: httpx.Headers) -> None:
        raw = headers.get("x-mbx-used-weight-1m")
        if raw is None:
            return
        try:
            self._used_weight_1m = int(raw)
        except ValueError:
            # A header we cannot read is not worth failing a good response over;
            # preflight's calibration simply has nothing to calibrate against.
            self._used_weight_1m = None

    def _now(self) -> int:
        return self._clock()

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    async def fetch_instruments(
        self, *, admission: AnyAdmission
    ) -> tuple[InstrumentRecord, ...]:
        """Every perpetual Binance lists, as of now (F1, F3).

        **Two calls against two different buckets**: ``exchangeInfo`` charges 1
        weight on ``binance:fapi_weight``, ``fundingInfo`` charges one request
        on ``binance:funding_rate``. They are acquired and settled separately
        because they are separate accounts (``04`` §2) — charging both to one
        would make the tightest bucket in the system invisible.

        ``fundingInfo`` lists only the symbols that are **not** on the 8-hour
        grid, so a symbol missing from it gets
        :data:`~.symbols.DEFAULT_FUNDING_INTERVAL_H`, which is a statement about
        Binance rather than a fallback.
        """
        self._capabilities.require(Capability.INSTRUMENTS)
        payload = await self._get(
            cost_of_call(BinanceCall.EXCHANGE_INFO),
            path_of_call(BinanceCall.EXCHANGE_INFO),
            {},
            admission=admission,
        )
        intervals = await self.fetch_funding_intervals(admission=admission)
        return norm.normalize_exchange_info(
            payload, ingest_ts=self._now(), funding_interval_h=intervals
        )

    async def fetch_funding_intervals(self, *, admission: AnyAdmission) -> dict[str, int]:
        """``fundingInfo`` → ``{venue symbol: interval hours}`` (``04`` §2).

        Not in ``MarketDataAdapter``: the protocol has one discovery method and
        Binance is the only venue that splits the interval off into a second
        endpoint. Exposed anyway because ``03`` §6's lane table polls it hourly
        on its own, independently of the daily instrument reconciliation.
        """
        self._capabilities.require(Capability.INSTRUMENTS)
        payload = await self._get(
            cost_of_call(BinanceCall.FUNDING_INFO),
            path_of_call(BinanceCall.FUNDING_INFO),
            {},
            admission=admission,
        )
        return norm.normalize_funding_info(payload)

    # ------------------------------------------------------------------ #
    # Fast lane
    # ------------------------------------------------------------------ #
    async def fetch_mark_prices(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """Mark and index, from ``premiumIndex``. ``symbols=None`` = whole market.

        The returned records carry the funding fields too, because **one**
        request answers both capabilities and ``03`` §6's lane table budgets one
        10-weight call per 30 seconds, not two. Which is also the warning:
        calling this *and* :meth:`fetch_funding` on the same tick spends 20
        weight for one lane's worth of data.
        """
        return await self._premium_index(
            Capability.MARK_PRICE, admission=admission, symbols=symbols
        )

    async def fetch_funding(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """Funding over Binance's **native** interval, plus the next settlement.

        Same endpoint and same single charge as :meth:`fetch_mark_prices` — see
        there. ``funding_rate`` is the raw value and ``funding_interval_h`` is
        8 hours, or 4 for the symbols ``fundingInfo`` named; the 8-hour figure
        is the contract's :attr:`~hlens_core.contracts.MarketRecord.
        funding_rate_8h` property, derived on read and never stored.
        """
        return await self._premium_index(
            Capability.FUNDING_RATE, admission=admission, symbols=symbols
        )

    async def _premium_index(
        self,
        capability: Capability,
        *,
        admission: AnyAdmission,
        symbols: Sequence[str] | None,
    ) -> tuple[MarketRecord, ...]:
        self._capabilities.require(capability)
        records: list[MarketRecord] = []
        for params, count in self._per_symbol_params(symbols):
            payload = await self._get(
                cost_of_call(BinanceCall.PREMIUM_INDEX, symbols=count),
                path_of_call(BinanceCall.PREMIUM_INDEX),
                params,
                admission=admission,
            )
            records.extend(
                norm.normalize_premium_index(
                    payload,
                    symbols=self._symbols,
                    ingest_ts=self._now(),
                    unknown=self._unknown_symbols,
                )
            )
        return tuple(records)

    # ------------------------------------------------------------------ #
    # Slow lane
    # ------------------------------------------------------------------ #
    async def fetch_open_interest(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """Open interest in base units, as slow-lane records.

        ``openInterest`` **requires** a symbol (``04`` §2), so this is the
        endpoint that grows linearly with the coin count — 180 coins = 180
        weight a minute, which is three quarters of ``03`` §6's whole 241. Each
        symbol is its own acquire/settle, so the ledger can pace them and deny
        one without losing the rest.

        ``symbols=None`` means every coin the mapping knows, not "the whole
        market in one call": there is no such call here.
        """
        self._capabilities.require(Capability.OPEN_INTEREST)
        wanted = self._venue_symbols(symbols)
        records: list[MarketRecord] = []
        for venue_symbol in wanted:
            payload = await self._get(
                cost_of_call(BinanceCall.OPEN_INTEREST, symbols=1),
                path_of_call(BinanceCall.OPEN_INTEREST),
                {"symbol": venue_symbol},
                admission=admission,
            )
            records.extend(
                norm.normalize_open_interest(
                    payload,
                    symbols=self._symbols,
                    ingest_ts=self._now(),
                    unknown=self._unknown_symbols,
                )
            )
        return tuple(records)

    async def fetch_ticker_24h(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """Rolling 24 h change and quote volume, as slow-lane records.

        ``symbols=None`` is one 40-weight call for the whole market (``04`` §2),
        which is what ``03`` §6's slow lane budgets.
        """
        self._capabilities.require(Capability.TICKER_24H)
        records: list[MarketRecord] = []
        for params, count in self._per_symbol_params(symbols):
            payload = await self._get(
                cost_of_call(BinanceCall.TICKER_24H, symbols=count),
                path_of_call(BinanceCall.TICKER_24H),
                params,
                admission=admission,
            )
            records.extend(
                norm.normalize_ticker_24h(
                    payload,
                    symbols=self._symbols,
                    ingest_ts=self._now(),
                    unknown=self._unknown_symbols,
                )
            )
        return tuple(records)

    # ------------------------------------------------------------------ #
    # Ratios
    # ------------------------------------------------------------------ #
    async def fetch_ls_ratio(
        self,
        *,
        admission: AnyAdmission,
        kind: LsRatioKind,
        symbol: str,
        limit: int | None = None,
    ) -> tuple[LsRatioPoint, ...]:
        """One of seam ①'s **three** ratio kinds, for one coin.

        Against ``binance:futures_data``, the separate request bucket: these
        endpoints charge no weight and have their own limit, and at 54 of 80
        requests a minute this is the tightest bucket in the system (``03`` §6).

        ``period`` is fixed at 5 minutes and is not a parameter of the protocol,
        which is right: 决定 A8 polls every 10 minutes and uses ``limit`` to take
        **both** of the window's 5-minute points back, and that grid is what
        ``03`` §5 sizes the table on. A caller that could change it could
        silently change the stored density.

        ``topLongShortAccountRatio`` is a fourth endpoint Binance publishes and
        ``04`` §1/§2 lists — it has no kind here because ``03`` §5 records three
        and no confirmed feature consumes a fourth. See "Doc corrections".
        """
        capability = capability_of_ls_ratio_kind(kind)
        self._capabilities.require(capability)
        mapping = self._symbols.to_venue(symbol)
        if mapping is None:
            raise KeyError(f"binance has no contract mapped to the unified symbol {symbol!r}")

        params: dict[str, str | int] = {
            "symbol": mapping.venue_symbol,
            "period": norm.LS_RATIO_PERIOD,
        }
        if limit is not None:
            params["limit"] = limit
        payload = await self._get(
            cost_of_call(call_of_ls_ratio_kind(kind)),
            path_of_ls_ratio_kind(kind),
            params,
            admission=admission,
        )
        return norm.normalize_ls_ratio(
            payload, kind=kind, symbol=symbol, ingest_ts=self._now()
        )

    # ------------------------------------------------------------------ #
    # History
    # ------------------------------------------------------------------ #
    async def fetch_klines(
        self,
        *,
        admission: AnyAdmission,
        symbol: str,
        interval: KlineInterval,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int | None = None,
    ) -> tuple[MarketRecord, ...]:
        """Closed bars → ``semantic='candle_close'`` backfill records.

        **M1 calls this nowhere**: F2 is explicit that M1 collects no klines and
        builds no kline table. It exists because AGENTS §3.4 requires it of
        every adapter and because M2's backfill is its only caller, on the
        opportunistic tier — ``03`` §6.1: "Binance 侧的 opportunistic 只有 K 线
        回补".

        The weight is charged on ``limit`` (``04`` §2's ladder), and an omitted
        ``limit`` is charged at Binance's own default of 500, not at zero.
        """
        self._capabilities.require(Capability.KLINES)
        mapping = self._symbols.to_venue(symbol)
        if mapping is None:
            raise KeyError(f"binance has no contract mapped to the unified symbol {symbol!r}")
        if limit is not None and not 1 <= limit <= MAX_KLINE_LIMIT:
            raise ValueError(f"04 §2: klines limit is 1..{MAX_KLINE_LIMIT}; got {limit}")

        params: dict[str, str | int] = {
            "symbol": mapping.venue_symbol,
            "interval": interval.value,
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        if limit is not None:
            params["limit"] = limit

        payload = await self._get(
            cost_of_call(BinanceCall.KLINES, rows=limit),
            path_of_call(BinanceCall.KLINES),
            params,
            admission=admission,
        )
        return norm.normalize_klines(
            payload,
            symbol=symbol,
            interval=interval.value,
            ingest_ts=self._now(),
            now_ms=self._now(),
        )

    async def fetch_funding_history(
        self,
        *,
        admission: AnyAdmission,
        symbol: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int | None = None,
        priority: LanePriority | None = None,
    ) -> tuple[MarketRecord, ...]:
        """``fundingRate`` — every settled funding period, back to listing.

        Not in ``MarketDataAdapter``: A4's protocol has ``fetch_funding`` for
        the current rate and no slot for its history, while ``03`` §6's lane
        table runs this one on its own schedule ("每 8h 对账一次") and ``04`` §5
        names it the cold-start source. So it is a Binance method until a later
        step decides whether the protocol should grow one; see the PR.

        ``priority`` exists because this endpoint genuinely serves two tiers at
        the same price: the 8-hour reconciliation is resident (the default) and
        the cold-start backfill is opportunistic. The caller says which; the
        adapter does not guess from the arguments.
        """
        self._capabilities.require(Capability.FUNDING_RATE)
        if limit is not None and not 1 <= limit <= MAX_FUNDING_RATE_LIMIT:
            raise ValueError(
                f"04 §2: fundingRate limit is 1..{MAX_FUNDING_RATE_LIMIT}; got {limit}"
            )
        params: dict[str, str | int] = {}
        if symbol is not None:
            mapping = self._symbols.to_venue(symbol)
            if mapping is None:
                raise KeyError(f"binance has no contract mapped to {symbol!r}")
            params["symbol"] = mapping.venue_symbol
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        if limit is not None:
            params["limit"] = limit

        payload = await self._get(
            cost_of_call(BinanceCall.FUNDING_RATE_HISTORY, priority=priority),
            path_of_call(BinanceCall.FUNDING_RATE_HISTORY),
            params,
            admission=admission,
        )
        return norm.normalize_funding_rate_history(
            payload,
            symbols=self._symbols,
            ingest_ts=self._now(),
            unknown=self._unknown_symbols,
        )

    # ------------------------------------------------------------------ #
    # Streams — both on the /market group (04 §2, 2026-04-23)
    # ------------------------------------------------------------------ #
    def stream_mark_prices(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> AsyncIterator[MarketRecord]:
        """``!markPrice@arr@1s`` — the 30-second fast lane's real source.

        ``symbols=None`` subscribes to the whole-market array, which is one
        stream and covers every coin at once; a list subscribes per symbol,
        which is one stream each and exists only as a fallback.

        The connection itself is admitted through the WebSocket side of the
        ledger by the caller, using :meth:`stream_plan`. ``admission`` is here
        so that a handshake's 429 or 418 is reported — a 418 bans the shared
        egress IP.
        """
        return self._stream_mark_prices(admission=admission, symbols=symbols)

    async def _stream_mark_prices(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None
    ) -> AsyncIterator[MarketRecord]:
        self._capabilities.require(Capability.MARK_PRICE_STREAM)
        streams: tuple[str, ...]
        if symbols is None:
            streams = (WS_MARK_PRICE_ALL,)
        else:
            streams = tuple(ws_mark_price_stream(s) for s in self._venue_symbols(symbols))
        async for payload in self._messages(streams, admission=admission):
            for record in norm.normalize_ws_mark_price(
                payload,
                symbols=self._symbols,
                ingest_ts=self._now(),
                unknown=self._unknown_symbols,
            ):
                yield record

    def stream_liquidations(
        self, *, admission: AnyAdmission
    ) -> AsyncIterator[norm.ForcedOrderObservation]:
        """``!forceOrder@arr`` — **always a lower bound**.

        Binance pushes at most one forced order per symbol per second (``04``
        §2), so the stream is a floor by construction; the capability says
        ``lower_bound`` with ``throttled_source=True`` and F12 carries the
        ``下界`` label to every number built on it.

        Yields :class:`~.normalize.ForcedOrderObservation` — seam ①'s five
        attributes and nothing else, because M1 has no liquidation contract.
        M2 narrows this annotation; it does not change the method.
        """
        return self._stream_liquidations(admission=admission)

    async def _stream_liquidations(
        self, *, admission: AnyAdmission
    ) -> AsyncIterator[norm.ForcedOrderObservation]:
        self._capabilities.require(Capability.LIQUIDATION_STREAM)
        async for payload in self._messages((WS_FORCE_ORDER_ALL,), admission=admission):
            for record in norm.normalize_ws_force_order(
                payload,
                symbols=self._symbols,
                ingest_ts=self._now(),
                unknown=self._unknown_symbols,
            ):
                yield record

    async def _messages(
        self, streams: tuple[str, ...], *, admission: AnyAdmission
    ) -> AsyncIterator[Any]:
        """Connect to the ``/market`` group and yield one decoded payload per frame.

        A handshake that comes back with an HTTP status is reported to the
        ledger before it is re-raised: a 418 is an IP ban shared with the legacy
        collector, and a reconnect loop that swallowed it would keep knocking.
        """
        url = self._endpoints.ws_url(streams)
        try:
            connection = self._connector(url)
        except Exception as error:  # pragma: no cover - connector-specific
            self._observe_handshake(error, admission=admission)
            raise
        try:
            async with connection as socket:
                async for message in socket:
                    yield _frame_payload(message)
        except Exception as error:
            self._observe_handshake(error, admission=admission)
            raise

    def _observe_handshake(self, error: BaseException, *, admission: AnyAdmission) -> None:
        """Report a handshake status code, if the failure carries one.

        Duck-typed on purpose: ``websockets`` raises its own exception class and
        this module must stay usable with a replay connector that raises
        something else. Reported against the venue's weight bucket because the
        ledger's 418 handling is venue-wide — the ban is on the IP, not on an
        endpoint.
        """
        status = getattr(getattr(error, "response", None), "status_code", None)
        if isinstance(status, int):
            admission.observe(BUCKET_FAPI_WEIGHT, status=status)

    # ------------------------------------------------------------------ #
    # Small shared pieces
    # ------------------------------------------------------------------ #
    def _venue_symbols(self, symbols: Sequence[str] | None) -> tuple[str, ...]:
        """Unified names → this venue's contract names, in the caller's order.

        ``None`` means every coin the mapping currently holds — which is the
        universe as of the last reconciliation, not "everything Binance lists".
        """
        if symbols is None:
            return tuple(mapping.venue_symbol for mapping in self._symbols)
        resolved: list[str] = []
        for symbol in symbols:
            mapping = self._symbols.to_venue(symbol)
            if mapping is None:
                raise KeyError(f"binance has no contract mapped to the unified symbol {symbol!r}")
            resolved.append(mapping.venue_symbol)
        return tuple(resolved)

    def _per_symbol_params(
        self, symbols: Sequence[str] | None
    ) -> tuple[tuple[dict[str, str | int], int | None], ...]:
        """The request(s) an endpoint that takes *at most one* symbol needs.

        ``None`` is one market-wide call; a list is one call per symbol. There
        is no middle form — Binance's ``symbol`` parameter is singular, and
        pretending otherwise is how a 10-weight market-wide call gets charged as
        if it were one.
        """
        if symbols is None:
            return (({}, None),)
        return tuple(({"symbol": v}, 1) for v in self._venue_symbols(symbols))


def _retry_after(headers: httpx.Headers) -> float | None:
    """``Retry-After`` in seconds, or ``None``.

    ``04`` §2: Binance answers a 418 with a ``Retry-After`` that escalates from
    two minutes to three days. Only the numeric (delta-seconds) form is read; an
    HTTP-date form is left to the ledger's own backoff rather than parsed into a
    number that might be wrong by a time zone.
    """
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _frame_payload(message: WsMessage) -> Any:
    """One WebSocket frame → the payload the normalizers expect.

    Binance's combined-stream form wraps each payload as ``{"stream": …,
    "data": …}`` and the single-stream form sends the payload bare. The
    2026-04-23 split documents the group base URLs but not whether the
    ``/ws/`` and ``/stream?streams=`` suffixes survived unchanged (``04`` §11
    第 9 项 leaves the connection rules ``未验证``), so both shapes are accepted
    rather than one being assumed correct until M1-G records the real thing.
    """
    decoded = json.loads(message)
    if isinstance(decoded, dict) and "data" in decoded and "stream" in decoded:
        return decoded["data"]
    return decoded


def _satisfies_the_protocol(adapter: BinanceMarketDataAdapter) -> MarketDataAdapter:
    """Static proof that seam ②'s protocol is satisfied.

    mypy checks this assignment with the production code (``[tool.mypy] files``
    includes ``packages``), which is stronger than a runtime ``isinstance``
    against a runtime-checkable protocol: that one compares method *names* and
    would pass with the wrong signatures.
    """
    return adapter
