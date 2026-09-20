"""Hyperliquid's weight table — the whole of it, in one file, owned by the adapter.

Seam ③ forbids ``adapters`` from importing ``ratelimit``, and
:mod:`hlens_core.adapters.admission` explains why the way out is dependency
inversion rather than a new edge. Nothing here imports ``ratelimit``, nothing
here reads ``config/venues.yaml``'s budget, and nothing here decides whether a
call may go out: the adapter states what a call costs, the ledger states
whether it may be spent, and a :class:`~hlens_core.adapters.admission.CallCost`
carries the first to the second.

One bucket, and why that is not a simplification
------------------------------------------------
Everything here charges ``hyperliquid:info_weight`` — Hyperliquid has one
metered REST surface, so unlike Binance there is no second request bucket to
keep separate. What replaces that risk is a worse one, and it is the reason
every number below is written conservatively:

* **Hyperliquid's responses carry no rate-limit headers at all** (``04`` §3).
  Binance answers with ``X-MBX-USED-WEIGHT-1M`` and preflight calibrates the
  local ledger against it; here there is nothing to calibrate against, so the
  local count **is** the only account, and an under-stated weight is a
  miscount nobody will catch until a 429.
* **the egress is shared with a still-running Hyperliquid collector** (``03``
  §6.1). This is the one venue where the other consumer is definitely on the
  same exchange, and where its usage is a conservative placeholder rather than
  a measurement (``04`` §11 第 12 项).

The weights (``04`` §3)
-----------------------
* weight group 2 — ``l2Book``, ``allMids``, ``clearinghouseState`` and friends
  — the **cheapest** group, not the most expensive (``04`` §13 第 8 行 corrects
  the opposite reading). None of them is called here: ``l2Book`` is M4,
  ``clearinghouseState`` is seam ⑤, and ``allMids`` is reached over the
  WebSocket, which costs no ``/info`` weight at all;
* every other documented request: **20**. That is ``meta``,
  ``metaAndAssetCtxs``, ``predictedFundings``, ``fundingHistory`` and
  ``candleSnapshot``;
* **the historical ones are charged by the row**: 1 per 20 rows returned, and
  1 per **60** for ``candleSnapshot``. Not a flat price — a 500-row page and a
  20-row page are not the same call.

``04`` §3 can be read two ways on that last line: its table's weight column for
``fundingHistory`` gives only "每 20 条 +1", while its prose ("其余文档化请求一律
20；历史类每 20 条 +1") could be read as that *plus* the flat 20. The two differ
by a constant 20 per call, Hyperliquid sends no header that could settle it, and
guessing low on a shared egress is how somebody else gets a 429 — so it was
worth settling rather than choosing. **``03`` §6.1's own arithmetic settles
it**: F11's two-year funding backfill is sized there at "157,680 权重", and
157,680 is exactly ``(2 × 365 × 24 rows ÷ 20) × 180 coins`` with no per-call
constant at all (the flat-20 reading gives ~284,000). So the per-row figure is
the whole weight, and that is what :func:`funding_history_weight` returns.

**One consequence worth knowing before writing a backfill loop**: a full
500-row ``fundingHistory`` page costs 25, and the transitional opportunistic
hard cap on this bucket is **20 weight a minute** (``03`` §6.1, pressed below
the formula on purpose). A single full page therefore never fits, and the
ledger denies it outright rather than throttling it — the backfill has to page
at **≤ 400 rows**, which is what makes ``03`` §6.1's "157,680 ÷ 20 ≈ 5.5 天"
achievable rather than impossible. Reported in the PR.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Final

from hlens_core.adapters.admission import CallCost, LanePriority
from hlens_core.adapters.base import StreamPlan
from hlens_core.adapters.capabilities import Capability, UnsupportedCapability

from .endpoints import (
    INFO_PATH,
    MAX_ROWS_PER_RANGED_RESPONSE,
    TYPE_CANDLE_SNAPSHOT,
    TYPE_FUNDING_HISTORY,
    TYPE_META,
    TYPE_META_AND_ASSET_CTXS,
    TYPE_PREDICTED_FUNDINGS,
    WS_GROUP_INFO,
)

__all__ = [
    "BUCKET_INFO_WEIGHT",
    "CANDLE_ROWS_PER_WEIGHT",
    "HISTORY_ROWS_PER_WEIGHT",
    "INFO_REQUEST_WEIGHT",
    "LANE_BACKFILL",
    "LANE_FAST_30S",
    "LANE_META",
    "LANE_PREDICTED_5MIN",
    "HyperliquidCall",
    "candle_snapshot_weight",
    "cost_of",
    "cost_of_call",
    "funding_history_weight",
    "request_type_of_call",
    "stream_plan_for",
]

#: The one metered account, spelled exactly as ``config/venues.yaml`` names it.
BUCKET_INFO_WEIGHT: Final = "hyperliquid:info_weight"

#: ``04`` §3: "其余文档化请求一律 20".
INFO_REQUEST_WEIGHT: Final = 20

#: ``04`` §3: "历史类每 20 条 +1（``candleSnapshot`` 每 60 条 +1）".
HISTORY_ROWS_PER_WEIGHT: Final = 20
CANDLE_ROWS_PER_WEIGHT: Final = 60

#: Paced lane names, one per row of ``03`` §6's lane table that belongs to this
#: venue. They exist so the ledger's burst shaper can spread a lane's calls
#: across its window instead of firing them in its first second.
LANE_FAST_30S: Final = "hyperliquid_fast_30s"
LANE_META: Final = "hyperliquid_meta"
LANE_PREDICTED_5MIN: Final = "hyperliquid_predicted_5min"
LANE_BACKFILL: Final = "hyperliquid_backfill"


class HyperliquidCall(StrEnum):
    """One row of the weight table each — one ``type`` on ``/info``.

    Finer-grained than :class:`~hlens_core.adapters.capabilities.Capability`,
    and on this venue the grain runs the other way from Binance's: there, one
    capability needed two endpoints; here **one endpoint answers four
    capabilities**. ``metaAndAssetCtxs`` carries mark, funding, open interest
    and 24 h volume in a single W=20 response, which is why ``03`` §6's lane
    table budgets 40 weight a minute for the whole Hyperliquid fast lane (two
    calls at 20) and not four times that.
    """

    META = "meta"
    META_AND_ASSET_CTXS = "meta_and_asset_ctxs"
    PREDICTED_FUNDINGS = "predicted_fundings"
    FUNDING_HISTORY = "funding_history"
    CANDLE_SNAPSHOT = "candle_snapshot"


_REQUEST_TYPES: Final[dict[HyperliquidCall, str]] = {
    HyperliquidCall.META: TYPE_META,
    HyperliquidCall.META_AND_ASSET_CTXS: TYPE_META_AND_ASSET_CTXS,
    HyperliquidCall.PREDICTED_FUNDINGS: TYPE_PREDICTED_FUNDINGS,
    HyperliquidCall.FUNDING_HISTORY: TYPE_FUNDING_HISTORY,
    HyperliquidCall.CANDLE_SNAPSHOT: TYPE_CANDLE_SNAPSHOT,
}

#: Which call answers a capability first. A capability absent here has no REST
#: call at all — the mark-price stream, and the four this venue declares
#: ``unsupported``, which never get this far.
_PRIMARY_CALL: Final[dict[Capability, HyperliquidCall]] = {
    Capability.INSTRUMENTS: HyperliquidCall.META,
    Capability.MARK_PRICE: HyperliquidCall.META_AND_ASSET_CTXS,
    Capability.FUNDING_RATE: HyperliquidCall.META_AND_ASSET_CTXS,
    Capability.OPEN_INTEREST: HyperliquidCall.META_AND_ASSET_CTXS,
    Capability.TICKER_24H: HyperliquidCall.META_AND_ASSET_CTXS,
    Capability.KLINES: HyperliquidCall.CANDLE_SNAPSHOT,
}

#: The lane and tier each call runs in, from ``03`` §6's lane table and ``04``
#: §3's frequency column. ``cost_of_call`` takes an override for the same
#: reason Binance's does: ``fundingHistory`` is both the cold-start backfill
#: (opportunistic, 决定 A7) and, when a caller wants it, a resident
#: reconciliation, at the same price.
_LANES: Final[dict[HyperliquidCall, tuple[LanePriority, str]]] = {
    # 03 §6: "快道 30 s | HL metaAndAssetCtxs（W=20）| HL 权重 | 40".
    HyperliquidCall.META_AND_ASSET_CTXS: (LanePriority.FAST_LANE, LANE_FAST_30S),
    # 03 §6: "1 h / 8 h / 冷启动 | … HL meta(W=20) …".
    HyperliquidCall.META: (LanePriority.RESIDENT, LANE_META),
    # 04 §3's frequency column for predictedFundings: 5 min.
    HyperliquidCall.PREDICTED_FUNDINGS: (LanePriority.RESIDENT, LANE_PREDICTED_5MIN),
    # 04 §5: 冷启动. F11's two-year funding backfill is the big one — 03 §6.1
    # sizes it at ~5.5 days on the transitional 20 weight/min hard cap.
    HyperliquidCall.FUNDING_HISTORY: (LanePriority.OPPORTUNISTIC, LANE_BACKFILL),
    HyperliquidCall.CANDLE_SNAPSHOT: (LanePriority.OPPORTUNISTIC, LANE_BACKFILL),
}


def request_type_of_call(call: HyperliquidCall) -> str:
    """The ``type`` string this call puts in the POST body."""
    return _REQUEST_TYPES[call]


def _rows_weight(rows: int | None, *, per_weight: int) -> int:
    """The per-row half of a historical request's weight.

    ``rows`` is how many rows the caller is asking for. ``None`` is charged at
    the documented per-response cap of :data:`~.endpoints.
    MAX_ROWS_PER_RANGED_RESPONSE`, because an unbounded time range is a request
    for as many rows as the venue will give: an omitted bound is not a cheap
    call, and the ledger must not find that out afterwards.
    """
    asked = MAX_ROWS_PER_RANGED_RESPONSE if rows is None else rows
    if asked < 1:
        raise ValueError(f"a historical request asks for at least one row; got {asked}")
    return math.ceil(asked / per_weight)


def funding_history_weight(rows: int | None) -> int:
    """``04`` §3: **1 per 20 rows**, and that is the whole weight.

    Not the flat :data:`INFO_REQUEST_WEIGHT` and not it plus this — see the
    module docstring for why ``03`` §6.1's F11 figure settles a question ``04``
    §3 leaves open. A full 500-row page is 25, which is above the transitional
    opportunistic hard cap of 20, so a backfill pages at ≤ 400 rows.
    """
    return _rows_weight(rows, per_weight=HISTORY_ROWS_PER_WEIGHT)


def candle_snapshot_weight(rows: int | None) -> int:
    """``04`` §3: **1 per 60 candles** — a different divisor from every other
    historical endpoint, and the kind of detail that is only ever wrong in one
    direction. A full 500-candle page is 9."""
    return _rows_weight(rows, per_weight=CANDLE_ROWS_PER_WEIGHT)


def cost_of_call(
    call: HyperliquidCall,
    *,
    symbols: int | None = None,
    rows: int | None = None,
    priority: LanePriority | None = None,
) -> CallCost:
    """What **one** call costs, against which bucket, in which lane.

    ``symbols`` follows :meth:`~hlens_core.adapters.base.MarketDataAdapter.
    cost_of`: ``None`` means the market-wide form. On Hyperliquid every
    snapshot endpoint **is** market-wide and has no per-symbol form at all —
    ``meta``, ``metaAndAssetCtxs`` and ``predictedFundings`` take no arguments —
    so asking for a subset is refused rather than quietly charged as if it were
    cheaper. (This is the mirror image of Binance's ``openInterest``, which has
    only a per-symbol form. Neither venue has both, and pretending otherwise is
    how a market-wide price gets charged as a per-symbol one.)

    ``rows`` is the historical row count, and only the two paginated endpoints
    read it.
    """
    lane_priority, lane = _LANES[call]
    if priority is not None:
        lane_priority = priority
    request_type = _REQUEST_TYPES[call]

    if symbols is not None and symbols < 1:
        raise ValueError(f"a call asks about at least one symbol; got {symbols}")

    if call in (
        HyperliquidCall.META,
        HyperliquidCall.META_AND_ASSET_CTXS,
        HyperliquidCall.PREDICTED_FUNDINGS,
    ):
        if symbols is not None:
            raise ValueError(
                f"{request_type} has no per-symbol form: it answers for the whole "
                f"market in one W={INFO_REQUEST_WEIGHT} call (04 §3). Ask with "
                "symbols=None"
            )
        return CallCost(
            bucket=BUCKET_INFO_WEIGHT,
            weight=INFO_REQUEST_WEIGHT,
            priority=lane_priority,
            lane=lane,
            endpoint=(
                f"{INFO_PATH} (type={request_type}, whole market, "
                f"04 §3: W={INFO_REQUEST_WEIGHT})"
            ),
        )

    if symbols is not None and symbols != 1:
        raise ValueError(
            f"{request_type} asks about exactly one coin (04 §3); got symbols={symbols}"
        )

    if call is HyperliquidCall.FUNDING_HISTORY:
        weight = funding_history_weight(rows)
        detail = f"{HISTORY_ROWS_PER_WEIGHT} rows"
    else:
        weight = candle_snapshot_weight(rows)
        detail = f"{CANDLE_ROWS_PER_WEIGHT} candles"

    asked = MAX_ROWS_PER_RANGED_RESPONSE if rows is None else rows
    return CallCost(
        bucket=BUCKET_INFO_WEIGHT,
        weight=weight,
        priority=lane_priority,
        lane=lane,
        endpoint=(
            f"{INFO_PATH} (type={request_type}, rows={asked}, "
            f"04 §3: 1 per {detail} -> W={weight})"
        ),
    )


def cost_of(
    capability: Capability,
    *,
    symbols: int | None = None,
    rows: int | None = None,
) -> CallCost:
    """The protocol's ``cost_of``: the primary call of ``capability``.

    Raises :class:`~hlens_core.adapters.capabilities.UnsupportedCapability`
    rather than returning a zero cost — "it is free" and "it does not exist"
    are different answers and only one of them means do not call. On this venue
    the second answer is the common one: four of the thirteen capabilities have
    no endpoint at all and never will (``04`` §1, §8).
    """
    call = _PRIMARY_CALL.get(capability)
    if call is None:
        hint = ""
        if capability is Capability.MARK_PRICE_STREAM:
            hint = "; it is a stream — ask stream_plan()"
        elif capability in (Capability.LONG_SHORT_RATIO, Capability.TAKER_RATIO):
            hint = "; this venue does not publish it and no substitute is derived (04 §1)"
        elif capability is Capability.LIQUIDATION_STREAM:
            hint = "; this venue has no public exchange-wide liquidation feed (04 §8)"
        raise UnsupportedCapability(f"hyperliquid has no REST call for {capability.value}{hint}")
    return cost_of_call(call, symbols=symbols, rows=rows)


def stream_plan_for(capability: Capability, *, symbols: int | None = None) -> StreamPlan:
    """What the stream for ``capability`` asks of the WebSocket ledger.

    One group (``04`` §3: a single WebSocket address, every channel on it), and
    on this venue the scarce resources are counted per egress IP and **zero-sum
    with the legacy collector**: 10 connections, 1000 subscriptions and — the
    hard one — 10 distinct ``user`` seats (``04`` §4). M1 asks for exactly one
    subscription, which is what ``03`` §6.1's table budgets.
    """
    if capability is Capability.MARK_PRICE_STREAM:
        if symbols is not None:
            raise ValueError(
                "allMids is the whole market in one subscription; there is no "
                "per-symbol form of it (04 §3). A per-coin mark price would be "
                "activeAssetCtx{coin}, which 03 §6.1 does not budget for M1"
            )
        return StreamPlan(
            capability=capability,
            group=WS_GROUP_INFO,
            streams=1,
            priority=LanePriority.FAST_LANE,
        )
    if capability is Capability.LIQUIDATION_STREAM:
        raise UnsupportedCapability(
            "hyperliquid publishes no exchange-wide liquidation stream: the ws trades "
            "channel carries no liquidation field and forced fills are visible only per "
            "wallet, which is seam ⑤ and M5 (04 §8). No lower bound is derived from "
            "wallet sampling"
        )
    raise UnsupportedCapability(f"hyperliquid publishes no stream for {capability.value}")
