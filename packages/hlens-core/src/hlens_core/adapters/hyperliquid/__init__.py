"""Hyperliquid perpetuals — M1-A step ⑥ (``03`` §16), the last of the six.

Five files, split the same way the Binance adapter's are, because the same
things need to be checkable independently:

``endpoints``
    where Hyperliquid is (read from ``config/venues.yaml``) and what we ask it
    for. One host, one WebSocket address, and one path: every documented read
    is a **POST** to ``/info`` with a ``type`` field, which is why the request
    type — not a URL — is what identifies a call everywhere else in this
    package.
``symbols``
    ``kPEPE`` ↔ ``PEPE``, both ways, carrying the quantity multiplier and the
    venue's native funding interval, which is **one hour for every symbol**
    (AGENTS §3.5, ``04`` §3).
``capabilities``
    the thirteen declarations. Four of them say no: ``long_short_ratio`` and
    ``taker_ratio`` because this venue does not publish them, the liquidation
    stream because it has no public exchange-wide feed, and — with Binance —
    the three M4 entries. ``klines`` is ``partial_history``, the only supported
    capability on either venue that is not ``full``.
``costs``
    the weight table and the single bucket it charges. Seam ③: nothing here
    imports ``ratelimit``; the ledger arrives as an
    :class:`~hlens_core.adapters.admission.Admission`.
``normalize`` / ``adapter``
    Hyperliquid's JSON → seam ①'s contracts, and the transport that fetches it.

**Market data only.** Wallet data has its own protocol from M5 (seam ⑤) and
never appears here — which matters more on this venue than on Binance, because
M5's wallet endpoints are Hyperliquid's and sit on the same ``/info`` path.
There is no wallet method, no wallet capability and no wallet request type
anywhere in this package.

Every network-facing function has an offline test against a fixture under
``tests/fixtures/hyperliquid/``. Those fixtures are tagged ``source:
documented``: AGENTS §3.3 requires recordings to be made **from the production
host's egress** (task ``M1-G``), this development machine shares that egress
with a still-running collector **of this same venue**, and this step sent no
request of any kind. The field names come from ``docs/04-DATA-SOURCES.md`` §3;
``M1-G`` overwrites them with real recordings and retags them
``live-recorded``.
"""

from __future__ import annotations

from .adapter import (
    AdmissionDenied,
    HyperliquidApiError,
    HyperliquidMarketDataAdapter,
    MillisClock,
    WsConnection,
    WsConnector,
    system_clock_ms,
    websockets_connector,
)
from .capabilities import HYPERLIQUID_CAPABILITIES, hyperliquid_capabilities
from .costs import (
    BUCKET_INFO_WEIGHT,
    CANDLE_ROWS_PER_WEIGHT,
    HISTORY_ROWS_PER_WEIGHT,
    INFO_REQUEST_WEIGHT,
    HyperliquidCall,
    candle_snapshot_weight,
    cost_of,
    cost_of_call,
    funding_history_weight,
    request_type_of_call,
    stream_plan_for,
)
from .endpoints import (
    INFO_PATH,
    MAX_CANDLE_HISTORY_ROWS,
    MAX_ROWS_PER_RANGED_RESPONSE,
    WS_CHANNEL_ALL_MIDS,
    WS_GROUP_INFO,
    EndpointError,
    HyperliquidEndpoints,
    subscribe_frame,
)
from .normalize import (
    PREDICTED_FUNDING_VENUE_KEY,
    SOURCE_CANDLE_SNAPSHOT,
    SOURCE_FUNDING_HISTORY,
    SOURCE_META,
    SOURCE_META_AND_ASSET_CTXS,
    SOURCE_PREDICTED_FUNDINGS,
    SOURCE_WS_ALL_MIDS,
    AssetMeta,
    asset_metadata,
    normalize_candle_snapshot,
    normalize_funding_history,
    normalize_meta,
    normalize_meta_and_asset_ctxs,
    normalize_predicted_fundings,
    normalize_ws_all_mids,
)
from .symbols import (
    FUNDING_INTERVAL_H,
    split_multiplier,
    symbol_table_of,
    unified_symbol,
)

__all__ = [
    "BUCKET_INFO_WEIGHT",
    "CANDLE_ROWS_PER_WEIGHT",
    "FUNDING_INTERVAL_H",
    "HISTORY_ROWS_PER_WEIGHT",
    "HYPERLIQUID_CAPABILITIES",
    "INFO_PATH",
    "INFO_REQUEST_WEIGHT",
    "MAX_CANDLE_HISTORY_ROWS",
    "MAX_ROWS_PER_RANGED_RESPONSE",
    "PREDICTED_FUNDING_VENUE_KEY",
    "SOURCE_CANDLE_SNAPSHOT",
    "SOURCE_FUNDING_HISTORY",
    "SOURCE_META",
    "SOURCE_META_AND_ASSET_CTXS",
    "SOURCE_PREDICTED_FUNDINGS",
    "SOURCE_WS_ALL_MIDS",
    "WS_CHANNEL_ALL_MIDS",
    "WS_GROUP_INFO",
    "AdmissionDenied",
    "AssetMeta",
    "EndpointError",
    "HyperliquidApiError",
    "HyperliquidCall",
    "HyperliquidEndpoints",
    "HyperliquidMarketDataAdapter",
    "MillisClock",
    "WsConnection",
    "WsConnector",
    "asset_metadata",
    "candle_snapshot_weight",
    "cost_of",
    "cost_of_call",
    "funding_history_weight",
    "hyperliquid_capabilities",
    "normalize_candle_snapshot",
    "normalize_funding_history",
    "normalize_meta",
    "normalize_meta_and_asset_ctxs",
    "normalize_predicted_fundings",
    "normalize_ws_all_mids",
    "request_type_of_call",
    "split_multiplier",
    "stream_plan_for",
    "subscribe_frame",
    "symbol_table_of",
    "system_clock_ms",
    "unified_symbol",
    "websockets_connector",
]
