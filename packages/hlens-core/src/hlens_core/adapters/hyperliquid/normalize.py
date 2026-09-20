"""Hyperliquid's JSON → seam ①'s contracts. The whole of the adapter's real job.

Rules this module does not get to reinterpret
---------------------------------------------
* **``ts`` is the observation instant, never a bucket.** Nothing here rounds
  anything; ``date_trunc('minute', …)`` happens at the persistence boundary
  (M1-C / M1-E).
* **Unknown is ``None``, never 0, and ``mult`` has no default of 1.**
* **``Decimal`` from the venue's own string.** Never ``float`` — a price that
  has been through binary floating point has already lost the venue's digits.
* **Funding stays on the venue's native interval**, which here is **one hour**
  (:data:`~.symbols.FUNDING_INTERVAL_H`). The raw hourly value is stored with
  ``funding_interval_h=1``; the 8-hour figure is a property of the contract,
  computed on read and never stored. Pre-multiplying by 8 here — or letting an
  interval of 8 reach a record by way of a copied default — would inflate every
  Hyperliquid funding rate eightfold in the direction that makes the venue look
  extreme, and nothing downstream could tell.

The one field Hyperliquid has and Binance does not
--------------------------------------------------
``premium`` is **real here**. ``04`` §2 lists Binance ``premiumIndex``'s fields
as ``markPrice, indexPrice, lastFundingRate, nextFundingTime`` — no premium
among them — so the Binance adapter writes ``None`` and explicitly refuses to
synthesise ``mark − index``. ``04`` §3 lists ``premium`` among
``metaAndAssetCtxs``'s own fields, and ``fundingHistory`` carries one per row,
so on this venue the column is filled from the venue's own number. Two adapters
disagreeing about one column is the correct outcome: ``03`` §5's ``premium`` is
an observation where the venue makes one and ``NULL`` where it does not.

What Hyperliquid does **not** send, and is therefore ``None``
-------------------------------------------------------------
``chg24h_pct``
    ``04`` §3's ``metaAndAssetCtxs`` field list has ``dayNtlVlm`` but no
    previous-day price, so the 24-hour change has no observed input. ``04`` §1
    nonetheless lists ``chg24h_pct`` as a ``market_1m`` column fed by "HL WS
    ``allMids``", which publishes only mid prices. Reported in "Doc
    corrections"; the column stays ``NULL`` rather than being reconstructed
    from a stored row 24 hours old, which would be a computation on our own
    history rather than an observation of the venue's.
``oi_usd``
    ``markPx × openInterest`` would be exact here — unlike on Binance, both
    numbers arrive in the *same* response at the *same* instant — but it is
    still a computation, and ``03`` §4 gives computations to ``compute``. An
    adapter that starts multiplying observations together produces rows nobody
    downstream can tell from measurements.
``next_funding_ts``
    ``metaAndAssetCtxs`` does not carry it. Hyperliquid settles on the hour, so
    it could be rounded up from the observation instant — which is arithmetic
    on a schedule, not an observation, and would be wrong for exactly the hour
    when it matters (a schedule change). ``predictedFundings`` publishes a real
    ``nextFundingTime`` and that is where the column is filled from.
``tick``
    ``meta`` publishes ``szDecimals`` (a *size* precision) and not a price
    increment. Deriving one from the significant-figure rule would be a guess
    about pricing from a fact about sizing.

``semantic`` on a record with no price
--------------------------------------
Seam ① makes ``semantic`` mandatory on :class:`MarketRecord` and offers two
values, both about a price. A settled funding rate is neither a mark price nor
a candle close; ``mark_price`` is the less wrong of the two (it is the mark
basis the rate is computed against) and is what the Binance adapter chose for
the same reason. The gap is reported rather than papered over by widening the
contract here.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableSequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from hlens_core.adapters.symbols import SymbolMap, SymbolMapping
from hlens_core.contracts import (
    InstrumentRecord,
    MarketRecord,
    Semantic,
    Venue,
)

from .symbols import FUNDING_INTERVAL_H, split_multiplier

__all__ = [
    "CANDLE_GRID_SECONDS",
    "PREDICTED_FUNDING_VENUE_KEY",
    "SOURCE_CANDLE_SNAPSHOT",
    "SOURCE_FUNDING_HISTORY",
    "SOURCE_META",
    "SOURCE_META_AND_ASSET_CTXS",
    "SOURCE_PREDICTED_FUNDINGS",
    "SOURCE_WS_ALL_MIDS",
    "AssetMeta",
    "asset_metadata",
    "normalize_candle_snapshot",
    "normalize_funding_history",
    "normalize_meta",
    "normalize_meta_and_asset_ctxs",
    "normalize_predicted_fundings",
    "normalize_ws_all_mids",
]

# --------------------------------------------------------------------------- #
# `source` tags. Convention from contracts/base.py: `<venue>_<transport>_<endpoint>`.
# On this venue the tag carries more weight than on Binance, because one path
# serves every endpoint: `source` is the only column that says which `type` a
# row came from — and, for `hyperliquid_ws_all_mids`, which price basis it is.
# --------------------------------------------------------------------------- #
SOURCE_META: Final = "hyperliquid_rest_meta"
SOURCE_META_AND_ASSET_CTXS: Final = "hyperliquid_rest_meta_and_asset_ctxs"
SOURCE_PREDICTED_FUNDINGS: Final = "hyperliquid_rest_predicted_fundings"
SOURCE_FUNDING_HISTORY: Final = "hyperliquid_rest_funding_history"
SOURCE_CANDLE_SNAPSHOT: Final = "hyperliquid_rest_candle_snapshot"
SOURCE_WS_ALL_MIDS: Final = "hyperliquid_ws_all_mids"

#: ``grid_s`` for each candle interval, so a backfilled row can say which grid
#: it sits on and F8's ``n`` stays traceable.
CANDLE_GRID_SECONDS: Final[dict[str, int]] = {"1m": 60, "1h": 3600, "1d": 86400}

#: Which entry of a ``predictedFundings`` row is **this** venue's.
#:
#: ``04`` §3 describes the response as "各所预测费率（含 Binance 的预测值）"
#: and names none of the per-venue keys → ``未验证``, and reported in "Doc
#: corrections". The value below is the one Hyperliquid's own documentation
#: uses for its perpetuals. Everything that is not this key is **skipped**:
#: a Hyperliquid adapter that emitted a Binance record would break seam ② and
#: :class:`~hlens_core.contracts.SingleVenueRecord`'s whole premise — an
#: adapter is one venue, and a venue cannot observe another one for us.
PREDICTED_FUNDING_VENUE_KEY: Final = "HlPerp"


@dataclass(frozen=True, slots=True)
class AssetMeta:
    """``meta.universe``'s two sizing facts, normalized but **not** a contract.

    ``04`` §3 lists ``szDecimals`` and ``maxLeverage`` as key fields of ``meta``
    and ``04`` §1 wants them for F1/F3 and for M6's key levels (MMR ≈
    ``1 / (2 · maxLeverage)``, an official *approximation* and not an
    endpoint). ``03`` §5's ``instruments`` table has a column for neither, so
    they are carried here — a small typed object, not raw JSON — and exposed on
    the adapter rather than written anywhere. Reported in "Doc corrections";
    inventing contract fields for them is M1-A1's decision to revisit, not
    this step's.

    The MMR approximation is deliberately **not** computed here: it is a
    formula from ``04``, it belongs to whoever publishes key levels, and a
    stored approximation is indistinguishable from a published number.
    """

    venue_symbol: str
    sz_decimals: int
    max_leverage: int | None


# --------------------------------------------------------------------------- #
# Scalar helpers
# --------------------------------------------------------------------------- #
def _decimal(value: object) -> Decimal | None:
    """The venue's own digits, or ``None``.

    Hyperliquid writes numbers as strings. An empty string is unknown, which is
    ``None`` — writing ``0`` there would state a measurement we do not have.
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
    """UTC milliseconds as a plain ``int``."""
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


