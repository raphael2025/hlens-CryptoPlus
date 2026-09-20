"""The one WebSocket stream, offline, against a trimmed fixture — and the one
that does not exist.

The connector is injected, so nothing here opens a socket. That is not only a
testing convenience: this development machine shares its public egress with the
production host **and a still-running collector of this same venue** (``04``
§6, ``03`` §6.1), so a fixture recorded here would not count (AGENTS §3.3) and a
connection opened here would take one of a zero-sum pool of seats.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import httpx
import pytest

from conftest import VENUES_PATH, hyperliquid_payload
from hlens_core.adapters import Admission, Capability, SymbolTable, UnsupportedCapability
from hlens_core.adapters.hyperliquid import (
    SOURCE_META_AND_ASSET_CTXS,
    SOURCE_WS_ALL_MIDS,
    HyperliquidEndpoints,
    HyperliquidMarketDataAdapter,
    normalize_meta,
    symbol_table_of,
)
from hlens_core.contracts import Semantic, Venue
from hlens_core.ratelimit import Grant, HlBodyKind, Priority, RateLimitLedger, classify_hl_429_body

T0 = 1_789_819_200_000
ENDPOINTS = HyperliquidEndpoints.load(VENUES_PATH)


def _symbols() -> SymbolTable:
    return symbol_table_of(
        normalize_meta(hyperliquid_payload("meta"), ingest_ts=T0, observed_ts=T0)
    )


class _Replay:
    """A recorded connection: the frames of a fixture, once, in order — and a
    ``send`` that records what was subscribed to."""

    def __init__(self, frames: list[Any], sent: list[str]) -> None:
        self.frames = frames
        self.sent = sent

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def _iterate(self) -> AsyncIterator[str]:
        for frame in self.frames:
            yield json.dumps(frame)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iterate()


def _connector(frames: list[Any], seen: list[str], sent: list[str]) -> Any:
    @asynccontextmanager
    async def connect(url: str) -> AsyncIterator[_Replay]:
        seen.append(url)
        yield _Replay(frames, sent)

    return connect


class _HandshakeRefused(Exception):
    """What ``websockets`` raises shape-wise: the failure carries a response,
    and on this venue that response's **body** is the evidence."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}")
        self.response = type("Response", (), {"status_code": status, "body": body})()


def _adapter(
    frames: list[Any], seen: list[str], sent: list[str]
) -> HyperliquidMarketDataAdapter:
    return HyperliquidMarketDataAdapter(
        client=httpx.AsyncClient(),
        endpoints=ENDPOINTS,
        symbols=_symbols(),
        clock=lambda: T0,
        connector=_connector(frames, seen, sent),
    )


@pytest.fixture
def admission(ledger: RateLimitLedger) -> Admission[Priority, Grant]:
    return Admission(ledger, lambda lane: Priority(lane.value))


# --------------------------------------------------------------------------- #
# allMids
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_stream_connects_once_and_subscribes_by_sending_a_frame(
    admission: Admission[Priority, Grant],
) -> None:
    """Binance names its streams in the URL; Hyperliquid connects to one
    address and then sends this (``04`` §3). The difference is why this
    adapter's WebSocket protocol has a ``send``."""
    seen: list[str] = []
    sent: list[str] = []
    adapter = _adapter(hyperliquid_payload("ws_all_mids"), seen, sent)

    records = [r async for r in adapter.stream_mark_prices(admission=admission)]

    assert seen == [ENDPOINTS.ws]
    assert json.loads(sent[0]) == {
        "method": "subscribe",
        "subscription": {"type": "allMids"},
    }
    # Two data frames: 3 mids (one unknown coin) + 2 mids.
    assert len(records) == 4


@pytest.mark.asyncio
async def test_frames_from_other_channels_are_skipped(
    admission: Admission[Priority, Grant],
) -> None:
    """One socket carries every channel on this venue, so "is this mine" is a
    question every reader has to ask. The fixture's first frame is the
    subscription acknowledgement."""
    adapter = _adapter(hyperliquid_payload("ws_all_mids"), [], [])
    records = [r async for r in adapter.stream_mark_prices(admission=admission)]
    assert [r.symbol for r in records] == ["BTC", "PEPE", "BTC", "PEPE"]


