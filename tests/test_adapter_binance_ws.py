"""The two WebSocket streams, offline, against trimmed fixtures.

Both are on the ``/market`` group. ``04`` §2's change notice is the reason that
matters: the legacy combined-stream endpoint stopped pushing **permanently** on
2026-04-23, and the failure mode of connecting to it is a socket that opens and
then says nothing — which looks like a quiet market, not like an error.

The connector is injected, so nothing here opens a socket. That is not only a
testing convenience: the development machine shares its public egress with the
production host and a still-running legacy collector (``04`` §6), so a fixture
recorded here would not count (AGENTS §3.3) and a request sent here spends
somebody else's budget.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import fields
from decimal import Decimal
from typing import Any

import httpx
import pytest

from conftest import VENUES_PATH, binance_payload
from hlens_core.adapters import Admission, Capability, SymbolTable
from hlens_core.adapters.base import NormalizedRecord
from hlens_core.adapters.binance import (
    WS_FORCE_ORDER_ALL,
    WS_MARK_PRICE_ALL,
    BinanceEndpoints,
    BinanceMarketDataAdapter,
    ForcedOrderObservation,
    normalize_exchange_info,
    normalize_funding_info,
    symbol_table_of,
)
from hlens_core.contracts import Semantic, Venue
from hlens_core.ratelimit import Grant, Priority, RateLimitLedger

T0 = 1_789_819_200_000
INGEST_TS = T0 + 123
ENDPOINTS = BinanceEndpoints.load(VENUES_PATH)


def _symbols() -> SymbolTable:
    intervals = normalize_funding_info(binance_payload("funding_info"))
    return symbol_table_of(
        normalize_exchange_info(
            binance_payload("exchange_info"), ingest_ts=INGEST_TS, funding_interval_h=intervals
        )
    )


class _Replay:
    """A recorded connection: the frames of a fixture, once, in order."""

    def __init__(self, frames: list[Any]) -> None:
        self.frames = frames

    async def _iterate(self) -> AsyncIterator[str]:
        for frame in self.frames:
            yield json.dumps(frame)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iterate()


def _connector(frames: list[Any], seen: list[str]) -> Any:
    @asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_Replay]:
        seen.append(url)
        yield _Replay(frames)

    return connect


class _HandshakeRefused(Exception):
    """What ``websockets`` raises shape-wise: the failure carries a response."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = type("Response", (), {"status_code": status})()


def _adapter(frames: list[Any], seen: list[str]) -> BinanceMarketDataAdapter:
    return BinanceMarketDataAdapter(
        client=httpx.AsyncClient(),
        endpoints=ENDPOINTS,
        symbols=_symbols(),
        clock=lambda: INGEST_TS,
        connector=_connector(frames, seen),
    )


@pytest.fixture
def admission(ledger: RateLimitLedger) -> Admission[Priority, Grant]:
    return Admission(ledger, lambda lane: Priority(lane.value))


# --------------------------------------------------------------------------- #
# !markPrice@arr@1s
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_mark_price_stream_connects_to_the_market_group(
    admission: Admission[Priority, Grant],
) -> None:
    seen: list[str] = []
    adapter = _adapter(binance_payload("ws_mark_price_arr"), seen)

    records = [r async for r in adapter.stream_mark_prices(admission=admission)]

    assert len(seen) == 1
    assert seen[0].startswith(ENDPOINTS.ws_market)
    assert WS_MARK_PRICE_ALL in seen[0]
    assert ENDPOINTS.ws_public not in seen[0]
    assert len(records) == 3


@pytest.mark.asyncio
async def test_mark_price_frames_normalize_into_fast_lane_records(
    admission: Admission[Priority, Grant],
) -> None:
    """Stream field names, not the REST ones (``04`` §2): ``s`` symbol · ``p``
    mark · ``i`` index · ``r`` funding rate · ``T`` next funding · ``E`` event
    time."""
    adapter = _adapter(binance_payload("ws_mark_price_arr"), [])
    records = [r async for r in adapter.stream_mark_prices(admission=admission)]

    btc = records[0]
    assert btc.venue is Venue.BINANCE and btc.symbol == "BTC"
    assert btc.mark == Decimal("64210.30000000")
    assert btc.index_px == Decimal("64198.74318182")
    assert btc.funding_rate == Decimal("0.00010000")
    assert btc.funding_interval_h == 8
    assert btc.next_funding_ts == T0 + 4 * 3_600_000
    assert btc.semantic is Semantic.MARK_PRICE
    # The observation instant, untouched — no bucketing anywhere in the adapter.
    assert btc.ts == T0 and btc.obs_ts_fast == T0
    assert btc.ingest_ts == INGEST_TS
    assert btc.obs_ts_slow is None

    pepe = records[1]
    assert pepe.symbol == "PEPE"
    assert pepe.funding_interval_h == 4
    assert pepe.funding_rate_8h == Decimal("-0.00005000") * 8 / 4