def _int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field}: expected a whole number, got {value!r}")
    return value


def _universe(node: object, *, field: str) -> list[dict[str, Any]]:
    body = _mapping(node, field=field)
    return [
        _mapping(entry, field=f"{field}.universe[{index}]")
        for index, entry in enumerate(_sequence(body.get("universe"), field=f"{field}.universe"))
    ]


def _coin(entry: Mapping[str, Any], *, field: str) -> str:
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{field}.name: expected a coin name, got {name!r}")
    return name


def _resolve(
    venue_symbol: object,
    *,
    symbols: SymbolMap,
    unknown: MutableSequence[str] | None,
) -> SymbolMapping | None:
    """Venue coin name → mapping, or ``None`` with the name recorded.

    An unrecognised coin on a market-wide response is an ordinary event — one
    listed since the last universe reconciliation — so it is skipped and
    counted, never raised in the middle of a stream.
    """
    if not isinstance(venue_symbol, str) or not venue_symbol:
        raise ValueError(f"expected a venue coin name, got {venue_symbol!r}")
    mapping = symbols.to_unified(venue_symbol)
    if mapping is None and unknown is not None:
        unknown.append(venue_symbol)
    return mapping


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def normalize_meta(
    payload: object, *, ingest_ts: int, observed_ts: int
) -> tuple[InstrumentRecord, ...]:
    """``meta`` → one record per perpetual.

    Everything ``meta.universe`` lists is returned: ``instruments`` is the
    venue's contract list, and which coins we *collect* is F1's cross-venue
    answer, owned by ``universe``.

    ``observed_ts`` is the caller's clock at receipt, and it is ``ts``.
    Hyperliquid sends **no server timestamp** on this endpoint (``04`` §3's
    field list has none), so the instant we observed the snapshot is the only
    instant there is — which is what ``ts`` means, and is still not a bucket.
    It is also why preflight's clock check (``04`` §7) cannot be calibrated
    against this venue the way it is against Binance's ``serverTime``.

    ``status`` is ``None`` for every record: ``04`` §3 documents no status
    vocabulary for ``meta.universe``, and :class:`~hlens_core.contracts.
    InstrumentStatus` says an unmappable status is ``None``, not a guess.
    """
    records: list[InstrumentRecord] = []
    for index, entry in enumerate(_universe(payload, field="meta")):
        coin = _coin(entry, field=f"meta.universe[{index}]")
        symbol, mult = split_multiplier(coin)
        records.append(
            InstrumentRecord(
                venue=Venue.HYPERLIQUID,
                symbol=symbol,
                ts=observed_ts,
                ingest_ts=ingest_ts,
                source=SOURCE_META,
                venue_symbol=coin,
                mult=mult,
                # The constant, not a lookup with a fallback: every Hyperliquid
                # perpetual funds hourly (04 §3, §5).
                funding_interval_h=FUNDING_INTERVAL_H,
                tick=None,
                status=None,
            )
        )
    return tuple(records)


