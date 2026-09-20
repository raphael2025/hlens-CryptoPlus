"""Binance's JSON → seam ①'s contracts. The whole of the adapter's real job.

Rules this module does not get to reinterpret
---------------------------------------------
* **``ts`` is the observation instant, never a bucket.** Binance's own
  timestamp is passed through untouched; ``date_trunc('minute', …)`` happens at
  the persistence boundary (M1-C / M1-E), and ``obs_ts_fast`` / ``obs_ts_slow``
  are filled from the same instant. Nothing here rounds anything.
* **Unknown is ``None``, never 0, and ``mult`` has no default of 1.** An
  unobserved open interest and an open interest of zero are different facts.
* **``Decimal`` from the venue's own string.** Never ``float`` — the contracts
  refuse it, because a price that has been through binary floating point has
  already lost the exchange's digits.
* **``valid_from`` / ``valid_to`` / ``in_from`` / ``in_to`` are not contract
  fields** and are not written here. They are assigned when a version is opened
  or closed, by the boundary that compares against the currently open row.
* **Funding rates stay on the venue's native interval.** ``funding_rate`` is
  Binance's raw value and ``funding_interval_h`` travels with it; the 8-hour
  figure is a property of the contract, computed on read and never stored.

Two fields Binance does not publish, and are therefore ``None``
---------------------------------------------------------------
``premium``
    ``04`` §1 lists ``premium`` as a ``market_1m`` column and ``04`` §2 lists
    ``premiumIndex``'s fields as ``markPrice, indexPrice, lastFundingRate,
    nextFundingTime`` — there is no premium among them, and the WebSocket frame
    has none either. The premium column is Hyperliquid's
    (``metaAndAssetCtxs.premium``). We do **not** write ``mark − index`` into
    it: that is a computed spread, which is ``compute``'s answer (F7's
    ``mark_spread_bps``), and manufacturing it here would make a derived number
    indistinguishable from an observed one.
``oi_usd``
    ``/fapi/v1/openInterest`` returns base-unit open interest and a timestamp,
    nothing else. The USD figure exists only on ``/futures/data/
    openInterestHist`` (``sumOpenInterestValue``), which is the 30-day,
    5-minute-grid backfill endpoint (``04`` §2, §5). Multiplying by a mark price
    observed by a *different* call at a *different* instant would be a
    computation wearing an observation's clothes.

``semantic`` on a record with no price at all
---------------------------------------------
Seam ① makes ``semantic`` mandatory on :class:`MarketRecord` and offers two
values, both of which are about a price: ``mark_price`` and ``candle_close``.
A slow-lane record (open interest, the 24 h ticker) and a settled funding rate
carry no price, so neither value is true of them — and ``03`` §5's column groups
show why nobody noticed: ``semantic`` is written only by the **backfill**
statement, not by either live statement. Live records here are tagged
``mark_price``, meaning "a live observation on the venue's mark-price basis",
and kline rows are tagged ``candle_close``, which is exactly what they are.

Both of those, and the two ``None`` columns above, are listed in the PR's
"Doc corrections".
"""

from __future__ import annotations

from collections.abc import MutableSequence, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from hlens_core.adapters.symbols import SymbolMap, SymbolMapping
from hlens_core.contracts import (
    InstrumentRecord,
    InstrumentStatus,
    LsRatioKind,
    LsRatioPoint,
    MarketRecord,
    Semantic,
    Venue,
)

from .symbols import DEFAULT_FUNDING_INTERVAL_H, split_multiplier

__all__ = [
    "KLINE_GRID_SECONDS",
    "LS_RATIO_PERIOD",
    "LS_RATIO_PERIOD_S",
    "SOURCE_EXCHANGE_INFO",
    "SOURCE_FUNDING_RATE",
    "SOURCE_KLINES",
    "SOURCE_OPEN_INTEREST",
    "SOURCE_PREMIUM_INDEX",
    "SOURCE_TICKER_24H",
    "SOURCE_WS_FORCE_ORDER",
    "SOURCE_WS_MARK_PRICE",
    "ForcedOrderObservation",
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
    "normalized_venue_symbols",
    "source_of_ls_ratio_kind",
]