@pytest.mark.asyncio
async def test_the_pushed_price_is_tagged_as_a_mid_rather_than_relabelled(
    admission: Admission[Priority, Grant],
) -> None:
    """``04`` §3 defines ``allMids`` as 币→中间价 — the **mid**, not the venue's
    mark — while ``04`` §1 names this channel as the push source for the mark
    column and ``03`` §6's fast lane polls ``metaAndAssetCtxs`` for the real
    ``markPx``.

    So the architecture's wiring is followed and the basis is made visible
    instead of substituted: these rows carry a ``source`` of their own, which
    is what tells F7's cross-venue mark spread to use the REST rows. Raised in
    the PR's "Doc corrections" rather than resolved here — which column a
    venue's mid belongs in is a data-model question.
    """
    adapter = _adapter(hyperliquid_payload("ws_all_mids"), [], [])
    records = [r async for r in adapter.stream_mark_prices(admission=admission)]

    assert all(r.source == SOURCE_WS_ALL_MIDS for r in records)
    assert SOURCE_WS_ALL_MIDS != SOURCE_META_AND_ASSET_CTXS

    declaration = adapter.capabilities[Capability.MARK_PRICE_STREAM]
    assert declaration.note is not None and "mid" in declaration.note.lower()


@pytest.mark.asyncio
async def test_mids_normalize_into_fast_lane_records(
    admission: Admission[Priority, Grant],
) -> None:
    adapter = _adapter(hyperliquid_payload("ws_all_mids"), [], [])
    records = [r async for r in adapter.stream_mark_prices(admission=admission)]

    btc = records[0]
    assert btc.venue is Venue.HYPERLIQUID and btc.symbol == "BTC"
    assert btc.mark == Decimal("64209.5")
    assert btc.funding_interval_h == 1
    assert btc.semantic is Semantic.MARK_PRICE
    # The frame carries no timestamp of its own, so ts is the receipt instant —
    # the observation instant, and still not a bucket.
    assert btc.ts == T0 and btc.obs_ts_fast == T0
    assert btc.ingest_ts == T0
    # Slow-lane fields belong to the other statement.
    assert btc.obs_ts_slow is None and btc.oi_base is None

    pepe = records[1]
    assert pepe.symbol == "PEPE"  # kPEPE, mapped
    assert pepe.mark == Decimal("0.0098115")


@pytest.mark.asyncio
async def test_an_unknown_coin_on_the_stream_is_skipped_and_counted(
    admission: Admission[Priority, Grant],
) -> None:
    adapter = _adapter(hyperliquid_payload("ws_all_mids"), [], [])
    records = [r async for r in adapter.stream_mark_prices(admission=admission)]
    assert all(r.symbol != "NEWCOIN" for r in records)
    assert adapter.unknown_venue_symbols == ("NEWCOIN",)


@pytest.mark.asyncio
async def test_there_is_no_per_symbol_subscription(
    admission: Admission[Priority, Grant],
) -> None:
    adapter = _adapter([], [], [])
    with pytest.raises(ValueError, match="whole market in one subscription"):
        adapter.stream_mark_prices(admission=admission, symbols=["BTC"])


@pytest.mark.asyncio
async def test_the_stream_plan_asks_for_exactly_one_seat(
    admission: Admission[Priority, Grant],
) -> None:
    """``04`` §4: connections, subscriptions and the ten ``user`` seats are
    counted per egress IP and are **zero-sum** with the legacy collector.
    ``03`` §6.1's table budgets 1 subscription for M1/M2."""
    adapter = _adapter([], [], [])
    plan = adapter.stream_plan(Capability.MARK_PRICE_STREAM)
    assert plan.group == "info" and plan.streams == 1


# --------------------------------------------------------------------------- #
# The liquidation stream that does not exist
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_liquidation_stream_refuses_before_opening_anything(
    admission: Admission[Priority, Grant],
) -> None:
    """``04`` §8, measured: the ``trades`` channel carries **no liquidation
    field**, and forced fills appear only on the per-wallet ``userFills``
    endpoints — seam ⑤ and M5.

    The refusal is immediate rather than on first iteration, because a caller
    that had to await an item before finding out would already have spent one
    of a zero-sum pool of connection seats.
    """
    seen: list[str] = []
    adapter = _adapter([], seen, [])

    with pytest.raises(UnsupportedCapability, match="wallet sampling"):
        adapter.stream_liquidations(admission=admission)
    assert seen == []


