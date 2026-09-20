"""Seam ② — the market-data adapter protocol.

``03`` §4: an adapter is "一个所怎么访问、字段怎么归一、声明了什么能力", and it
"返回契约对象（不碰库）". This module is that sentence as a type:

* a :class:`typing.Protocol`, not a base class. Nothing in this repository ever
  has to *be* an adapter to be used as one, so a test double, a replay adapter
  over recorded fixtures (M1-G) and a real venue client are interchangeable
  without an inheritance ceremony — and an adapter cannot inherit behaviour
  that quietly differs from the venue's;
* every method returns **contract objects** (seam ①), never the venue's JSON.
  Normalization is the adapter's whole job; a caller that receives a ``dict``
  would have to know the venue, which is the coupling seam ② removes;
* every method charges its own call through the :class:`~hlens_core.adapters.
  admission.Admission` it is handed, using the weights it publishes via
  :meth:`MarketDataAdapter.cost_of`. Read
  :mod:`hlens_core.adapters.admission` for why the ledger is injected
  structurally rather than imported.

Scope, deliberately
-------------------
The method set is AGENTS §3.4's list and nothing else: instrument discovery,
mark price, funding, open interest, the ratios the venue publishes, klines,
the 24 h ticker, and the mark-price and liquidation streams.

``trade_stream`` ``book_l2`` ``spot`` are **capabilities** here (seam ②
requires them declared now, ``unsupported`` on both venues) but they get no
methods: M4's trades and order book are a different shape — a whitelist of at
most 10 coins, their own tables, their own retention (seam ④) — and giving them
placeholder methods now would mean inventing their signatures before F19's
capacity gate has decided whether they exist at all. M4 adds a **second**
protocol for them, which is how it stays true that opening them changes no
adapter that does not implement them.

M1 has no liquidation contract
------------------------------
M1-A1 left the liquidation contract to M2 on purpose — its completeness
semantics are decided by the data, and the data is not collected yet. The
liquidation stream is still declared here, because AGENTS §3.4 requires it and
because its *capability* declaration is what F12's "下界" label hangs on. Its
element type is therefore :class:`NormalizedRecord`, the five attributes seam ①
requires of every record that crosses a boundary, and nothing more: no invented
columns, no raw JSON. M2 narrows this one annotation to the contract it adds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from hlens_core.contracts import (
    InstrumentRecord,
    LsRatioKind,
    LsRatioPoint,
    MarketRecord,
    Venue,
)

from .admission import AnyAdmission, CallCost, LanePriority
from .capabilities import Capability, CapabilitySet
from .symbols import SymbolMap

__all__ = [
    "KlineInterval",
    "MarketDataAdapter",
    "NormalizedRecord",
    "StreamPlan",
]


class KlineInterval(StrEnum):
    """The three grids ``04`` §1 collects. Not the venue's spelling — the
    adapter maps it, the same as it maps a symbol."""

    M1 = "1m"
    H1 = "1h"
    D1 = "1d"


@runtime_checkable
class NormalizedRecord(Protocol):
    """Seam ①'s floor: what every record carries, whatever else it carries.

    ``03`` §2 seam ①: "每条记录必带 ``venue`` ``symbol`` ``ts`` ``ingest_ts``
    ``source``". Every contract model already satisfies this structurally —
    :class:`~hlens_core.contracts.MarketRecord` and the rest are assignable to
    it with no change — so it is a statement of the seam, not a second
    hierarchy beside it. Used where M1 has no contract yet (the liquidation
    stream) so that the seam still holds without M1 inventing the shape M2 owns.
    """

    venue: Venue
    symbol: str
    ts: int
    ingest_ts: int
    source: str


@dataclass(frozen=True, slots=True)
class StreamPlan:
    """What a stream will ask of the WebSocket ledger before it connects.

    The same principle as :class:`~hlens_core.adapters.admission.CallCost`, for
    the other scarcity: connections and subscriptions are metered per egress IP
    and the seats are zero-sum with the legacy collector (``04`` §4), so the
    caller has to ask the ledger before opening anything — and only the adapter
    knows how many streams a subscription costs and which endpoint group it
    belongs to. Binance's 2026-04-23 split makes ``group`` load-bearing:
    ``@markPrice`` and ``@forceOrder`` are both on ``market``, ``@depth`` is on
    ``public``, and the two can never share one connection (``04`` §2).
    """

    capability: Capability
    group: str
    """The adapter's own name for the connection group — ``"market"`` for
    Binance's ``/market``, ``"info"`` for Hyperliquid's single WebSocket. Never
    a URL: hosts live in ``config/venues.yaml``."""

    streams: int
    """How many streams / subscriptions this plan opens on that connection."""

    priority: LanePriority

    def __post_init__(self) -> None:
        if self.streams < 1:
            raise ValueError(f"a stream plan opens at least one stream; got {self.streams}")
        if "://" in self.group:
            raise ValueError("group is a name, not a URL; hosts live in config/venues.yaml")


class MarketDataAdapter(Protocol):
    """One venue's market data, normalized, with its capabilities declared.

    **Market data only.** Wallet data has its own protocol from M5 and never
    appears here — seam ⑤ in as many words: "行情适配器里不出现任何钱包方法或
    钱包能力". :mod:`hlens_core.wallet` shares no base class, no enum and no
    import with this module.

    Every ``fetch_*`` method:

    * raises :class:`~hlens_core.adapters.capabilities.UnsupportedCapability`
      when its capability is declared ``unsupported`` — before spending
      anything, because on a shared egress IP a pointless request is somebody
      else's 429;
    * asks ``admission`` for its allowance, using :meth:`cost_of`, and settles
      it afterwards with the status the venue returned;
    * returns contract objects, already normalized, with ``ingest_ts`` set from
      the moment of receipt.
    """

    @property
    def venue(self) -> Venue:
        """Which venue this adapter is. One adapter is never two venues."""
        ...

    @property
    def capabilities(self) -> CapabilitySet:
        """The three answers for every capability (seam ②). Complete by
        construction: a capability this venue does not publish is declared
        ``unsupported`` with a reason, never omitted."""
        ...

    @property
    def symbols(self) -> SymbolMap:
        """Unified name ↔ venue contract name, with the multiplier and the
        native funding interval (AGENTS §3.5)."""
        ...

    def cost_of(
        self,
        capability: Capability,
        *,
        symbols: int | None = None,
        rows: int | None = None,
    ) -> CallCost:
        """What one call for ``capability`` costs, and against which bucket.

        The whole venue weight table lives behind this method, which is why no
        caller ever holds a weight: adding a venue adds an adapter and changes
        no call site (seam ②).

        ``symbols`` is how many symbols the call asks about; ``None`` means the
        market-wide form of the endpoint, which is a different price — Binance
        ``premiumIndex`` charges 1 per symbol and 10 for all of them, and
        ``ticker/24hr`` charges 1 and 40 (``04`` §2). ``rows`` is how many
        historical rows are requested, which is what the paginated endpoints
        charge on: Binance klines step 1/2/5/10 by ``limit``, Hyperliquid's
        ``candleSnapshot`` adds 1 per 60 candles (``04`` §2, §3).

        Raises :class:`~hlens_core.adapters.capabilities.UnsupportedCapability`
        rather than returning a zero cost: "it is free" and "it does not exist"
        are different answers, and only one of them means do not call.
        """
        ...

    def stream_plan(self, capability: Capability, *, symbols: int | None = None) -> StreamPlan:
        """What the stream for ``capability`` will ask of the WebSocket ledger."""
        ...

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    async def fetch_instruments(
        self, *, admission: AnyAdmission
    ) -> tuple[InstrumentRecord, ...]:
        """Every perpetual this venue lists, as of now (F1, F3).

        Binance: ``exchangeInfo`` filtered to ``contractType == PERPETUAL``,
        plus ``fundingInfo`` for the symbols whose interval is not the default.
        Hyperliquid: ``meta``. Status values that do not map onto
        :class:`~hlens_core.contracts.InstrumentStatus` are ``None``, not a
        guess.

        The universe itself — which coins **both** venues list — is the
        ``universe`` module's answer (F1), computed from these records. An
        adapter never returns a :class:`~hlens_core.contracts.CoinUniverseRecord`:
        a single venue cannot observe a cross-venue fact.
        """
        ...

    # ------------------------------------------------------------------ #
    # The fast lane (§5's fast column group)
    # ------------------------------------------------------------------ #
    async def fetch_mark_prices(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """Mark, index and premium. ``symbols=None`` asks for the whole market.

        Returns fast-lane records: ``obs_ts_fast`` set, slow-lane fields
        ``None``. A venue whose single endpoint answers several capabilities at
        once (Hyperliquid's ``metaAndAssetCtxs`` carries mark, funding **and**
        open interest) may serve this from one call and say so in its
        ``source`` tag; what it may not do is charge the ledger twice for one
        request, which is why :meth:`cost_of` is per call and not per field.
        """
        ...

    async def fetch_funding(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """Funding over the venue's **native** interval, plus the next
        settlement time.

        ``funding_rate`` is the raw value and ``funding_interval_h`` is the
        venue's own interval — Hyperliquid's hourly rate stays hourly. The
        8-hour figure is derived by the contract on read and is never stored
        (seam ①, §5).
        """
        ...

    # ------------------------------------------------------------------ #
    # The slow lane (§5's slow column group)
    # ------------------------------------------------------------------ #
    async def fetch_open_interest(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """Open interest, base and USD, as slow-lane records (``obs_ts_slow``).

        Coverage differs by venue and the cost model says so: Hyperliquid
        answers for the whole market in one call, Binance's ``openInterest``
        requires a symbol and therefore grows linearly with the coin count
        (``04`` §1).
        """
        ...

    async def fetch_ticker_24h(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """Rolling 24 h change and quote volume, as slow-lane records."""
        ...

    # ------------------------------------------------------------------ #
    # Ratios — only where the venue publishes them
    # ------------------------------------------------------------------ #
    async def fetch_ls_ratio(
        self,
        *,
        admission: AnyAdmission,
        kind: LsRatioKind,
        symbol: str,
        limit: int | None = None,
    ) -> tuple[LsRatioPoint, ...]:
        """One of seam ①'s three ratio kinds, for one coin.

        Binance only, and against the separate ``futures_data`` request bucket
        — it charges no weight and has its own limit, and mixing the two hides
        the tightest bucket in the system behind a weight bucket that is a
        quarter full (``04`` §4).

        ``limit`` returns the intermediate 5-minute points of the polling
        window: 决定 A8 polls every 10 minutes and takes both points back, which
        is what keeps the stored grid at 5 minutes and the freshness inside
        ``01`` §4.5's 16 minutes.

        Hyperliquid declares both ratio capabilities ``unsupported`` and this
        method raises there. It does **not** derive a substitute from wallet
        sampling — that would need seam ⑤ data and would be a different
        measurement wearing this one's name (AGENTS §3.4).
        """
        ...

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
        """Closed bars, normalized to ``semantic='candle_close'`` records.

        The backfill input, so the records carry ``grid_s`` and are written by
        §5's **third** upsert statement, never the live ones. Depth is a venue
        fact the capability declaration already states: Binance reaches full
        history, Hyperliquid keeps about 5000 bars — roughly 3.5 days at 1m —
        which is why its klines capability is ``partial_history`` and why F8
        shows "数据积累中" rather than extrapolating (``04`` §5).
        """
        ...

    # ------------------------------------------------------------------ #
    # Streams
    # ------------------------------------------------------------------ #
    def stream_mark_prices(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> AsyncIterator[MarketRecord]:
        """Pushed mark prices, as fast-lane records.

        The connection itself is admitted through the WebSocket side of the
        ledger by the caller, using :meth:`stream_plan`; ``admission`` is here
        so that a handshake's 429 or 418 is reported
        (:meth:`~hlens_core.adapters.admission.Admission.observe`). A 418 bans
        the whole egress IP, which is shared, so it can never be swallowed by a
        reconnect loop (``03`` §6.1).
        """
        ...

    def stream_liquidations(self, *, admission: AnyAdmission) -> AsyncIterator[NormalizedRecord]:
        """Pushed forced orders — **always a lower bound where it exists**.

        Binance throttles ``!forceOrder@arr`` to one order per symbol per
        second (``04`` §2), so what arrives is a floor and the capability says
        ``lower_bound``; the type of that declaration has no ``full`` to write.
        Hyperliquid declares this ``unsupported`` and this method raises there:
        its ``trades`` channel carries no liquidation field, and the per-wallet
        fills that do are M5 and seam ⑤ (``04`` §8).

        Yields :class:`NormalizedRecord` because M1 has no liquidation contract
        — see the module docstring. M2 narrows this annotation; it does not
        change the method.
        """
        ...