# --------------------------------------------------------------------------- #
# `source` tags. Convention from contracts/base.py: `<venue>_<transport>_<endpoint>`.
# --------------------------------------------------------------------------- #
SOURCE_EXCHANGE_INFO: Final = "binance_rest_exchange_info"
SOURCE_PREMIUM_INDEX: Final = "binance_rest_premium_index"
SOURCE_OPEN_INTEREST: Final = "binance_rest_open_interest"
SOURCE_TICKER_24H: Final = "binance_rest_ticker_24h"
SOURCE_KLINES: Final = "binance_rest_klines"
SOURCE_FUNDING_RATE: Final = "binance_rest_funding_rate"
SOURCE_WS_MARK_PRICE: Final = "binance_ws_mark_price"
SOURCE_WS_FORCE_ORDER: Final = "binance_ws_force_order"

_SOURCE_BY_RATIO_KIND: Final[dict[LsRatioKind, str]] = {
    LsRatioKind.GLOBAL_LONG_SHORT_ACCOUNT: "binance_rest_global_long_short_account_ratio",
    LsRatioKind.TOP_LONG_SHORT_POSITION: "binance_rest_top_long_short_position_ratio",
    LsRatioKind.TAKER_LONG_SHORT: "binance_rest_taker_long_short_ratio",
}


def source_of_ls_ratio_kind(kind: LsRatioKind) -> str:
    return _SOURCE_BY_RATIO_KIND[kind]


#: 决定 A8 / ``03`` §6: poll every 10 minutes and take the window's 5-minute
#: points back with ``limit``. The protocol's ``fetch_ls_ratio`` has no
#: ``period`` parameter, which is exactly right — the grid is a decision, not a
#: caller's option, and ``03`` §5 sizes ``ls_ratio`` on this number.
LS_RATIO_PERIOD: Final = "5m"
LS_RATIO_PERIOD_S: Final = 300

#: ``grid_s`` for each kline interval, so a backfilled row can say which grid it
#: sits on and F8's ``n`` stays traceable.
KLINE_GRID_SECONDS: Final[dict[str, int]] = {"1m": 60, "1h": 3600, "1d": 86400}

#: Binance's own status vocabulary, mapped onto :class:`InstrumentStatus`.
#: Anything not in this table normalizes to ``None`` — an unmappable status is
#: not a guess (``contracts.universe``).
_STATUS: Final[dict[str, InstrumentStatus]] = {
    "TRADING": InstrumentStatus.TRADING,
    # Listed, announced, not yet tradable: "listed but temporarily not tradable"
    # is the closest of the three and the one that keeps it out of collection.
    "PENDING_TRADING": InstrumentStatus.SUSPENDED,
    "SETTLING": InstrumentStatus.SUSPENDED,
    "PRE_SETTLE": InstrumentStatus.SUSPENDED,
    "DELIVERING": InstrumentStatus.SUSPENDED,
    "PRE_DELIVERING": InstrumentStatus.SUSPENDED,
    "CLOSE": InstrumentStatus.DELISTED,
}

_PERPETUAL: Final = "PERPETUAL"


# --------------------------------------------------------------------------- #
# Scalar helpers
# --------------------------------------------------------------------------- #
def _decimal(value: object) -> Decimal | None:
    """The venue's own digits, or ``None``.

    Binance writes numbers as strings, and writes an absent one as ``""`` (the
    funding rate of a contract that has no funding). Empty is unknown, which is
    ``None`` — writing ``0`` there would say "the funding rate is zero".
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"expected the venue's own string or an int, got {value!r}")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip()
        return Decimal(text) if text else None
    raise TypeError(f"cannot read a Decimal out of {type(value).__name__}")


def _required_decimal(value: object, *, field: str) -> Decimal:
    parsed = _decimal(value)
    if parsed is None:
        raise ValueError(f"{field} is required and was empty")
    return parsed


def _ms(value: object, *, field: str) -> int:
    """UTC milliseconds as a plain ``int``. Binance sends both ints and numeric
    strings depending on the endpoint; seam ① accepts only the int."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} is required and was {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return int(value)
    raise ValueError(f"{field} is not a UTC millisecond timestamp: {value!r}")