def asset_metadata(payload: object) -> tuple[AssetMeta, ...]:
    """``meta`` → :class:`AssetMeta` per coin: ``szDecimals`` and ``maxLeverage``.

    Separate from :func:`normalize_meta` because they are separate questions:
    one produces the contract records ``03`` §5 stores, the other carries two
    documented fields that have no column to be stored in. Both read the same
    response, so exposing this costs no extra weight.
    """
    metas: list[AssetMeta] = []
    for index, entry in enumerate(_universe(payload, field="meta")):
        where = f"meta.universe[{index}]"
        leverage = entry.get("maxLeverage")
        metas.append(
            AssetMeta(
                venue_symbol=_coin(entry, field=where),
                sz_decimals=_int(entry.get("szDecimals"), field=f"{where}.szDecimals"),
                max_leverage=(
                    None if leverage is None else _int(leverage, field=f"{where}.maxLeverage")
                ),
            )
        )
    return tuple(metas)


# --------------------------------------------------------------------------- #
# The one call that answers four capabilities
# --------------------------------------------------------------------------- #
def normalize_meta_and_asset_ctxs(
    payload: object,
    *,
    symbols: SymbolMap,
    ingest_ts: int,
    observed_ts: int,
    unknown: MutableSequence[str] | None = None,
) -> tuple[MarketRecord, ...]:
    """``metaAndAssetCtxs`` → one record per coin, carrying **both** lanes.

    The response is a two-element array: the ``meta`` object, then the array of
    contexts. **They are paired by position, and nothing in the response says
    so** — no context carries a coin name. A length mismatch is therefore an
    error and not something to zip through, because the failure mode of zipping
    is every coin's price attached to the next coin's name, silently, in a
    market-wide feed.

    One request answers ``mark_price``, ``funding_rate``, ``open_interest`` and
    ``ticker_24h`` at one instant, which is why ``03`` §6's lane table budgets
    40 weight a minute for this venue's whole fast lane. The record therefore
    carries **both** ``obs_ts_fast`` and ``obs_ts_slow``: §5's two live column
    groups really were observed together here, unlike on Binance where they
    come from different endpoints at different instants.
    """
    body = _sequence(payload, field="metaAndAssetCtxs")
    if len(body) != 2:
        raise ValueError(
            f"metaAndAssetCtxs: expected [meta, contexts], got {len(body)} element(s)"
        )
    universe = _universe(body[0], field="metaAndAssetCtxs[0]")
    contexts = _sequence(body[1], field="metaAndAssetCtxs[1]")
    if len(universe) != len(contexts):
        raise ValueError(
            f"metaAndAssetCtxs: {len(universe)} coins but {len(contexts)} contexts; "
            "the two arrays are paired by position and nothing in the response "
            "repeats the coin name, so a mismatch cannot be recovered from"
        )

    records: list[MarketRecord] = []
    for index, (entry, node) in enumerate(zip(universe, contexts, strict=True)):
        coin = _coin(entry, field=f"metaAndAssetCtxs[0].universe[{index}]")
        context = _mapping(node, field=f"metaAndAssetCtxs[1][{index}]")
        mapping = _resolve(coin, symbols=symbols, unknown=unknown)
        if mapping is None:
            continue
        open_interest = _decimal(context.get("openInterest"))
        records.append(
            MarketRecord(
                venue=Venue.HYPERLIQUID,
                symbol=mapping.symbol,
                ts=observed_ts,
                ingest_ts=ingest_ts,
                source=SOURCE_META_AND_ASSET_CTXS,
                semantic=Semantic.MARK_PRICE,
                mark=_decimal(context.get("markPx")),
                # HL's oracle price is this venue's index basis; it is the
                # venue's own published number, not a stand-in computed here.
                index_px=_decimal(context.get("oraclePx")),
                # Real on this venue — see the module docstring.
                premium=_decimal(context.get("premium")),
                funding_rate=_decimal(context.get("funding")),
                funding_interval_h=FUNDING_INTERVAL_H,
                next_funding_ts=None,
                obs_ts_fast=observed_ts,
                # Open interest is quoted in units of the listed contract, so a
                # `kPEPE` figure counts thousands of PEPE. `oi_base` is in the
                # coin's own units — which is what makes an `oi_share` across
                # two venues mean anything — and an unknown multiplier yields
                # None, exactly as 03 §5 requires of an unknown notional.
                oi_base=None if open_interest is None else mapping.to_base_units(open_interest),
                oi_usd=None,
                vol24h_usd=_decimal(context.get("dayNtlVlm")),
                chg24h_pct=None,
                obs_ts_slow=observed_ts,
            )
        )
    return tuple(records)


