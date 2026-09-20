"""Binance USDⓈ-M perpetuals — M1-A step ⑤ (``03`` §16).

Five files, split by what would otherwise be impossible to check independently:

``endpoints``
    where Binance is (read from ``config/venues.yaml``) and what we ask it for
    (paths, here). The 2026-04-23 WebSocket split is why the hosts are
    configuration: ``!markPrice@arr@1s`` and ``!forceOrder@arr`` both moved to
    the ``/market`` group and the legacy endpoint stopped pushing permanently.
``symbols``
    ``1000PEPEUSDT`` ↔ ``PEPE``, both ways, carrying the quantity multiplier
    and the venue's native funding interval (AGENTS §3.5).
``capabilities``
    the thirteen declarations — ``trade_stream`` / ``book_l2`` / ``spot``
    ``unsupported`` until M4, the liquidation stream ``lower_bound`` with
    ``throttled_source=True``, forever.
``costs``
    the whole weight table, and the three buckets it charges. Seam ③: nothing
    here imports ``ratelimit``; the ledger arrives as an
    :class:`~hlens_core.adapters.admission.Admission`.
``normalize`` / ``adapter``
    Binance's JSON → seam ①'s contracts, and the transport that fetches it.

**Market data only.** Wallet data has its own protocol from M5 (seam ⑤) and
never appears here.

Every network-facing function has an offline test against a fixture under
``tests/fixtures/binance/``. Those fixtures are tagged ``source: documented``:
AGENTS §3.3 requires recordings to be made **from the production host's
egress** (task ``M1-G``), the development machine shares that egress with a
still-running legacy collector, and this step sent no request of any kind. The
field names come from ``docs/04-DATA-SOURCES.md`` §2; ``M1-G`` overwrites them
with real recordings and retags them ``live-recorded``.
"""

from __future__ import annotations

from .adapter import (
    AdmissionDenied,
    BinanceApiError,
    BinanceMarketDataAdapter,
    MillisClock,
    WsConnection,
    WsConnector,
    system_clock_ms,
    websockets_connector,
)
from .capabilities import BINANCE_CAPABILITIES, binance_capabilities
from .costs import (
    BUCKET_FAPI_WEIGHT,
    BUCKET_FUNDING_RATE,
    BUCKET_FUTURES_DATA,
    BinanceCall,
    call_of_ls_ratio_kind,
    cost_of,
    cost_of_call,
    kline_weight,
    path_of_call,
    path_of_ls_ratio_kind,
    stream_plan_for,
)
from .endpoints import (
    WS_FORCE_ORDER_ALL,
    WS_GROUP_MARKET,
    WS_GROUP_PUBLIC,
    WS_MARK_PRICE_ALL,
    BinanceEndpoints,
    EndpointError,
    ws_mark_price_stream,
)
from .normalize import (
    LS_RATIO_PERIOD,
    LS_RATIO_PERIOD_S,
    ForcedOrderObservation,
    normalize_exchange_info,
    normalize_funding_info,
    normalize_funding_rate_history,
    normalize_klines,
    normalize_ls_ratio,
    normalize_open_interest,
    normalize_premium_index,
    normalize_ticker_24h,
    normalize_ws_force_order,
    normalize_ws_mark_price,
)
from .symbols import (
    DEFAULT_FUNDING_INTERVAL_H,
    DEFAULT_QUOTE_ASSET,
    split_multiplier,
    symbol_table_of,
    unified_symbol,
)

__all__ = [
    "BINANCE_CAPABILITIES",
    "BUCKET_FAPI_WEIGHT",
    "BUCKET_FUNDING_RATE",
    "BUCKET_FUTURES_DATA",
    "DEFAULT_FUNDING_INTERVAL_H",
    "DEFAULT_QUOTE_ASSET",
    "LS_RATIO_PERIOD",
    "LS_RATIO_PERIOD_S",
    "WS_FORCE_ORDER_ALL",
    "WS_GROUP_MARKET",
    "WS_GROUP_PUBLIC",
    "WS_MARK_PRICE_ALL",
    "AdmissionDenied",
    "BinanceApiError",
    "BinanceCall",
    "BinanceEndpoints",
    "BinanceMarketDataAdapter",
    "EndpointError",
    "ForcedOrderObservation",
    "MillisClock",
    "WsConnection",
    "WsConnector",
    "binance_capabilities",
    "call_of_ls_ratio_kind",
    "cost_of",
    "cost_of_call",
    "kline_weight",
    "normalize_exchange_info",
    "normalize_funding_info",
    "normalize_funding_rate_history",
    "normalize_klines",
    "normalize_ls_ratio",
    "normalize_open_interest",
    "normalize_premium_index",
    "normalize_ticker_24h",
    "normalize_ws_force_order",
    "normalize_ws_mark_price",
    "path_of_call",
    "path_of_ls_ratio_kind",
    "split_multiplier",
    "stream_plan_for",
    "symbol_table_of",
    "system_clock_ms",
    "unified_symbol",
    "websockets_connector",
    "ws_mark_price_stream",
]