def _mapping(node: object, *, field: str) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise ValueError(f"{field}: expected an object, got {type(node).__name__}")
    return node


def _sequence(node: object, *, field: str) -> list[Any]:
    if not isinstance(node, list):
        raise ValueError(f"{field}: expected an array, got {type(node).__name__}")
    return node


def _resolve(
    venue_symbol: object,
    *,
    symbols: SymbolMap,
    unknown: MutableSequence[str] | None,
) -> SymbolMapping | None:
    """Venue contract name → mapping, or ``None`` with the name recorded.

    An unrecognised symbol on a market-wide response is an ordinary event — a
    coin listed since the last universe reconciliation — so it is skipped and
    counted, never raised in the middle of a stream (``adapters.symbols``).
    """
    if not isinstance(venue_symbol, str) or not venue_symbol:
        raise ValueError(f"expected a venue symbol, got {venue_symbol!r}")
    mapping = symbols.to_unified(venue_symbol)
    if mapping is None and unknown is not None:
        unknown.append(venue_symbol)
    return mapping


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def normalize_exchange_info(
    payload: object,
    *,
    ingest_ts: int,
    funding_interval_h: dict[str, int] | None = None,
) -> tuple[InstrumentRecord, ...]:
    """``exchangeInfo`` (+ ``fundingInfo``) → one record per perpetual.

    Filtered to ``contractType == "PERPETUAL"`` (``04`` §2). Everything that
    survives is returned — both quote assets, every status — because
    ``instruments`` is the venue's contract list and which coins we *collect* is
    F1's cross-venue answer, owned by ``universe``.

    ``ts`` is ``exchangeInfo``'s own ``serverTime``: F1 requires a
    reconciliation instant at least once a day, and that is what this is.
    """
    body = _mapping(payload, field="exchangeInfo")
    server_time = _ms(body.get("serverTime"), field="exchangeInfo.serverTime")
    intervals = funding_interval_h or {}

    records: list[InstrumentRecord] = []
    for index, node in enumerate(_sequence(body.get("symbols"), field="exchangeInfo.symbols")):
        entry = _mapping(node, field=f"exchangeInfo.symbols[{index}]")
        if entry.get("contractType") != _PERPETUAL:
            continue
        venue_symbol = entry["symbol"]
        base_asset = entry["baseAsset"]
        if not isinstance(venue_symbol, str) or not isinstance(base_asset, str):
            raise ValueError(f"exchangeInfo.symbols[{index}]: symbol/baseAsset must be strings")
        symbol, mult = split_multiplier(base_asset)
        status = entry.get("status")
        records.append(
            InstrumentRecord(
                venue=Venue.BINANCE,
                symbol=symbol,
                ts=server_time,
                ingest_ts=ingest_ts,
                source=SOURCE_EXCHANGE_INFO,
                venue_symbol=venue_symbol,
                mult=mult,
                funding_interval_h=intervals.get(venue_symbol, DEFAULT_FUNDING_INTERVAL_H),
                tick=_price_tick(entry.get("filters")),
                status=_STATUS.get(status) if isinstance(status, str) else None,
            )
        )
    return tuple(records)


def _price_tick(filters: object) -> Decimal | None:
    """``PRICE_FILTER.tickSize``, or ``None`` if the venue did not send one."""
    if filters is None:
        return None
    for node in _sequence(filters, field="symbols[].filters"):
        entry = _mapping(node, field="symbols[].filters[]")
        if entry.get("filterType") == "PRICE_FILTER":
            return _decimal(entry.get("tickSize"))
    return None


def normalize_funding_info(payload: object) -> dict[str, int]:
    """``fundingInfo`` → ``{venue symbol: interval hours}``.

    ``04`` §2: the endpoint "只列非默认周期币", so a symbol absent from this
    mapping settles on Binance's 8-hour default. That absence is a statement
    about the venue, not an unknown.
    """
    intervals: dict[str, int] = {}
    for index, node in enumerate(_sequence(payload, field="fundingInfo")):
        entry = _mapping(node, field=f"fundingInfo[{index}]")
        venue_symbol = entry.get("symbol")
        hours = entry.get("fundingIntervalHours")
        if not isinstance(venue_symbol, str) or not venue_symbol:
            raise ValueError(f"fundingInfo[{index}].symbol: expected a venue symbol")
        if isinstance(hours, bool) or not isinstance(hours, int):
            raise ValueError(
                f"fundingInfo[{index}].fundingIntervalHours: expected whole hours, got {hours!r}"
            )
        intervals[venue_symbol] = hours
    return intervals