# --------------------------------------------------------------------------- #
# Funding: predicted and settled
# --------------------------------------------------------------------------- #
def normalize_predicted_fundings(
    payload: object,
    *,
    symbols: SymbolMap,
    ingest_ts: int,
    observed_ts: int,
    unknown: MutableSequence[str] | None = None,
    other_venues: MutableSequence[str] | None = None,
) -> tuple[MarketRecord, ...]:
    """``predictedFundings`` → **this venue's** predicted rate, and no other's.

    The response is an array of ``[coin, [[venue key, {...}], …]]`` and ``04``
    §3 is explicit that it contains other exchanges' predictions, Binance's
    included. Every entry whose venue key is not
    :data:`PREDICTED_FUNDING_VENUE_KEY` is skipped and counted in
    ``other_venues``: a Hyperliquid adapter that returned a Binance record
    would be one adapter claiming to observe two venues, which seam ② exists to
    prevent and which :class:`~hlens_core.contracts.SingleVenueRecord` could
    not catch — the record would be perfectly well-formed and simply not ours
    to make.

    ``ts`` is the observation instant and ``next_funding_ts`` is the venue's
    own ``nextFundingTime``, which is the only place this adapter gets one.
    **A predicted rate is not a settled one**, and ``03`` §5 has no column that
    distinguishes them — only ``source`` does. M1's collector calls nothing
    here; the method exists because ``04`` §1 lists the endpoint and because
    F7's funding comparison is the obvious future caller. Reported in "Doc
    corrections".
    """
    records: list[MarketRecord] = []
    for index, node in enumerate(_sequence(payload, field="predictedFundings")):
        row = _sequence(node, field=f"predictedFundings[{index}]")
        if len(row) != 2:
            raise ValueError(
                f"predictedFundings[{index}]: expected [coin, venues], got {len(row)} element(s)"
            )
        mapping = _resolve(row[0], symbols=symbols, unknown=unknown)
        for venue_index, venue_node in enumerate(
            _sequence(row[1], field=f"predictedFundings[{index}][1]")
        ):
            pair = _sequence(venue_node, field=f"predictedFundings[{index}][1][{venue_index}]")
            if len(pair) != 2:
                raise ValueError(
                    f"predictedFundings[{index}][1][{venue_index}]: expected "
                    f"[venue, prediction], got {len(pair)} element(s)"
                )
            key = pair[0]
            if key != PREDICTED_FUNDING_VENUE_KEY:
                if other_venues is not None and isinstance(key, str):
                    other_venues.append(key)
                continue
            if mapping is None:
                continue
            prediction = _mapping(
                pair[1], field=f"predictedFundings[{index}][1][{venue_index}][1]"
            )
            next_funding = prediction.get("nextFundingTime")
            records.append(
                MarketRecord(
                    venue=Venue.HYPERLIQUID,
                    symbol=mapping.symbol,
                    ts=observed_ts,
                    ingest_ts=ingest_ts,
                    source=SOURCE_PREDICTED_FUNDINGS,
                    semantic=Semantic.MARK_PRICE,
                    funding_rate=_decimal(prediction.get("fundingRate")),
                    funding_interval_h=FUNDING_INTERVAL_H,
                    next_funding_ts=(
                        _ms(next_funding, field="nextFundingTime") if next_funding else None
                    ),
                    obs_ts_fast=observed_ts,
                )
            )
    return tuple(records)