def test_nothing_in_this_adapter_derives_a_liquidation_lower_bound() -> None:
    """Binance's stream is a lower bound because the venue throttles a real
    feed. A sample of some wallets' fills is not a floor on the venue — it is a
    floor on our sample, and F12 would print it beside a venue's name. ``04``
    §8 puts the hub dataset's historical capture at 1.6–8 %.

    What is checked is the set of **request names this package can send** — a
    Hyperliquid endpoint is a ``type`` string, so the endpoints it can reach
    are exactly the bare camelCase string literals in it. Prose is excluded on
    purpose: the docstrings and the capability notes name ``userFills`` and
    ``l2Book`` repeatedly, and saying *why* something is absent is the opposite
    of reaching for it.
    """
    import ast
    import re
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / (
        "packages/hlens-core/src/hlens_core/adapters/hyperliquid"
    )
    #: Every `type` this package is allowed to send, and the one ws channel.
    #: M4's (`l2Book`, `trades`) and seam ⑤'s (`userFills`, `clearinghouseState`,
    #: `userFunding`, `userFillsByTime`) are absent because they are absent.
    must_be_present = {
        "meta",
        "metaAndAssetCtxs",
        "predictedFundings",
        "fundingHistory",
        "candleSnapshot",
        "allMids",
    }
    #: seam ⑤'s endpoints and M4's, which this package must not be able to send.
    #: ``userFills`` is the specific temptation: ``04`` §8 says it is the only
    #: public place a Hyperliquid forced fill is visible at all.
    must_be_absent = {
        "userFills",
        "userFillsByTime",
        "userFunding",
        "userNonFundingLedgerUpdates",
        "userEvents",
        "clearinghouseState",
        "spotClearinghouseState",
        "l2Book",
        "trades",
        "spotMeta",
        "spotMetaAndAssetCtxs",
    }
    token = re.compile(r"[a-z][A-Za-z0-9]*")

    names: set[str] = set()
    for source in package.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and token.fullmatch(node.value)
            ):
                names.add(node.value)

    reachable = names & must_be_absent
    assert not reachable, f"this package can send {sorted(reachable)}"
    # And the check cannot pass on nothing: the five it *is* allowed to send,
    # plus the one channel, all have to be in there.
    assert must_be_present <= names


# --------------------------------------------------------------------------- #
# Handshakes
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_handshake_429_reaches_the_ledger_with_its_body(
    admission: Admission[Priority, Grant], ledger: RateLimitLedger
) -> None:
    """``04`` §3: an nginx HTML page on a 429 means the **connection-rate**
    limiter, not the weight limiter — the remedy is fewer connections, and a
    reconnect loop that swallowed it would do the opposite. The body travels
    verbatim; the adapter classifies nothing."""
    page = "<html><head><title>429 Too Many Requests</title></head></html>"

    def refusing(url: str) -> Any:
        raise _HandshakeRefused(429, page)

    adapter = HyperliquidMarketDataAdapter(
        client=httpx.AsyncClient(),
        endpoints=ENDPOINTS,
        symbols=_symbols(),
        clock=lambda: T0,
        connector=refusing,
    )

    with pytest.raises(_HandshakeRefused):
        _ = [r async for r in adapter.stream_mark_prices(admission=admission)]

    assert classify_hl_429_body(page) is HlBodyKind.CONNECTION
    # 03 §6.1: any HL 429 also has to wake somebody — on a shared egress it
    # means another consumer may be getting hit too. That is the ledger's job,
    # and this is where the adapter hands it the evidence to do it with.
    snapshot = ledger.snapshot("hyperliquid:info_weight")
    assert snapshot.opportunistic_frozen


@pytest.mark.asyncio
async def test_a_handshake_failure_without_a_status_is_re_raised_untouched(
    admission: Admission[Priority, Grant], ledger: RateLimitLedger
) -> None:
    """A DNS failure is not a rate-limit signal and must not be reported as
    one; inventing a status here would freeze lanes for a cable somebody
    unplugged."""

    def broken(url: str) -> Any:
        raise OSError("name resolution failed")

    adapter = HyperliquidMarketDataAdapter(
        client=httpx.AsyncClient(),
        endpoints=ENDPOINTS,
        symbols=_symbols(),
        clock=lambda: T0,
        connector=broken,
    )
    with pytest.raises(OSError, match="name resolution"):
        _ = [r async for r in adapter.stream_mark_prices(admission=admission)]
    snapshot = ledger.snapshot("hyperliquid:info_weight")
    assert not snapshot.halted and not snapshot.opportunistic_frozen