# --------------------------------------------------------------------------- #
# Fast lane
# --------------------------------------------------------------------------- #
def normalize_premium_index(
    payload: object,
    *,
    symbols: SymbolMap,
    ingest_ts: int,
    unknown: MutableSequence[str] | None = None,
) -> tuple[MarketRecord, ...]:
    """``premiumIndex`` → fast-lane records: mark, index, funding, next funding.

    One request answers both the ``mark_price`` and the ``funding_rate``
    capability, which is why ``03`` §6's lane table budgets **one** 10-weight
    call per 30 seconds and not two. Every field of the fast column group that
    Binance publishes is on the record; ``premium`` is ``None`` (see the module
    docstring), and the slow-lane fields are ``None`` because this is not that
    lane.
    """
    entries = payload if isinstance(payload, list) else [payload]
    records: list[MarketRecord] = []
    for index, node in enumerate(entries):
        entry = _mapping(node, field=f"premiumIndex[{index}]")
        mapping = _resolve(entry.get("symbol"), symbols=symbols, unknown=unknown)
        if mapping is None:
            continue
        observed = _ms(entry.get("time"), field=f"premiumIndex[{index}].time")
        next_funding = entry.get("nextFundingTime")
        records.append(
            MarketRecord(
                venue=Venue.BINANCE,
                symbol=mapping.symbol,
                ts=observed,
                ingest_ts=ingest_ts,
                source=SOURCE_PREMIUM_INDEX,
                semantic=Semantic.MARK_PRICE,
                mark=_decimal(entry.get("markPrice")),
                index_px=_decimal(entry.get("indexPrice")),
                premium=None,
                funding_rate=_decimal(entry.get("lastFundingRate")),
                funding_interval_h=mapping.funding_interval_h,
                # Binance writes 0 for "no scheduled settlement" on contracts
                # that do not fund; 0 as an epoch is 1970, so it is an unknown.
                next_funding_ts=(
                    _ms(next_funding, field="nextFundingTime") if next_funding else None
                ),
                obs_ts_fast=observed,
            )
        )
    return tuple(records)


def normalize_ws_mark_price(
    payload: object,
    *,
    symbols: SymbolMap,
    ingest_ts: int,
    unknown: MutableSequence[str] | None = None,
) -> tuple[MarketRecord, ...]:
    """``!markPrice@arr@1s`` frames → fast-lane records.

    Field names are the stream's, not the REST endpoint's (``04`` §2 / the
    2026-04-23 change notice): ``s`` symbol · ``p`` mark · ``i`` index ·
    ``r`` funding rate · ``T`` next funding time · ``E`` event time. ``P``
    (estimated settle price) has no column in ``03`` §5 and is dropped rather
    than parked in ``index_px``.
    """
    entries = payload if isinstance(payload, list) else [payload]
    records: list[MarketRecord] = []
    for index, node in enumerate(entries):
        entry = _mapping(node, field=f"markPriceUpdate[{index}]")
        mapping = _resolve(entry.get("s"), symbols=symbols, unknown=unknown)
        if mapping is None:
            continue
        observed = _ms(entry.get("E"), field=f"markPriceUpdate[{index}].E")
        next_funding = entry.get("T")
        records.append(
            MarketRecord(
                venue=Venue.BINANCE,
                symbol=mapping.symbol,
                ts=observed,
                ingest_ts=ingest_ts,
                source=SOURCE_WS_MARK_PRICE,
                semantic=Semantic.MARK_PRICE,
                mark=_decimal(entry.get("p")),
                index_px=_decimal(entry.get("i")),
                premium=None,
                funding_rate=_decimal(entry.get("r")),
                funding_interval_h=mapping.funding_interval_h,
                next_funding_ts=(_ms(next_funding, field="T") if next_funding else None),
                obs_ts_fast=observed,
            )
        )
    return tuple(records)