def normalize_funding_history(
    payload: object, *, symbol: str, ingest_ts: int
) -> tuple[MarketRecord, ...]:
    """``fundingHistory`` → one record per settled **hourly** period.

    ``04`` §3's field list is ``fundingRate, premium, time``; ``ts`` is the
    settlement instant and the rate stays on the venue's native one-hour
    interval. ``grid_s`` is 3600 for the same reason, which is what makes F8's
    percentile able to say which grid its ``n`` was counted on — Binance's
    funding history lands on an 8-hour grid and the two must never be pooled.

    ``symbol`` is the **unified** name and is passed in rather than read out of
    the response: ``04`` §3 does not list a ``coin`` field on these rows, and
    the request asked about exactly one coin. Relying on a field the document
    does not promise is how a normalizer starts working by accident.
    """
    records: list[MarketRecord] = []
    for index, node in enumerate(_sequence(payload, field="fundingHistory")):
        entry = _mapping(node, field=f"fundingHistory[{index}]")
        settled = _ms(entry.get("time"), field=f"fundingHistory[{index}].time")
        records.append(
            MarketRecord(
                venue=Venue.HYPERLIQUID,
                symbol=symbol,
                ts=settled,
                ingest_ts=ingest_ts,
                source=SOURCE_FUNDING_HISTORY,
                semantic=Semantic.MARK_PRICE,
                funding_rate=_required_decimal(
                    entry.get("fundingRate"), field=f"fundingHistory[{index}].fundingRate"
                ),
                funding_interval_h=FUNDING_INTERVAL_H,
                premium=_decimal(entry.get("premium")),
                obs_ts_fast=settled,
                grid_s=FUNDING_INTERVAL_H * 3600,
                backfilled=True,
            )
        )
    return tuple(records)


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
def normalize_candle_snapshot(
    payload: object,
    *,
    symbol: str,
    interval: str,
    ingest_ts: int,
    now_ms: int,
) -> tuple[MarketRecord, ...]:
    """``candleSnapshot`` → ``semantic='candle_close'`` records, closed bars only.

    ``04`` §3's field list is ``t,o,h,l,c,v,n``. Only the close price and the
    close instant have a column in ``03`` §5; the rest is dropped rather than
    parked somewhere plausible.

    The close instant is the response's ``T`` when it is there and the
    interval's own arithmetic when it is not — ``04`` §3's list names only the
    open time, so the fallback exists rather than a field being assumed
    (reported in "Doc corrections"). The fallback is ``t + grid_s − 1 ms``,
    **not** ``t + grid_s``: the venue's own ``T`` is the last millisecond
    inside the bar, and a fallback that landed on the next bar's open instead
    would give the same bar two different ``ts`` depending on which path
    produced it — which §5's upsert would then store as two rows.

    The bar still forming is discarded: a partial bar's close is not a close,
    and writing it would put a number that is about to change into a row F8
    later counts. ``now_ms`` is the caller's clock, so the rule is testable.

    ``backfilled`` is ``True`` and ``grid_s`` is the interval's spacing — these
    rows are the input to ``03`` §5's **third** upsert statement, never the live
    ones. The depth behind them is this venue's own limit and the reason the
    klines capability is ``partial_history``: about 5000 bars, ~3.5 days at 1m
    (``04`` §5).
    """
    grid_s = CANDLE_GRID_SECONDS.get(interval)
    if grid_s is None:
        raise ValueError(f"unknown candle interval {interval!r}; 04 §1 collects 1m / 1h / 1d")

    records: list[MarketRecord] = []
    for index, node in enumerate(_sequence(payload, field="candleSnapshot")):
        entry = _mapping(node, field=f"candleSnapshot[{index}]")
        closed = entry.get("T")
        close_time = (
            _ms(closed, field=f"candleSnapshot[{index}].T")
            if closed is not None
            else _ms(entry.get("t"), field=f"candleSnapshot[{index}].t") + grid_s * 1000 - 1
        )
        if close_time >= now_ms:
            continue
        records.append(
            MarketRecord(
                venue=Venue.HYPERLIQUID,
                symbol=symbol,
                ts=close_time,
                ingest_ts=ingest_ts,
                source=SOURCE_CANDLE_SNAPSHOT,
                semantic=Semantic.CANDLE_CLOSE,
                mark=_required_decimal(entry.get("c"), field=f"candleSnapshot[{index}].c"),
                obs_ts_fast=close_time,
                grid_s=grid_s,
                backfilled=True,
            )
        )
    return tuple(records)


