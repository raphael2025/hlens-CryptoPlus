"""The Hyperliquid market-data adapter — step ⑥ of M1-A, and the last of them.

Satisfies :class:`~hlens_core.adapters.base.MarketDataAdapter` structurally
(``_satisfies_the_protocol`` below is the static proof), and nothing more.

**Market data only.** Wallet data has its own protocol from M5 (seam ⑤) and
never appears here. That sentence costs more on this venue than on Binance and
is worth saying twice: M5's wallet collection *is* Hyperliquid, its endpoints
(``clearinghouseState``, ``userFills``, ``userFunding``) sit on the very same
``/info`` path this file posts to, and the one public source of forced-fill
information is among them (``04`` §8). None of it is here — not a method, not a
capability, not a request type — because seam ⑤ is a different protocol in
:mod:`hlens_core.wallet`, which this package neither imports nor is imported
by, and because a wallet endpoint reachable from a market-data adapter is a
boundary that exists only in the documentation.

What every ``fetch_*`` method does, in this order
-------------------------------------------------
1. ``capabilities.require(...)`` — a call for something the venue does not
   publish must cost **zero** weight. Four of the thirteen capabilities refuse
   here, and on a shared egress IP a pointless request is somebody else's 429.
2. ``admission.acquire(cost_of(...))`` — the ledger answers "may I". A denial
   raises :class:`AdmissionDenied`; nothing is sent.
3. the request: a **POST** to ``/info`` with a ``type`` field.
4. ``admission.settle(...)`` with the status **and the body**. On this venue
   the body is not a detail: Hyperliquid sends no rate-limit headers at all,
   and ``04`` §3 makes a 429's body type the only evidence of which limiter
   fired — a JSON ``null`` is the weight limiter, an nginx HTML page is the
   connection-rate limiter, and the two need opposite remedies. So the body is
   handed over **verbatim** and this file classifies nothing, decides no
   backoff and sends no notification: ``classify_hl_429_body``, the AIMD cut,
   the one-hour opportunistic freeze and ``03`` §6.1's mandatory F4 private
   message all live in the ledger, which is where the budget is.
5. normalization into contract objects (:mod:`.normalize`). No raw JSON crosses
   this boundary, and nothing touches a database.

Two things this adapter is *given* rather than decides
------------------------------------------------------
* the **clock**, as a callable returning UTC milliseconds. It matters more here
  than on Binance: Hyperliquid's snapshot endpoints carry no server timestamp
  at all, so the clock supplies ``ts`` as well as ``ingest_ts``;
* the **transports** — an :class:`httpx.AsyncClient` and a WebSocket connector
  — which is what makes every network-facing function testable offline against
  a fixture, as AGENTS §2.6 requires of all of them.

Nothing in this module names a host. Both base URLs come from
``config/venues.yaml`` via :class:`~.endpoints.HyperliquidEndpoints`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Final, Protocol, cast

import httpx

from hlens_core.adapters.admission import AnyAdmission, CallCost, LanePriority
from hlens_core.adapters.base import (
    KlineInterval,
    MarketDataAdapter,
    NormalizedRecord,
    StreamPlan,
)
from hlens_core.adapters.capabilities import (
    Capability,
    CapabilitySet,
    UnsupportedCapability,
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
from .capabilities import hyperliquid_capabilities
from .costs import (
    BUCKET_INFO_WEIGHT,
    HyperliquidCall,
    cost_of,
    cost_of_call,
    request_type_of_call,
    stream_plan_for,
)
from .endpoints import (
    MAX_CANDLE_HISTORY_ROWS,
    MAX_ROWS_PER_RANGED_RESPONSE,
    WS_CHANNEL_ALL_MIDS,
    HyperliquidEndpoints,
    subscribe_frame,
)

__all__ = [
    "AdmissionDenied",
    "HyperliquidApiError",
    "HyperliquidMarketDataAdapter",
    "MillisClock",
    "WsConnection",
    "WsConnector",
    "system_clock_ms",
    "websockets_connector",
]

#: How much of an error body is handed to the ledger. Enough for ``04`` §3's
#: "JSON ``null`` vs nginx HTML page" classification — which reads the first
#: non-space character, so a truncated HTML page still classifies — and short
#: enough that a venue's error page cannot reach a log line in full.
_MAX_BODY_CHARS: Final = 512


class HyperliquidApiError(RuntimeError):
    """Hyperliquid answered with a non-2xx status.

    Carries the status **and the body**, because on this venue the body is the
    only thing that says which limiter fired (``04`` §3). The ledger has
    already been told both by the time this is raised; this is for the caller
    that wants to log or branch, not a second place where the classification
    happens.
    """

    def __init__(self, *, status: int, request_type: str, body: str) -> None:
        super().__init__(f"hyperliquid /info type={request_type} -> HTTP {status}: {body}")
        self.status = status
        self.request_type = request_type
        self.body = body


class AdmissionDenied(RuntimeError):
    """The ledger refused the allowance, so nothing was sent.

    Not an error of the venue's and not a reason to retry immediately: the
    ledger already knows why (a ceiling, a paced lane, a frozen opportunistic
    tier after a 429, a halted venue) and the caller's answer is to come back
    next tick.
    """

    def __init__(self, cost: CallCost) -> None:
        super().__init__(
            f"rate-limit ledger denied {cost.weight} on {cost.bucket} "
            f"({cost.priority.value}); nothing was sent"
        )
        self.cost = cost


MillisClock = Callable[[], int]
"""UTC milliseconds since the epoch. Injected so ``ts`` and ``ingest_ts`` are
testable — and on this venue ``ts`` comes from here for every snapshot, because
the venue dates none of them."""


def system_clock_ms() -> int:
    """The real clock. ``time.time_ns()`` rather than ``time.time()`` so the
    millisecond truncation happens once, in a known place."""
    import time

    return time.time_ns() // 1_000_000


WsMessage = str | bytes


class WsConnection(Protocol):
    """What this adapter needs of a WebSocket: **send**, then messages.

    The ``send`` is the difference from Binance's connection protocol and it is
    the venue's, not a preference: Binance names its streams in the URL,
    Hyperliquid connects to one address and then sends a subscription frame
    (``04`` §3).
    """

    async def send(self, message: str) -> None: ...

    def __aiter__(self) -> AsyncIterator[WsMessage]: ...


WsConnector = Callable[[str], AbstractAsyncContextManager[WsConnection]]
"""Opens one connection. The real one is :func:`websockets_connector`; a test
passes a replay of a recorded fixture, which is the only kind this repository
can record anywhere but the production host (AGENTS §3.3)."""


def websockets_connector(url: str) -> AbstractAsyncContextManager[WsConnection]:
    """The production connector.

    ``03`` §3 pins ``websockets`` 17.1 and requires the explicit
    ``websockets.asyncio.client`` import. The import is local so that importing
    this module — which every offline test does — does not require the library
    in a context that will never open a socket.
    """
    from websockets.asyncio.client import connect

    return cast(AbstractAsyncContextManager[WsConnection], connect(url))


class HyperliquidMarketDataAdapter:
    """One venue's market data, normalized, with its capabilities declared."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        endpoints: HyperliquidEndpoints,
        symbols: SymbolMap | None = None,
        clock: MillisClock = system_clock_ms,
        connector: WsConnector = websockets_connector,
    ) -> None:
        """
        ``symbols`` is the mapping the adapter normalizes **with**. It starts
        empty on purpose: :meth:`fetch_instruments` is the bootstrap — it reads
        the unified names straight out of ``meta`` and needs no map — and
        ``universe`` (M1-D) owns the decision of which coins the table then
        holds. :meth:`adopt_symbols` is how it hands one back.

        There is no ``mirror`` argument, unlike the Binance adapter: ``04`` §6
        records ``api.hyperliquid.xyz`` answering 200 from both a US and a
        non-US egress, so this venue has one host and no switch to get wrong.
        """
        self._client = client
        self._endpoints = endpoints
        self._symbols: SymbolMap = symbols if symbols is not None else SymbolTable.of(())
        self._clock = clock
        self._connector = connector
        self._capabilities = hyperliquid_capabilities()
        self._unknown_symbols: list[str] = []
        self._other_venue_keys: list[str] = []
        self._asset_metadata: tuple[norm.AssetMeta, ...] = ()

    # ------------------------------------------------------------------ #
    # Identity and declarations
    # ------------------------------------------------------------------ #
    @property
    def venue(self) -> Venue:
        return Venue.HYPERLIQUID

    @property
    def capabilities(self) -> CapabilitySet:
        return self._capabilities

    @property
    def symbols(self) -> SymbolMap:
        return self._symbols

    def adopt_symbols(self, symbols: SymbolMap) -> None:
        """Replace the mapping used for normalization.

        Called after a universe reconciliation. Deliberately not a setter on
        :attr:`symbols`: swapping the map is an event worth grepping for, not
        an assignment that could happen anywhere.
        """
        self._symbols = symbols

    @property
    def unknown_venue_symbols(self) -> tuple[str, ...]:
        """Coin names seen on a market-wide response or stream that the current
        mapping does not know — a coin listed since the last reconciliation.
        Skipped and **counted**, never raised mid-stream."""
        return tuple(self._unknown_symbols)

    @property
    def other_venue_keys(self) -> tuple[str, ...]:
        """Venue keys seen in a ``predictedFundings`` response that are not
        Hyperliquid's (``04`` §3: the response carries other exchanges'
        predictions, Binance's included). Counted so that ``04`` §3's unnamed
        key vocabulary can be filled in from a real recording (``M1-G``)
        instead of guessed at — and so that the fact that they were **skipped**
        is observable rather than assumed."""
        return tuple(self._other_venue_keys)

    @property
    def asset_metadata(self) -> tuple[norm.AssetMeta, ...]:
        """``szDecimals`` and ``maxLeverage`` from the last ``meta`` response.

        ``04`` §3 names both as key fields and ``03`` §5's ``instruments`` has a
        column for neither, so they are exposed rather than stored — the same
        shape as the Binance adapter's ``last_used_weight_1m``: the adapter
        reads what the venue said and stops there. M6's key levels need
        ``maxLeverage`` for ``04`` §3's MMR approximation, which is **not**
        computed here.
        """
        return self._asset_metadata

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
    async def _post(
        self,
        cost: CallCost,
        body: Mapping[str, Any],
        *,
        admission: AnyAdmission,
    ) -> Any:
        """One admitted, settled POST to ``/info``.

        Every REST call in this file goes through here, which is why there is
        exactly one place that can forget to settle — and exactly one place
        that decides what reaches the ledger after a 429.
        """
        grant = admission.acquire(cost)
        if not grant:
            raise AdmissionDenied(cost)

        request_type = str(body.get("type", "?"))
        try:
            response = await self._client.post(self._endpoints.info_url(), json=dict(body))
        except BaseException:
            # The call never happened, so the allowance is not ours to keep.
            admission.release(grant)
            raise

        failed = response.status_code >= 400
        # Verbatim, not classified: 04 §3 makes the body type the only evidence
        # of WHICH Hyperliquid limiter fired, and 03 §6.1 puts the remedy and
        # the mandatory F4 private message in the ledger. An adapter that
        # decided here would be a second, invisible backoff policy.
        error_body = response.text[:_MAX_BODY_CHARS] if failed else None
        admission.settle(
            grant,
            actual_cost=cost.weight,
            status=response.status_code,
            retry_after_s=_retry_after(response.headers),
            body=error_body,
        )
        if failed:
            raise HyperliquidApiError(
                status=response.status_code, request_type=request_type, body=error_body or ""
            )
        return response.json()

    def _now(self) -> int:
        return self._clock()

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    async def fetch_instruments(
        self, *, admission: AnyAdmission
    ) -> tuple[InstrumentRecord, ...]:
        """Every perpetual Hyperliquid lists, as of now (F1, F3).

        **One call**, unlike Binance's two: there is no second endpoint for
        funding intervals because there are no exceptions to list — every
        Hyperliquid perpetual funds hourly (``04`` §3), so
        :data:`~.symbols.FUNDING_INTERVAL_H` is a constant on every record and
        not a lookup with a fallback.

        The response's ``szDecimals`` and ``maxLeverage`` are parsed in the same
        pass and left on :attr:`asset_metadata`; they have no column in ``03``
        §5 and are not invented into one.
        """
        self._capabilities.require(Capability.INSTRUMENTS)
        payload = await self._post(
            cost_of_call(HyperliquidCall.META),
            {"type": request_type_of_call(HyperliquidCall.META)},
            admission=admission,
        )
        observed = self._now()
        self._asset_metadata = norm.asset_metadata(payload)
        return norm.normalize_meta(payload, ingest_ts=observed, observed_ts=observed)

    # ------------------------------------------------------------------ #
    # The one call that answers four capabilities
    # ------------------------------------------------------------------ #
    async def fetch_asset_contexts(
        self,
        *,
        admission: AnyAdmission,
        capability: Capability = Capability.MARK_PRICE,
    ) -> tuple[MarketRecord, ...]:
        """``metaAndAssetCtxs`` — mark, oracle, premium, funding, OI and 24 h
        volume for the whole market, in **one** W=20 call.

        This is the method ``03`` §6's fast lane actually calls, twice a minute,
        for 40 weight — and the reason the four protocol methods below delegate
        to it rather than each making a request. **Calling all four on one tick
        spends 80 weight for one tick's data**, which on the transitional
        Hyperliquid budget (120 weight a minute, ``03`` §6.1) is two thirds of
        everything we have.

        Not in ``MarketDataAdapter``: the protocol has one method per
        capability, and this venue has one endpoint for four of them. Exposed
        so the collector can spend once and say which capability it was
        exercising.
        """
        self._capabilities.require(capability)
        payload = await self._post(
            cost_of_call(HyperliquidCall.META_AND_ASSET_CTXS),
            {"type": request_type_of_call(HyperliquidCall.META_AND_ASSET_CTXS)},
            admission=admission,
        )
        observed = self._now()
        return norm.normalize_meta_and_asset_ctxs(
            payload,
            symbols=self._symbols,
            ingest_ts=observed,
            observed_ts=observed,
            unknown=self._unknown_symbols,
        )

    async def fetch_mark_prices(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """Mark, oracle (index) and **premium**, from ``metaAndAssetCtxs``.

        ``premium`` is a real observation on this venue, which is the one place
        the two adapters' outputs differ in kind: ``04`` §2's Binance
        ``premiumIndex`` publishes no premium at all and that adapter writes
        ``None`` rather than synthesising ``mark − index``.

        ``symbols`` is refused when it is not ``None``: this endpoint has no
        per-symbol form (``04`` §3) and asking for a subset would still cost the
        whole 20, so narrowing belongs to the caller's own filter and not to a
        request parameter that does not exist.
        """
        _refuse_subset(symbols)
        return await self.fetch_asset_contexts(
            admission=admission, capability=Capability.MARK_PRICE
        )

    async def fetch_funding(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """Funding over Hyperliquid's native interval — **one hour**.

        Same endpoint and same single charge as :meth:`fetch_mark_prices`.
        ``funding_rate`` is the raw hourly value and ``funding_interval_h`` is
        1; the 8-hour figure is the contract's
        :attr:`~hlens_core.contracts.MarketRecord.funding_rate_8h` property,
        derived on read and never stored (``03`` §5, seam ①).

        ``next_funding_ts`` is ``None`` here — ``metaAndAssetCtxs`` does not
        carry one and rounding the observation instant up to the next hour
        would be a schedule, not an observation. It is filled from
        :meth:`fetch_predicted_fundings`, which publishes a real one.
        """
        _refuse_subset(symbols)
        return await self.fetch_asset_contexts(
            admission=admission, capability=Capability.FUNDING_RATE
        )

    async def fetch_open_interest(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """Open interest in the coin's own base units, whole market, one call.

        The mirror image of Binance, where ``openInterest`` requires a symbol
        and 180 coins cost 180 weight a minute (``04`` §1). It is also the
        column ``04`` §5 says **cannot be backfilled** on this venue at all —
        there is no history endpoint — which is why F8 shows "数据积累中" until
        30 days have actually been collected rather than extrapolating.
        """
        _refuse_subset(symbols)
        return await self.fetch_asset_contexts(
            admission=admission, capability=Capability.OPEN_INTEREST
        )

    async def fetch_ticker_24h(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """24 h notional volume, from the same call. ``chg24h_pct`` is ``None``:
        ``04`` §3's field list carries no previous-day price, and an unknown is
        not a zero."""
        _refuse_subset(symbols)
        return await self.fetch_asset_contexts(
            admission=admission, capability=Capability.TICKER_24H
        )

    # ------------------------------------------------------------------ #
    # Funding, forward and back
    # ------------------------------------------------------------------ #
    async def fetch_predicted_fundings(
        self, *, admission: AnyAdmission
    ) -> tuple[MarketRecord, ...]:
        """``predictedFundings`` → **this venue's** predictions only.

        ``04`` §3: the response carries several exchanges' predicted rates,
        Binance's included. Every entry that is not Hyperliquid's is skipped and
        counted on :attr:`other_venue_keys`. An adapter returning another
        venue's record would be one adapter claiming to observe two venues —
        well-formed, plausible, and not ours to make.

        Not in ``MarketDataAdapter``: A4's protocol has ``fetch_funding`` for
        the current rate and no slot for a prediction, and ``03`` §5 has no
        column that separates a predicted rate from a settled one — only
        ``source`` does. M1's collector calls nothing here. See the PR.
        """
        self._capabilities.require(Capability.FUNDING_RATE)
        payload = await self._post(
            cost_of_call(HyperliquidCall.PREDICTED_FUNDINGS),
            {"type": request_type_of_call(HyperliquidCall.PREDICTED_FUNDINGS)},
            admission=admission,
        )
        observed = self._now()
        return norm.normalize_predicted_fundings(
            payload,
            symbols=self._symbols,
            ingest_ts=observed,
            observed_ts=observed,
            unknown=self._unknown_symbols,
            other_venues=self._other_venue_keys,
        )

    async def fetch_funding_history(
        self,
        *,
        admission: AnyAdmission,
        symbol: str,
        start_ms: int,
        end_ms: int | None = None,
        rows: int | None = None,
        priority: LanePriority | None = None,
    ) -> tuple[MarketRecord, ...]:
        """``fundingHistory`` — settled hourly rates, back as far as it goes.

        ``start_ms`` is **required**, because the venue requires it (``04`` §3:
        ``coin, startTime, endTime``). A ranged response returns at most
        :data:`~.endpoints.MAX_ROWS_PER_RANGED_RESPONSE` elements, so a caller
        paginates by moving ``start_ms`` forward; ``rows`` says how many are
        expected so the per-row half of the weight is charged honestly, and an
        omitted ``rows`` is charged at the full 500 rather than at nothing.

        ``priority`` exists because this endpoint serves two tiers at the same
        price: F11's two-year backfill is opportunistic (the default; ``03``
        §6.1 sizes it at ~5.5 days under the transitional 20 weight/min hard
        cap, and at ~3 hours once the legacy collector retires), while a
        reconciliation would be resident. The caller says which.

        Not in ``MarketDataAdapter``: the protocol has ``fetch_funding`` for
        the current rate and no slot for its history, exactly as on Binance.
        """
        self._capabilities.require(Capability.FUNDING_RATE)
        mapping = self._symbols.to_venue(symbol)
        if mapping is None:
            raise KeyError(f"hyperliquid has no contract mapped to the unified symbol {symbol!r}")
        _check_rows(rows, "fundingHistory")

        body: dict[str, Any] = {
            "type": request_type_of_call(HyperliquidCall.FUNDING_HISTORY),
            "coin": mapping.venue_symbol,
            "startTime": start_ms,
        }
        if end_ms is not None:
            body["endTime"] = end_ms
        payload = await self._post(
            cost_of_call(HyperliquidCall.FUNDING_HISTORY, symbols=1, rows=rows, priority=priority),
            body,
            admission=admission,
        )
        return norm.normalize_funding_history(payload, symbol=symbol, ingest_ts=self._now())

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
        """``candleSnapshot`` → ``semantic='candle_close'`` backfill records.

        **M1 calls this nowhere** (F2: M1 collects no klines and builds no kline
        table). It exists because AGENTS §3.4 requires it of every adapter and
        because M2's backfill is its only caller, on the opportunistic tier.

        Two venue limits shape the signature, and ``04`` §13 第 3 行 exists
        because they were once read as one:

        * ``start_ms`` is **required** — the request is a time range
          (``req{coin, interval, startTime, endTime}``) and there is no ``limit``
          parameter to send. ``limit`` here is what the caller *expects* the
          range to return: it charges the per-60-candle half of the weight and
          is bounded by the 500-element response cap;
        * the endpoint retains only :data:`~.endpoints.MAX_CANDLE_HISTORY_ROWS`
          bars — about 3.5 days at 1m — which is a *depth* limit, why the
          capability is ``partial_history``, and why F8 cannot backfill a
          30-day 1-minute percentile on this venue at all (``04`` §5).
        """
        self._capabilities.require(Capability.KLINES)
        mapping = self._symbols.to_venue(symbol)
        if mapping is None:
            raise KeyError(f"hyperliquid has no contract mapped to the unified symbol {symbol!r}")
        if start_ms is None:
            raise ValueError(
                "04 §3: candleSnapshot takes req{coin, interval, startTime, endTime} and "
                "has no limit parameter, so a start instant is required"
            )
        _check_rows(limit, "candleSnapshot")

        request: dict[str, Any] = {
            "coin": mapping.venue_symbol,
            "interval": interval.value,
            "startTime": start_ms,
        }
        if end_ms is not None:
            request["endTime"] = end_ms
        payload = await self._post(
            cost_of_call(HyperliquidCall.CANDLE_SNAPSHOT, symbols=1, rows=limit),
            {"type": request_type_of_call(HyperliquidCall.CANDLE_SNAPSHOT), "req": request},
            admission=admission,
        )
        now = self._now()
        return norm.normalize_candle_snapshot(
            payload, symbol=symbol, interval=interval.value, ingest_ts=now, now_ms=now
        )

    # ------------------------------------------------------------------ #
    # Ratios — this venue publishes none
    # ------------------------------------------------------------------ #
    async def fetch_ls_ratio(
        self,
        *,
        admission: AnyAdmission,
        kind: LsRatioKind,
        symbol: str,
        limit: int | None = None,
    ) -> tuple[LsRatioPoint, ...]:
        """Always raises. Hyperliquid publishes no long/short or taker ratio.

        ``04`` §1 (多空比 / 主动买卖比 **仅 Binance**; F3: 该所不发布) and
        AGENTS §3.4 ("declare both as unsupported rather than deriving a
        substitute"). Nothing is spent and nothing is sent: the refusal comes
        out of the capability sheet, which
        :data:`~hlens_core.adapters.capabilities.
        VENUE_MUST_DECLARE_UNSUPPORTED` will not let this venue declare
        otherwise.

        There is no substitute here and none is reachable from here. A share
        computed from tracked wallets' positions would be a share of our
        sample; one inferred from open interest and price direction would be a
        model; one reconstructed from the ``trades`` channel would need M4's
        trade collection and would still be a different measurement wearing
        this one's name.
        """
        self._capabilities.require(
            Capability.TAKER_RATIO
            if kind is LsRatioKind.TAKER_LONG_SHORT
            else Capability.LONG_SHORT_RATIO
        )
        raise AssertionError("unreachable: the capability sheet refuses both ratio kinds")

    # ------------------------------------------------------------------ #
    # Streams
    # ------------------------------------------------------------------ #
    def stream_mark_prices(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> AsyncIterator[MarketRecord]:
        """``allMids`` — one subscription for the whole market.

        **What it pushes is the mid price, not the mark price** (``04`` §3:
        币→中间价). ``04`` §1 names this channel as the push source for the
        mark column and ``03`` §6.1 budgets exactly one subscription for it, so
        the architecture's wiring is followed and the basis is made visible
        instead: every row carries ``source='hyperliquid_ws_all_mids'``, and
        ``03`` §6's fast lane polls ``metaAndAssetCtxs`` for the venue's real
        ``markPx``. Raised in the PR's "Doc corrections".

        The connection itself is admitted through the WebSocket side of the
        ledger by the caller, using :meth:`stream_plan` — on this venue the
        seats are zero-sum with the legacy collector (``04`` §4).
        ``admission`` is here so that a handshake's 429 reaches the ledger with
        its body, which is the only thing that says which limiter fired.
        """
        if symbols is not None:
            raise ValueError(
                "allMids is the whole market in one subscription; there is no per-symbol "
                "form of it (04 §3)"
            )
        return self._stream_mark_prices(admission=admission)

    async def _stream_mark_prices(
        self, *, admission: AnyAdmission
    ) -> AsyncIterator[MarketRecord]:
        self._capabilities.require(Capability.MARK_PRICE_STREAM)
        async for payload in self._messages(WS_CHANNEL_ALL_MIDS, admission=admission):
            observed = self._now()
            for record in norm.normalize_ws_all_mids(
                payload,
                symbols=self._symbols,
                ingest_ts=observed,
                observed_ts=observed,
                unknown=self._unknown_symbols,
            ):
                yield record

    def stream_liquidations(self, *, admission: AnyAdmission) -> AsyncIterator[NormalizedRecord]:
        """Always raises — **immediately**, before anything is opened.

        ``04`` §8, measured twice: Hyperliquid's ``trades`` channel carries no
        liquidation field (the messages name both counterparties and mark
        nothing as forced), and forced fills appear only on the per-wallet
        ``userFills`` endpoints, which are seam ⑤ and M5. The hub machine's
        existing Hyperliquid liquidation dataset is a derivative of tracked
        wallets' fills with 1.6–8 % historical capture and may be a reference
        for large-wallet events, **never an exchange-wide total**.

        So there is no lower bound to derive here. Binance's stream is a lower
        bound because the venue throttles a real feed; a sample of some wallets
        is not a floor on the venue — it is a floor on our sample, and F12
        would print it beside a venue's name. ``01`` §4.9's ban on a cross-venue
        "whole market" total is the same rule seen from the page.

        Not an ``async def``: a caller that awaited the first item before
        finding out would have already opened a connection on a shared,
        seat-limited socket budget.
        """
        raise UnsupportedCapability(
            "hyperliquid publishes no exchange-wide liquidation stream (04 §8); the "
            "per-wallet forced fills that exist are seam ⑤ and M5, and no lower bound "
            "is derived from wallet sampling"
        )

    async def _messages(self, channel: str, *, admission: AnyAdmission) -> AsyncIterator[Any]:
        """Connect, subscribe, and yield the payload of each frame on ``channel``.

        Frames from other channels — the subscription acknowledgement, a pong —
        are skipped rather than parsed: one socket carries every channel on this
        venue, so "is this mine" is a question every reader has to ask.

        A handshake that comes back with an HTTP status is reported to the
        ledger before it is re-raised, with whatever body it carried: ``04`` §3
        says an nginx HTML page means the **connection-rate** limiter, which
        needs fewer connections rather than less weight — and a reconnect loop
        that swallowed it would do the opposite.
        """
        url = self._endpoints.ws_url()
        try:
            connection = self._connector(url)
        except Exception as error:  # pragma: no cover - connector-specific
            self._observe_handshake(error, admission=admission)
            raise
        try:
            async with connection as socket:
                await socket.send(json.dumps(subscribe_frame(channel)))
                async for message in socket:
                    payload = _frame_payload(message, channel=channel)
                    if payload is not None:
                        yield payload
        except Exception as error:
            self._observe_handshake(error, admission=admission)
            raise

    def _observe_handshake(self, error: BaseException, *, admission: AnyAdmission) -> None:
        """Report a handshake status code, if the failure carries one.

        Duck-typed on purpose: ``websockets`` raises its own exception class and
        this module must stay usable with a replay connector that raises
        something else. Reported against the venue's weight bucket because the
        ledger's halt is venue-wide, and the body travels with it because it is
        the only evidence of which limiter fired.
        """
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if not isinstance(status, int):
            return
        body = getattr(response, "body", None)
        if isinstance(body, bytes | str):
            body = body[:_MAX_BODY_CHARS]
        else:
            body = None
        admission.observe(BUCKET_INFO_WEIGHT, status=status, body=body)


def _refuse_subset(symbols: Sequence[str] | None) -> None:
    """``metaAndAssetCtxs`` has no per-symbol form (``04`` §3).

    Accepting a subset and filtering locally would be worse than refusing: the
    call costs the same 20 either way, and a caller that believed it was
    spending less would size a lane on a number that is not true.
    """
    if symbols is not None:
        raise ValueError(
            "04 §3: metaAndAssetCtxs answers for the whole market in one W=20 call and "
            "has no per-symbol form; pass symbols=None and filter the records"
        )


def _check_rows(rows: int | None, request_type: str) -> None:
    """A ranged Hyperliquid response returns at most 500 elements (``04`` §3).

    Asking for more is not an error the venue reports — it simply returns 500 —
    so a caller that asked for 2000 would silently lose three quarters of a
    backfill window and be charged for one page. Refusing here is how the
    pagination stays the caller's explicit loop.
    """
    if rows is None:
        return
    if rows < 1:
        raise ValueError(f"{request_type} asks for at least one row; got {rows}")
    if rows > MAX_ROWS_PER_RANGED_RESPONSE:
        raise ValueError(
            f"04 §3: a ranged {request_type} response returns at most "
            f"{MAX_ROWS_PER_RANGED_RESPONSE} elements (and candleSnapshot retains only "
            f"{MAX_CANDLE_HISTORY_ROWS} bars in total); got {rows}. Paginate by moving "
            "startTime forward"
        )


def _retry_after(headers: httpx.Headers) -> float | None:
    """``Retry-After`` in seconds, or ``None``.

    Hyperliquid documents no rate-limit headers at all (``04`` §3), so this
    will usually be ``None`` and the ledger's own backoff is what runs. It is
    read anyway because a proxy in front of the venue may add one, and a real
    number beats a default.
    """
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _frame_payload(message: WsMessage, *, channel: str) -> Any:
    """One WebSocket frame → the payload for ``channel``, or ``None`` to skip.

    ``04`` §3's frames are ``{"channel": …, "data": …}``. Every channel shares
    one socket on this venue, so anything that is not ours — the
    ``subscriptionResponse`` acknowledgement, another subscription's data — is
    skipped rather than parsed into a record.
    """
    decoded = json.loads(message)
    if not isinstance(decoded, dict):
        return None
    if decoded.get("channel") != channel:
        return None
    return decoded.get("data")


def _satisfies_the_protocol(
    adapter: HyperliquidMarketDataAdapter,
) -> MarketDataAdapter:
    """Static proof that seam ②'s protocol is satisfied.

    mypy checks this assignment with the production code (``[tool.mypy] files``
    includes ``packages``), which is stronger than a runtime ``isinstance``
    against a runtime-checkable protocol: that one compares method *names* and
    would pass with the wrong signatures. It is also what proves that the two
    venues really are interchangeable behind seam ② even though four of this
    one's thirteen capabilities refuse.
    """
    return adapter