def normalize_funding_rate_history(
    payload: object,
    *,
    symbols: SymbolMap,
    ingest_ts: int,
    unknown: MutableSequence[str] | None = None,
) -> tuple[MarketRecord, ...]:
    """``fundingRate`` → one fast-lane record per settled funding period.

    ``04`` §2's field list is ``fundingRate, fundingTime``; ``ts`` is the
    settlement instant and the rate stays on the venue's native interval.

    ``semantic`` is ``mark_price`` because seam ① makes it mandatory on a
    :class:`MarketRecord` and offers no third value — a settled funding rate is
    neither a price nor a candle close. It is the Binance premium-index basis
    the rate is computed from, so ``mark_price`` is the less wrong of the two;
    the PR reports the gap rather than widening the contract here.
    """
    records: list[MarketRecord] = []
    for index, node in enumerate(_sequence(payload, field="fundingRate")):
        entry = _mapping(node, field=f"fundingRate[{index}]")
        mapping = _resolve(entry.get("symbol"), symbols=symbols, unknown=unknown)
        if mapping is None:
            continue
        settled = _ms(entry.get("fundingTime"), field=f"fundingRate[{index}].fundingTime")
        records.append(
            MarketRecord(
                venue=Venue.BINANCE,
                symbol=mapping.symbol,
                ts=settled,
                ingest_ts=ingest_ts,
                source=SOURCE_FUNDING_RATE,
                semantic=Semantic.MARK_PRICE,
                funding_rate=_required_decimal(
                    entry.get("fundingRate"), field=f"fundingRate[{index}].fundingRate"
                ),
                funding_interval_h=mapping.funding_interval_h,
                obs_ts_fast=settled,
                grid_s=mapping.funding_interval_h * 3600,
                backfilled=True,
            )
        )
    return tuple(records)


# --------------------------------------------------------------------------- #
# Slow lane
# --------------------------------------------------------------------------- #
def normalize_open_interest(
    payload: object,
    *,
    symbols: SymbolMap,
    ingest_ts: int,
    unknown: MutableSequence[str] | None = None,
) -> tuple[MarketRecord, ...]:
    """``openInterest`` → one slow-lane record.

    The venue reports open interest in units of the **listed contract**, so
    ``1000PEPEUSDT``'s number counts thousands of PEPE. ``oi_base`` is in the
    coin's own units, which is what makes an ``oi_share`` across two venues mean
    anything, so the mapping's multiplier is applied here — and if the
    multiplier is unknown the answer is ``None``, exactly as ``03`` §5 requires
    of an unknown notional.
    """
    entry = _mapping(payload, field="openInterest")
    mapping = _resolve(entry.get("symbol"), symbols=symbols, unknown=unknown)
    if mapping is None:
        return ()
    observed = _ms(entry.get("time"), field="openInterest.time")
    contracts = _decimal(entry.get("openInterest"))
    return (
        MarketRecord(
            venue=Venue.BINANCE,
            symbol=mapping.symbol,
            ts=observed,
            ingest_ts=ingest_ts,
            source=SOURCE_OPEN_INTEREST,
            semantic=Semantic.MARK_PRICE,
            oi_base=None if contracts is None else mapping.to_base_units(contracts),
            oi_usd=None,
            obs_ts_slow=observed,
        ),
    )


def normalize_ticker_24h(
    payload: object,
    *,
    symbols: SymbolMap,
    ingest_ts: int,
    unknown: MutableSequence[str] | None = None,
) -> tuple[MarketRecord, ...]:
    """``ticker/24hr`` → slow-lane records: 24 h change and quote volume.

    ``quoteVolume`` is denominated in the contract's quote asset. F3 fixes the
    project on USD-quoted perpetuals ("一律 USD 永续"), so the USDT figure *is*
    the USD figure here; a coin quoted in something else would need a rate, and
    F3 rules currency conversion out rather than inventing one.
    """
    entries = payload if isinstance(payload, list) else [payload]
    records: list[MarketRecord] = []
    for index, node in enumerate(entries):
        entry = _mapping(node, field=f"ticker24hr[{index}]")
        mapping = _resolve(entry.get("symbol"), symbols=symbols, unknown=unknown)
        if mapping is None:
            continue
        observed = _ms(entry.get("closeTime"), field=f"ticker24hr[{index}].closeTime")
        records.append(
            MarketRecord(
                venue=Venue.BINANCE,
                symbol=mapping.symbol,
                ts=observed,
                ingest_ts=ingest_ts,
                source=SOURCE_TICKER_24H,
                semantic=Semantic.MARK_PRICE,
                vol24h_usd=_decimal(entry.get("quoteVolume")),
                chg24h_pct=_decimal(entry.get("priceChangePercent")),
                obs_ts_slow=observed,
            )
        )
    return tuple(records)