@pytest.mark.asyncio
async def test_a_bare_frame_is_accepted_as_well_as_the_wrapped_one(
    admission: Admission[Priority, Grant],
) -> None:
    """``04`` §11 第 9 项 leaves the post-split connection rules ``未验证``: the
    change notice gives the group base URLs and does not say whether the
    ``{stream, data}`` envelope survived. Both shapes are accepted rather than
    one being assumed right until ``M1-G`` records the real thing."""
    wrapped = binance_payload("ws_mark_price_arr")
    bare = [frame["data"] for frame in wrapped]
    adapter = _adapter(bare, [])
    records = [r async for r in adapter.stream_mark_prices(admission=admission)]
    assert [r.symbol for r in records] == ["BTC", "PEPE", "BTC"]


@pytest.mark.asyncio
async def test_a_per_symbol_subscription_names_one_stream_per_coin(
    admission: Admission[Priority, Grant],
) -> None:
    seen: list[str] = []
    adapter = _adapter([], seen)
    _ = [r async for r in adapter.stream_mark_prices(admission=admission, symbols=["PEPE"])]
    assert "1000pepeusdt@markPrice@1s" in seen[0]
    assert WS_MARK_PRICE_ALL not in seen[0]


# --------------------------------------------------------------------------- #
# !forceOrder@arr
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_liquidation_stream_yields_only_seam_ones_floor(
    admission: Admission[Priority, Grant],
) -> None:
    """M1 has no liquidation contract (M1-A1 left it to M2), so
    ``adapters/base.py`` types this stream as ``NormalizedRecord`` — "the five
    attributes seam ① requires … and nothing more: no invented columns, no raw
    JSON". ``side`` / ``price`` / ``size`` / ``notional_usd`` are ``03`` §5's
    ``liquidations`` columns and M2 owns them.
    """
    seen: list[str] = []
    adapter = _adapter(binance_payload("ws_force_order"), seen)

    records = [r async for r in adapter.stream_liquidations(admission=admission)]

    assert seen[0].startswith(ENDPOINTS.ws_market)
    assert WS_FORCE_ORDER_ALL in seen[0]
    # The third frame is a coin the mapping does not know: skipped and counted.
    assert [r.symbol for r in records] == ["BTC", "PEPE"]
    assert adapter.unknown_venue_symbols == ("NEWCOINUSDT",)

    first = records[0]
    assert isinstance(first, ForcedOrderObservation)
    assert isinstance(first, NormalizedRecord)
    assert first.venue is Venue.BINANCE
    # o.T, the order's own trade time — not E, which is when Binance decided
    # to tell us.
    assert first.ts == T0
    assert first.ingest_ts == INGEST_TS
    assert first.source == "binance_ws_force_order"
    assert {f.name for f in fields(first)} == {"venue", "symbol", "ts", "ingest_ts", "source"}


@pytest.mark.asyncio
async def test_the_liquidation_stream_is_declared_a_throttled_lower_bound(
    admission: Admission[Priority, Grant],
) -> None:
    """The stream itself cannot fix the throttle and must not pretend to: what
    makes the number honest is the declaration F12 reads (``01`` §4.7)."""
    adapter = _adapter([], [])
    declaration = adapter.capabilities[Capability.LIQUIDATION_STREAM]
    assert declaration.completeness.value == "lower_bound"
    plan = adapter.stream_plan(Capability.LIQUIDATION_STREAM)
    assert plan.group == "market" and plan.streams == 1


# --------------------------------------------------------------------------- #
# Handshakes
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_handshake_418_reaches_the_ledger_before_it_reaches_the_caller(
    admission: Admission[Priority, Grant], ledger: RateLimitLedger
) -> None:
    """``03`` §6.1: a 418 bans the whole egress IP and takes the legacy
    collector down with it, so a reconnect loop must never swallow it."""

    def refusing(url: str) -> Any:
        raise _HandshakeRefused(418)

    adapter = BinanceMarketDataAdapter(
        client=httpx.AsyncClient(),
        endpoints=ENDPOINTS,
        symbols=_symbols(),
        clock=lambda: INGEST_TS,
        connector=refusing,
    )

    with pytest.raises(_HandshakeRefused):
        _ = [r async for r in adapter.stream_mark_prices(admission=admission)]

    # The venue is halted: every lane of it, not just this stream.
    assert ledger.snapshot("binance:fapi_weight").halted


@pytest.mark.asyncio
async def test_a_handshake_failure_without_a_status_is_re_raised_untouched(
    admission: Admission[Priority, Grant], ledger: RateLimitLedger
) -> None:
    """A DNS failure is not a rate-limit signal and must not be reported as one;
    inventing a status here would freeze lanes for a cable somebody unplugged."""

    def broken(url: str) -> Any:
        raise OSError("name resolution failed")

    adapter = BinanceMarketDataAdapter(
        client=httpx.AsyncClient(),
        endpoints=ENDPOINTS,
        symbols=_symbols(),
        clock=lambda: INGEST_TS,
        connector=broken,
    )
    with pytest.raises(OSError, match="name resolution"):
        _ = [r async for r in adapter.stream_mark_prices(admission=admission)]
    assert not ledger.snapshot("binance:fapi_weight").halted
