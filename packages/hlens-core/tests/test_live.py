"""Live smoke tests. Deselected by default; run with ``uv run pytest -q -m live``.

Cost discipline: the whole module spends ~60 weight of Binance's 2400/min ceiling
(2.5%), far under the 40% budget rule.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from hlens_core.adapters import BinanceFutures
from hlens_core.contracts import InstrumentStatus, LSKind
from hlens_core.preflight import run_preflight

pytestmark = pytest.mark.live


@pytest.fixture
async def live():
    a = BinanceFutures(egress_ip="live")
    try:
        yield a
    finally:
        await a.aclose()


async def test_live_preflight_binance():
    report = await run_preflight(["binance"])
    assert report.egress.ip, "no egress IP: every budget is keyed on it"
    reach = next(c for c in report.checks if c.kind.value == "reachability")
    assert reach.ok, f"binance unreachable from this egress: {reach.detail}"
    print("\n" + report.to_table())


async def test_live_discover_instruments(live):
    instruments = await live.discover_instruments()
    assert len(instruments) > 100
    btc = next(i for i in instruments if i.symbol == "BTC")
    assert btc.status is InstrumentStatus.trading
    assert btc.tick and btc.tick > 0
    assert btc.funding_interval_h and btc.funding_interval_h > 0
    # the multiplier rule holds against the real symbol list
    for i in instruments:
        if i.venue_symbol.startswith("1000"):
            assert i.mult >= 1000 and not i.symbol.startswith("1000")


async def test_live_marks_and_funding_agree_with_the_ledger(live):
    marks, fundings = await live.fetch_premium_index()
    assert len(marks) > 100 and len(fundings) > 100
    btc = next(m for m in marks if m.symbol == "BTC")
    assert btc.mark > 0 and btc.age_ms < 120_000
    f = next(f for f in fundings if f.symbol == "BTC")
    assert abs(f.rate) < Decimal("0.01")
    assert f.rate_8h is not None
    # the response header should have re-synced the ledger to the venue's own count
    assert live.budget.used() >= 10
    assert live.budget.stats.header_syncs >= 1


async def test_live_open_interest_and_ratios(live):
    await live.discover_instruments()
    marks, _ = await live.fetch_premium_index(["BTC"])
    (oi,) = await live.fetch_open_interest(["BTC"], marks={"BTC": marks[0].mark})
    assert oi.oi_base > 0 and oi.oi_usd > 0

    rows = await live.fetch_long_short_ratios("BTC", limit=1)
    assert {r.kind for r in rows} == set(LSKind)
    assert all(0 < r.long_share < 1 for r in rows)


async def test_live_klines_have_taker_buy_volume(live):
    candles = await live.fetch_klines("BTC", "1h", limit=5)
    assert len(candles) == 5
    assert all(c.taker_buy_v is not None and c.taker_buy_v <= c.v for c in candles)
    assert candles[-1].closed is False or candles[-1].close_ts <= candles[-1].ingest_ts


async def test_live_ticker_24h(live):
    tickers = await live.fetch_ticker_24h()
    assert len(tickers) > 100
    btc = next(t for t in tickers if t.symbol == "BTC")
    assert btc.vol24h_usd and btc.vol24h_usd > 0


async def test_live_healthcheck(live):
    health = {h.capability: h for h in await live.healthcheck()}
    assert health["reachability"].ok
    assert health["reachability"].latency_ms > 0
    print("\nclock:", health["clock"].latency_ms, "ms skew")


@pytest.mark.xfail(
    reason="fstream.binance.com ACKs SUBSCRIBE but sends no data frames from the "
    "ET/AS24757 egress (2026-09-12); other venues' WS work from the same host",
    strict=False,
)
async def test_live_mark_price_stream(live):
    import asyncio

    stream = live.stream_mark_price(["BTC"])
    try:
        async with asyncio.timeout(20):
            async for mark in stream:
                assert mark.mark > 0
                break
    finally:
        await stream.aclose()