# --------------------------------------------------------------------------- #
# The stream
# --------------------------------------------------------------------------- #
def normalize_ws_all_mids(
    payload: object,
    *,
    symbols: SymbolMap,
    ingest_ts: int,
    observed_ts: int,
    unknown: MutableSequence[str] | None = None,
) -> tuple[MarketRecord, ...]:
    """``allMids`` frames → fast-lane records. **The price is a mid.**

    ``04`` §3 defines this channel as ``币→中间价``: what it pushes is the mid
    price, not the venue's mark price. ``04`` §1 nonetheless names it as the
    push source for the mark column, and ``03`` §6's fast lane polls
    ``metaAndAssetCtxs`` for the real ``markPx`` twice a minute — so this
    adapter follows the architecture's wiring and makes the basis visible
    instead of quietly substituting one price for the other:

    * the value lands in ``mark`` because ``03`` §5 has no mid column, and
    * every such row carries ``source='hyperliquid_ws_all_mids'``, which is
      what tells F7's cross-venue mark spread to use the REST rows instead.

    Raised in the PR's "Doc corrections" rather than resolved here: which
    column a venue's mid belongs in is a data-model question, and the data
    model is not this step's to change.

    The frame carries no timestamp of its own, so ``ts`` is the receipt instant
    — the observation instant, still never a bucket.
    """
    body = _mapping(payload, field="allMids")
    mids = _mapping(body.get("mids"), field="allMids.mids")
    records: list[MarketRecord] = []
    for coin, price in mids.items():
        mapping = _resolve(coin, symbols=symbols, unknown=unknown)
        if mapping is None:
            continue
        records.append(
            MarketRecord(
                venue=Venue.HYPERLIQUID,
                symbol=mapping.symbol,
                ts=observed_ts,
                ingest_ts=ingest_ts,
                source=SOURCE_WS_ALL_MIDS,
                semantic=Semantic.MARK_PRICE,
                mark=_decimal(price),
                funding_interval_h=FUNDING_INTERVAL_H,
                obs_ts_fast=observed_ts,
            )
        )
    return tuple(records)