# --------------------------------------------------------------------------- #
# Ratios
# --------------------------------------------------------------------------- #
def normalize_ls_ratio(
    payload: object,
    *,
    kind: LsRatioKind,
    symbol: str,
    ingest_ts: int,
) -> tuple[LsRatioPoint, ...]:
    """One ``/futures/data/*`` response → :class:`LsRatioPoint` s.

    ``symbol`` is the **unified** name and is passed in rather than read out of
    the response, because ``takerlongshortRatio`` does not echo one: ``04`` §2
    lists its fields as ``buyVol, sellVol`` and the response carries only those
    plus ``buySellRatio`` and ``timestamp``. The account-ratio endpoints do echo
    a ``symbol``; relying on it for two of three and not the third would be the
    kind of asymmetry that goes wrong quietly.

    The taker share is ``buyVol ÷ (buyVol + sellVol)``. When both sides are zero
    the share is ``None``, not ``0`` — nobody traded is not "everybody sold".
    """
    points: list[LsRatioPoint] = []
    for index, node in enumerate(_sequence(payload, field="futures_data")):
        entry = _mapping(node, field=f"futures_data[{index}]")
        observed = _ms(entry.get("timestamp"), field=f"futures_data[{index}].timestamp")
        if kind is LsRatioKind.TAKER_LONG_SHORT:
            long_share = _taker_share(entry, index=index)
        else:
            long_share = _decimal(entry.get("longAccount"))
        points.append(
            LsRatioPoint(
                venue=Venue.BINANCE,
                symbol=symbol,
                ts=observed,
                ingest_ts=ingest_ts,
                source=source_of_ls_ratio_kind(kind),
                kind=kind,
                long_share=long_share,
                period=LS_RATIO_PERIOD_S,
            )
        )
    return tuple(points)


def _taker_share(entry: dict[str, Any], *, index: int) -> Decimal | None:
    buy = _decimal(entry.get("buyVol"))
    sell = _decimal(entry.get("sellVol"))
    if buy is None or sell is None:
        return None
    total = buy + sell
    if total <= 0:
        return None
    if buy < 0 or sell < 0:
        raise ValueError(f"futures_data[{index}]: a traded volume cannot be negative")
    return buy / total


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
def normalize_klines(
    payload: object,
    *,
    symbol: str,
    interval: str,
    ingest_ts: int,
    now_ms: int,
) -> tuple[MarketRecord, ...]:
    """``klines`` → ``semantic='candle_close'`` records, **closed bars only**.

    ``04`` §2's array layout: ``[openTime, open, high, low, close, volume,
    closeTime, quoteAssetVolume, trades, takerBuyBaseVolume,
    takerBuyQuoteVolume, ignore]``. Only the close price and the close instant
    have a column in ``03`` §5; ``takerBuyBaseVolume`` has none (``04`` §12
    lists it as a gap) and is deliberately dropped rather than parked somewhere
    plausible.

    The bar still forming is discarded: a partial bar's close is not a close,
    and writing it would put a number that is about to change into a row F8
    later counts. ``now_ms`` is the caller's clock, so the rule is testable.

    ``backfilled`` is ``True`` and ``grid_s`` is the interval's spacing: these
    rows are the input to ``03`` §5's **third** upsert statement, the one that
    must never be merged with the live ones. A candle close is never a live
    observation of the current minute, so the flag is the record's own truth
    rather than a caller's framing.
    """
    grid_s = KLINE_GRID_SECONDS.get(interval)
    if grid_s is None:
        raise ValueError(f"unknown kline interval {interval!r}; 04 §1 collects 1m / 1h / 1d")

    records: list[MarketRecord] = []
    for index, node in enumerate(_sequence(payload, field="klines")):
        row = _sequence(node, field=f"klines[{index}]")
        if len(row) < 7:
            raise ValueError(f"klines[{index}]: expected at least 7 columns, got {len(row)}")
        close_time = _ms(row[6], field=f"klines[{index}][6] closeTime")
        if close_time >= now_ms:
            continue
        records.append(
            MarketRecord(
                venue=Venue.BINANCE,
                symbol=symbol,
                ts=close_time,
                ingest_ts=ingest_ts,
                source=SOURCE_KLINES,
                semantic=Semantic.CANDLE_CLOSE,
                mark=_required_decimal(row[4], field=f"klines[{index}][4] close"),
                obs_ts_fast=close_time,
                grid_s=grid_s,
                backfilled=True,
            )
        )
    return tuple(records)


