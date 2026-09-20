"""Binance's weight table — the whole of it, in one file, owned by the adapter.

Seam ③ forbids ``adapters`` from importing ``ratelimit``, and
:mod:`hlens_core.adapters.admission` explains why the way out is dependency
inversion rather than a new edge: the adapter is the only thing that knows what
a call costs, the ledger is the only thing that knows whether we may spend it,
and they meet through a :class:`~hlens_core.adapters.admission.CallCost` the
caller carries from one to the other. Nothing here imports ``ratelimit``,
nothing here reads ``config/venues.yaml``'s budget, and nothing here decides
whether a call may go out.

Three buckets, and they are never the same account
--------------------------------------------------
``04`` §2/§4, spelled exactly as ``config/venues.yaml`` names them:

* ``binance:fapi_weight`` — ``REQUEST_WEIGHT``, what every ``/fapi/*`` call
  charges. 2400/min per IP officially.
* ``binance:futures_data`` — the three ``/futures/data/*`` ratio endpoints.
  They charge **no weight at all** (no ``X-MBX-USED-WEIGHT`` header) and are
  metered in **requests** against their own limit. Mixing them into the weight
  bucket would hide the tightest bucket in the system (54 of 80 per minute,
  ``03`` §6) behind a weight bucket that is a quarter full.
* ``binance:funding_rate`` — ``fundingRate`` and ``fundingInfo``, a third
  request bucket. ``04`` §11 第 10 项 lists "is it the same counter as
  ``futures_data``?" as unverified; modelling them as separate accounts is why
  ``config/venues.yaml`` presses this bucket's opportunistic cap down to 5.

The ladder that is charged on ``limit``
---------------------------------------
Only klines have one (``04`` §2): ``limit`` < 100 → 1, < 500 → 2, ≤ 1000 → 5,
> 1000 → 10. An omitted ``limit`` is **not** a free call — Binance's own default
is 500, so it charges 5, and :data:`~.endpoints.DEFAULT_KLINE_LIMIT` says so
rather than leaving the caller to find out from a 429.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from hlens_core.adapters.admission import CallCost, LanePriority
from hlens_core.adapters.base import StreamPlan
from hlens_core.adapters.capabilities import Capability, UnsupportedCapability
from hlens_core.contracts import LsRatioKind

from .endpoints import (
    DEFAULT_KLINE_LIMIT,
    REST_EXCHANGE_INFO,
    REST_FUNDING_INFO,
    REST_FUNDING_RATE,
    REST_GLOBAL_LONG_SHORT_ACCOUNT_RATIO,
    REST_KLINES,
    REST_OPEN_INTEREST,
    REST_PREMIUM_INDEX,
    REST_TAKER_LONG_SHORT_RATIO,
    REST_TICKER_24H,
    REST_TOP_LONG_SHORT_POSITION_RATIO,
    WS_GROUP_MARKET,
)

__all__ = [
    "BUCKET_FAPI_WEIGHT",
    "BUCKET_FUNDING_RATE",
    "BUCKET_FUTURES_DATA",
    "LANE_BACKFILL",
    "LANE_FAST_30S",
    "LANE_META",
    "LANE_RATIOS_10MIN",
    "LANE_SLOW_60S",
    "BinanceCall",
    "cost_of_call",
    "kline_weight",
    "path_of_call",
    "path_of_ls_ratio_kind",
    "stream_plan_for",
]

BUCKET_FAPI_WEIGHT: Final = "binance:fapi_weight"
BUCKET_FUTURES_DATA: Final = "binance:futures_data"
BUCKET_FUNDING_RATE: Final = "binance:funding_rate"

#: Paced lane names, one per row of ``03`` §6's lane table. They exist so the
#: ledger's burst shaper can spread 540 ratio requests across ten minutes
#: instead of firing them in the window's first second.
LANE_FAST_30S: Final = "binance_fast_30s"
LANE_SLOW_60S: Final = "binance_slow_60s"
LANE_RATIOS_10MIN: Final = "binance_ratios_10min"
LANE_META: Final = "binance_meta"
LANE_BACKFILL: Final = "binance_backfill"


class BinanceCall(StrEnum):
    """One row of the weight table each.

    Finer-grained than :class:`~hlens_core.adapters.capabilities.Capability` on
    purpose, because two of Binance's capabilities need more than one endpoint:
    ``instruments`` is ``exchangeInfo`` **plus** ``fundingInfo``, and those two
    charge different buckets; ``funding_rate`` is ``premiumIndex`` now and
    ``fundingRate`` for history. ``cost_of`` (the protocol method) answers for
    the primary call of a capability; :func:`cost_of_call` answers for any of
    them, which is what a method that makes two calls needs.
    """

    EXCHANGE_INFO = "exchange_info"
    FUNDING_INFO = "funding_info"
    PREMIUM_INDEX = "premium_index"
    OPEN_INTEREST = "open_interest"
    TICKER_24H = "ticker_24h"
    KLINES = "klines"
    FUNDING_RATE_HISTORY = "funding_rate_history"
    GLOBAL_LONG_SHORT_ACCOUNT_RATIO = "global_long_short_account_ratio"
    TOP_LONG_SHORT_POSITION_RATIO = "top_long_short_position_ratio"
    TAKER_LONG_SHORT_RATIO = "taker_long_short_ratio"


_PATHS: Final[dict[BinanceCall, str]] = {
    BinanceCall.EXCHANGE_INFO: REST_EXCHANGE_INFO,
    BinanceCall.FUNDING_INFO: REST_FUNDING_INFO,
    BinanceCall.PREMIUM_INDEX: REST_PREMIUM_INDEX,
    BinanceCall.OPEN_INTEREST: REST_OPEN_INTEREST,
    BinanceCall.TICKER_24H: REST_TICKER_24H,
    BinanceCall.KLINES: REST_KLINES,
    BinanceCall.FUNDING_RATE_HISTORY: REST_FUNDING_RATE,
    BinanceCall.GLOBAL_LONG_SHORT_ACCOUNT_RATIO: REST_GLOBAL_LONG_SHORT_ACCOUNT_RATIO,
    BinanceCall.TOP_LONG_SHORT_POSITION_RATIO: REST_TOP_LONG_SHORT_POSITION_RATIO,
    BinanceCall.TAKER_LONG_SHORT_RATIO: REST_TAKER_LONG_SHORT_RATIO,
}

#: Which call answers a capability first. A capability whose primary call is
#: absent here has no REST call at all (the two streams, and the three M4
#: entries, which never get this far because they are ``unsupported``).
_PRIMARY_CALL: Final[dict[Capability, BinanceCall]] = {
    Capability.INSTRUMENTS: BinanceCall.EXCHANGE_INFO,
    Capability.MARK_PRICE: BinanceCall.PREMIUM_INDEX,
    Capability.FUNDING_RATE: BinanceCall.PREMIUM_INDEX,
    Capability.OPEN_INTEREST: BinanceCall.OPEN_INTEREST,
    Capability.TICKER_24H: BinanceCall.TICKER_24H,
    Capability.KLINES: BinanceCall.KLINES,
    Capability.LONG_SHORT_RATIO: BinanceCall.GLOBAL_LONG_SHORT_ACCOUNT_RATIO,
    Capability.TAKER_RATIO: BinanceCall.TAKER_LONG_SHORT_RATIO,
}

#: Which endpoint each of seam ①'s three ratio kinds comes from. Three, not
#: four: ``03`` §5 records three kinds.
_RATIO_PATHS: Final[dict[LsRatioKind, str]] = {
    LsRatioKind.GLOBAL_LONG_SHORT_ACCOUNT: REST_GLOBAL_LONG_SHORT_ACCOUNT_RATIO,
    LsRatioKind.TOP_LONG_SHORT_POSITION: REST_TOP_LONG_SHORT_POSITION_RATIO,
    LsRatioKind.TAKER_LONG_SHORT: REST_TAKER_LONG_SHORT_RATIO,
}

_RATIO_CALLS: Final[dict[LsRatioKind, BinanceCall]] = {
    LsRatioKind.GLOBAL_LONG_SHORT_ACCOUNT: BinanceCall.GLOBAL_LONG_SHORT_ACCOUNT_RATIO,
    LsRatioKind.TOP_LONG_SHORT_POSITION: BinanceCall.TOP_LONG_SHORT_POSITION_RATIO,
    LsRatioKind.TAKER_LONG_SHORT: BinanceCall.TAKER_LONG_SHORT_RATIO,
}

#: The lane and tier each call runs in by default, from ``03`` §6's lane table.
#: ``cost_of_call`` takes an override because one endpoint genuinely serves two
#: tiers: ``fundingRate`` is the 8-hour reconciliation (resident) *and* the
#: cold-start backfill (opportunistic), and the same weight buys either.
_LANES: Final[dict[BinanceCall, tuple[LanePriority, str]]] = {
    BinanceCall.PREMIUM_INDEX: (LanePriority.FAST_LANE, LANE_FAST_30S),
    BinanceCall.OPEN_INTEREST: (LanePriority.RESIDENT, LANE_SLOW_60S),
    BinanceCall.TICKER_24H: (LanePriority.RESIDENT, LANE_SLOW_60S),
    BinanceCall.EXCHANGE_INFO: (LanePriority.RESIDENT, LANE_META),
    BinanceCall.FUNDING_INFO: (LanePriority.RESIDENT, LANE_META),
    BinanceCall.FUNDING_RATE_HISTORY: (LanePriority.RESIDENT, LANE_META),
    BinanceCall.GLOBAL_LONG_SHORT_ACCOUNT_RATIO: (LanePriority.RESIDENT, LANE_RATIOS_10MIN),
    BinanceCall.TOP_LONG_SHORT_POSITION_RATIO: (LanePriority.RESIDENT, LANE_RATIOS_10MIN),
    BinanceCall.TAKER_LONG_SHORT_RATIO: (LanePriority.RESIDENT, LANE_RATIOS_10MIN),
    # 03 §6.1: "Binance 侧的 opportunistic 只有 K 线回补".
    BinanceCall.KLINES: (LanePriority.OPPORTUNISTIC, LANE_BACKFILL),
}

#: Calls that take at most one ``symbol`` parameter and answer for the whole
#: market when it is omitted, with the two weights ``04`` §2 gives each.
_MARKET_WIDE_WEIGHT: Final[dict[BinanceCall, tuple[int, int]]] = {
    # call: (weight for one symbol, weight for the whole market)
    BinanceCall.PREMIUM_INDEX: (1, 10),
    BinanceCall.TICKER_24H: (1, 40),
}


def path_of_call(call: BinanceCall) -> str:
    return _PATHS[call]


def path_of_ls_ratio_kind(kind: LsRatioKind) -> str:
    return _RATIO_PATHS[kind]


def call_of_ls_ratio_kind(kind: LsRatioKind) -> BinanceCall:
    return _RATIO_CALLS[kind]


def kline_weight(limit: int | None) -> int:
    """``04`` §2's ladder: ``<100 → 1``, ``<500 → 2``, ``≤1000 → 5``, ``>1000 → 10``.

    ``None`` is charged as Binance's own default of 500, i.e. 5 — an omitted
    parameter still spends.
    """
    rows = DEFAULT_KLINE_LIMIT if limit is None else limit
    if rows < 1:
        raise ValueError(f"a kline request asks for at least one row; got {rows}")
    if rows < 100:
        return 1
    if rows < 500:
        return 2
    if rows <= 1000:
        return 5
    return 10


def cost_of_call(
    call: BinanceCall,
    *,
    symbols: int | None = None,
    rows: int | None = None,
    priority: LanePriority | None = None,
) -> CallCost:
    """What **one** call costs, against which bucket, in which lane.

    ``symbols`` is how many symbols this one request asks about, exactly as
    :meth:`~hlens_core.adapters.base.MarketDataAdapter.cost_of` defines it:
    ``None`` means the market-wide form of the endpoint, which is a different
    price. Binance's REST endpoints take **at most one** ``symbol``, so a count
    above 1 is refused rather than silently multiplied — several symbols are
    several calls, each with its own grant and its own settle, which is what
    makes 180 coins cost 180 weight a minute instead of one grant of 180 that
    the ledger can neither pace nor partially deny.
    """
    lane_priority, lane = _LANES[call]
    if priority is not None:
        lane_priority = priority
    path = _PATHS[call]

    if symbols is not None and symbols < 1:
        raise ValueError(f"a call asks about at least one symbol; got {symbols}")

    if call in _MARKET_WIDE_WEIGHT:
        one, whole_market = _MARKET_WIDE_WEIGHT[call]
        if symbols is None:
            weight, shape = whole_market, "whole market"
        elif symbols == 1:
            weight, shape = one, "one symbol"
        else:
            raise ValueError(
                f"{path} takes at most one symbol; ask for the market-wide form "
                f"(symbols=None) or cost each of the {symbols} calls separately"
            )
        return CallCost(
            bucket=BUCKET_FAPI_WEIGHT,
            weight=weight,
            priority=lane_priority,
            lane=lane,
            endpoint=f"{path} ({shape}, 04 §2: W={weight})",
        )

    if call is BinanceCall.OPEN_INTEREST:
        if symbols is None or symbols != 1:
            raise ValueError(
                f"{path} requires exactly one symbol (04 §2: 'symbol 必填'); "
                f"got symbols={symbols}"
            )
        return CallCost(
            bucket=BUCKET_FAPI_WEIGHT,
            weight=1,
            priority=lane_priority,
            lane=lane,
            endpoint=f"{path} (one symbol, 04 §2: W=1)",
        )

    if call is BinanceCall.EXCHANGE_INFO:
        return CallCost(
            bucket=BUCKET_FAPI_WEIGHT,
            weight=1,
            priority=lane_priority,
            lane=lane,
            endpoint=f"{path} (04 §2: W=1)",
        )

    if call is BinanceCall.KLINES:
        weight = kline_weight(rows)
        return CallCost(
            bucket=BUCKET_FAPI_WEIGHT,
            weight=weight,
            priority=lane_priority,
            lane=lane,
            endpoint=f"{path} (limit={rows or DEFAULT_KLINE_LIMIT}, 04 §2 ladder: W={weight})",
        )

    if call in (BinanceCall.FUNDING_INFO, BinanceCall.FUNDING_RATE_HISTORY):
        return CallCost(
            bucket=BUCKET_FUNDING_RATE,
            weight=1,
            priority=lane_priority,
            lane=lane,
            endpoint=f"{path} (own request bucket, 04 §2: 500 req/5min)",
        )

    # The three /futures/data/* ratios: no weight, one request each.
    return CallCost(
        bucket=BUCKET_FUTURES_DATA,
        weight=1,
        priority=lane_priority,
        lane=lane,
        endpoint=f"{path} (no weight; own request bucket, 04 §2: 1000 req/5min)",
    )


def cost_of(
    capability: Capability,
    *,
    symbols: int | None = None,
    rows: int | None = None,
) -> CallCost:
    """The protocol's ``cost_of``: the primary call of ``capability``.

    Raises :class:`~hlens_core.adapters.capabilities.UnsupportedCapability` for
    a capability with no REST call rather than returning a zero cost — "it is
    free" and "it does not exist" are different answers and only one of them
    means do not call.
    """
    call = _PRIMARY_CALL.get(capability)
    if call is None:
        raise UnsupportedCapability(
            f"binance has no REST call for {capability.value}"
            + (
                "; it is a stream — ask stream_plan()"
                if capability
                in (Capability.MARK_PRICE_STREAM, Capability.LIQUIDATION_STREAM)
                else ""
            )
        )
    return cost_of_call(call, symbols=symbols, rows=rows)


def stream_plan_for(capability: Capability, *, symbols: int | None = None) -> StreamPlan:
    """What the stream for ``capability`` asks of the WebSocket ledger.

    Both M1/M2 streams live on the ``/market`` group, which is the whole point
    of naming the group: ``04`` §2's 2026-04-23 split put ``@depth`` on
    ``/public``, and the two can never share a connection.
    """
    if capability is Capability.MARK_PRICE_STREAM:
        # `!markPrice@arr@1s` is one stream for the entire market; per-symbol
        # subscription is N streams and only exists as a fallback.
        return StreamPlan(
            capability=capability,
            group=WS_GROUP_MARKET,
            streams=1 if symbols is None else symbols,
            priority=LanePriority.FAST_LANE,
        )
    if capability is Capability.LIQUIDATION_STREAM:
        if symbols is not None:
            raise ValueError(
                "!forceOrder@arr is the whole market in one stream; there is no "
                "per-symbol form worth opening (04 §2)"
            )
        return StreamPlan(
            capability=capability,
            group=WS_GROUP_MARKET,
            streams=1,
            priority=LanePriority.RESIDENT,
        )
    raise UnsupportedCapability(f"binance publishes no stream for {capability.value}")