# --------------------------------------------------------------------------- #
# Liquidations — seam ①'s floor and nothing more
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ForcedOrderObservation:
    """One ``!forceOrder@arr`` message, carrying only what seam ① requires.

    M1 has **no liquidation contract**: M1-A1 left it to M2 because its
    completeness semantics are decided by data that has not been collected yet,
    and ``adapters/base.py`` therefore types this stream as
    :class:`~hlens_core.adapters.base.NormalizedRecord` — "the five attributes
    seam ① requires of every record that crosses a boundary, and nothing more:
    no invented columns, no raw JSON".

    So ``side``, ``price``, ``size`` and ``notional_usd`` are **not here**. They
    are ``03`` §5's ``liquidations`` columns and M2 owns them together with the
    ``completeness`` / ``throttled_source`` pair that makes F12's ``下界`` label
    mean something. Inventing them a step early would mean choosing their
    semantics in the one place that has no data to check them against.

    **Not frozen, and that is not a preference.** Seam ②'s ``NormalizedRecord``
    protocol declares its five attributes as *settable* variables, so a frozen
    dataclass is not assignable to it — mypy says "expected settable variable,
    got read-only attribute". The contract models get away with being frozen
    only because they are pydantic and mypy is run without the pydantic plugin.
    Reported in the PR; M2, which adds the real liquidation contract, is the
    step that should decide whether the protocol's attributes become read-only.
    """

    venue: Venue
    symbol: str
    ts: int
    ingest_ts: int
    source: str


def normalize_ws_force_order(
    payload: object,
    *,
    symbols: SymbolMap,
    ingest_ts: int,
    unknown: MutableSequence[str] | None = None,
) -> tuple[ForcedOrderObservation, ...]:
    """``!forceOrder@arr`` frames → the seam-① floor, one per message.

    ``04`` §2: the venue pushes **at most one order per symbol per second**, so
    what arrives is a lower bound on what happened. Nothing here can fix that
    and nothing here should pretend to — the capability declaration says
    ``lower_bound`` with ``throttled_source=True`` and F12 carries the label all
    the way to the page.

    ``ts`` is the order's trade time ``o.T``, not the frame's ``E``: the frame
    time is when Binance decided to tell us.
    """
    entries = payload if isinstance(payload, list) else [payload]
    records: list[ForcedOrderObservation] = []
    for index, node in enumerate(entries):
        frame = _mapping(node, field=f"forceOrder[{index}]")
        order = _mapping(frame.get("o"), field=f"forceOrder[{index}].o")
        mapping = _resolve(order.get("s"), symbols=symbols, unknown=unknown)
        if mapping is None:
            continue
        records.append(
            ForcedOrderObservation(
                venue=Venue.BINANCE,
                symbol=mapping.symbol,
                ts=_ms(order.get("T"), field=f"forceOrder[{index}].o.T"),
                ingest_ts=ingest_ts,
                source=SOURCE_WS_FORCE_ORDER,
            )
        )
    return tuple(records)


def normalized_venue_symbols(records: Sequence[InstrumentRecord]) -> tuple[str, ...]:
    """Every venue contract name in ``records``, in order. Small enough to be
    obvious, useful enough that three call sites would otherwise re-derive it."""
    return tuple(record.venue_symbol for record in records)
