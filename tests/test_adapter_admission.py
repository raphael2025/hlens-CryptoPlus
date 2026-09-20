"""The proof that seam ③ holds without a new edge — checked statically.

This is the one test file that is type-checked together with the production
code (``[tool.mypy] files``), because what it asserts is a *typing* fact:
``hlens_core.ratelimit.RateLimitLedger`` already satisfies the
``SpendAuthority`` protocol that ``hlens_core.adapters`` declares, with no
change to either package and no import between them.

It has to be here rather than in ``packages/``: proving it inside the adapters
package would mean importing ``ratelimit`` there, which is the seam-③ violation
the whole design exists to avoid. ``tests/`` is outside the boundary test's
scan (it walks ``packages/`` only) and has a TID251 per-file-ignore, so a test
is the only legal place in the repository that may name both modules.

An ``isinstance`` check would not do: a ``runtime_checkable`` protocol compares
method *names*. The mismatch that matters here is in the *types* — the ledger's
``priority`` parameter is a ``Priority``, which is narrower than ``str`` — and
only a type checker sees it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from pathlib import Path

import pytest

from hlens_core.adapters import (
    M4_CAPABILITIES,
    VENUE_MUST_DECLARE_UNSUPPORTED,
    AdapterVenue,
    Admission,
    AnyAdmission,
    CallCost,
    Capability,
    CapabilityDeclaration,
    CapabilitySet,
    Completeness,
    KlineInterval,
    LanePriority,
    LiquidationCapabilityDeclaration,
    MarketCapabilityDeclaration,
    MarketDataAdapter,
    Mode,
    NormalizedRecord,
    SpendAuthority,
    StreamPlan,
    Support,
    SymbolMap,
    SymbolMapping,
    SymbolTable,
    unsupported,
)
from hlens_core.contracts import (
    InstrumentRecord,
    LsRatioKind,
    LsRatioPoint,
    MarketRecord,
    Semantic,
    Venue,
)
from hlens_core.ratelimit import (
    FakeClock,
    Grant,
    LedgerConfig,
    Priority,
    RateLimitLedger,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
VENUES_PATH = REPO_ROOT / "config" / "venues.yaml"
CONSUMERS_PATH = REPO_ROOT / "config" / "egress-consumers.yaml"

#: Every bucket an adapter may charge, spelled the way ``config/venues.yaml``
#: spells it. The venue adapters (M1-A steps ⑤/⑥) quote these strings in their
#: cost tables; ``test_every_bucket_an_adapter_can_name_exists`` checks the
#: spelling against the real config so a typo is caught here and not by a
#: ``KeyError`` on the first live call.
ADAPTER_BUCKETS = ("binance:fapi_weight", "binance:futures_data", "hyperliquid:info_weight")


def _ledger() -> RateLimitLedger:
    return RateLimitLedger(LedgerConfig.load(VENUES_PATH, CONSUMERS_PATH), clock=FakeClock())


def _to_priority(lane: LanePriority) -> Priority:
    """The collector's one line of wiring, written out. It knows both
    vocabularies and no weights; the weights stay on the adapter."""
    return Priority(lane.value)


def test_the_ledger_already_satisfies_the_adapters_protocol() -> None:
    """The assignment *is* the test: mypy checks it, pytest runs it.

    If ``SpendAuthority.acquire`` declared ``priority: str`` this line would
    fail to type check — ``Priority`` is a ``StrEnum`` and therefore narrower
    than ``str``, and a protocol's parameters are checked contravariantly.
    """
    ledger = _ledger()
    authority: SpendAuthority[Priority, Grant] = ledger
    grant = authority.acquire("binance:fapi_weight", cost=10, priority=Priority.FAST_LANE)
    assert grant.granted
    authority.settle(grant, actual_cost=10, status=200)


def test_the_two_priority_vocabularies_cannot_drift() -> None:
    """``LanePriority`` and ``Priority`` are separate enums (seam ③) with equal
    values, and this is the only place allowed to know both. If a tier is
    renamed on either side, the collector's one-line translation would start
    raising at runtime — so it is asserted here instead."""
    assert {lane.value for lane in LanePriority} == {p.value for p in Priority}


def test_every_bucket_an_adapter_can_name_exists() -> None:
    config = LedgerConfig.load(VENUES_PATH, CONSUMERS_PATH)
    known = {str(key) for key in config.buckets}
    assert set(ADAPTER_BUCKETS) <= known


def test_an_adapter_spends_through_the_binder_without_importing_the_ledger() -> None:
    """End to end: a cost the adapter states, admitted by the real ledger."""
    ledger = _ledger()
    admission: Admission[Priority, Grant] = Admission(ledger, _to_priority)

    # The row a Binance adapter publishes for the market-wide premiumIndex call
    # (04 §2: weight 10 without a symbol), on the 30-second fast lane.
    cost = CallCost(
        bucket="binance:fapi_weight",
        weight=10,
        priority=LanePriority.FAST_LANE,
        endpoint="/fapi/v1/premiumIndex",
    )
    grant = admission.acquire(cost)
    assert grant.granted
    assert ledger.snapshot("binance:fapi_weight").used_fast_per_min == 10

    admission.settle(grant, actual_cost=10, status=200)
    assert ledger.snapshot("binance:fapi_weight").used_fast_per_min == 10

    # A call that was never sent gives its allowance back.
    second = admission.acquire(cost)
    admission.release(second)
    assert ledger.snapshot("binance:fapi_weight").used_fast_per_min == 10


def test_the_binder_forwards_a_refusal_instead_of_hiding_it() -> None:
    ledger = _ledger()
    admission: Admission[Priority, Grant] = Admission(ledger, _to_priority)
    absurd = CallCost(
        bucket="binance:futures_data",
        weight=10_000,
        priority=LanePriority.OPPORTUNISTIC,
        endpoint="/futures/data/topLongShortPositionRatio",
    )
    grant = admission.acquire(absurd)
    assert not grant.granted
    assert grant.reason is not None


def test_the_binder_reports_a_status_that_had_no_grant() -> None:
    """A stream handshake's 418 still has to reach the ledger: it bans the
    whole egress IP, which is shared with the legacy collector (03 §6.1)."""
    ledger = _ledger()
    admission: Admission[Priority, Grant] = Admission(ledger, _to_priority)
    admission.observe("binance:fapi_weight", status=418, retry_after_s=120)
    assert ledger.snapshot("binance:fapi_weight").halted
    assert ledger.snapshot("binance:futures_data").halted


def test_a_liquidation_capability_cannot_be_declared_full() -> None:
    """Demo ③, the static half.

    ``LiquidationCompleteness`` has no ``full`` member, so the argument below
    is an ``arg-type`` error. mypy runs in strict mode, which turns on
    ``warn_unused_ignores``: the moment ``Completeness.FULL`` becomes writable
    here, this ``# type: ignore`` stops being used and mypy fails. The runtime
    guard is only the second fence — see ``tests/test_adapter_capabilities.py``.
    """
    with pytest.raises(ValueError, match="lower_bound"):
        LiquidationCapabilityDeclaration(
            capability=Capability.LIQUIDATION_STREAM,
            supported=Support.SUPPORTED,
            mode=Mode.PUSH_STREAM,
            completeness=Completeness.FULL,  # type: ignore[arg-type]
            throttled_source=True,
        )


# --------------------------------------------------------------------------- #
# The other half of the proof: the protocol is satisfiable, structurally, by an
# object that inherits nothing — and one of its methods really does spend
# through the binder. No venue is implemented here and nothing opens a socket;
# this is a stand-in that exists to be type-checked.
# --------------------------------------------------------------------------- #
def _full_capability_set(venue: AdapterVenue) -> CapabilitySet:
    declarations: list[CapabilityDeclaration] = []
    for capability in Capability:
        if capability is Capability.LIQUIDATION_STREAM:
            if capability in VENUE_MUST_DECLARE_UNSUPPORTED[venue]:
                declarations.append(
                    unsupported(capability, note="no public exchange-wide stream (04 §8)")
                )
            else:
                declarations.append(
                    LiquidationCapabilityDeclaration(
                        capability=capability,
                        supported=Support.SUPPORTED,
                        mode=Mode.PUSH_STREAM,
                        completeness=Completeness.LOWER_BOUND,
                        throttled_source=True,
                        note="one order per symbol per second (04 §2)",
                    )
                )
        elif capability in M4_CAPABILITIES or capability in VENUE_MUST_DECLARE_UNSUPPORTED[venue]:
            declarations.append(unsupported(capability, note="not in this milestone"))
        else:
            declarations.append(
                MarketCapabilityDeclaration(
                    capability=capability,
                    supported=Support.SUPPORTED,
                    mode=Mode.POLL_SNAPSHOT,
                    completeness=Completeness.FULL,
                )
            )
    return CapabilitySet(venue=venue, declarations=tuple(declarations))


class _StandInAdapter:
    """Satisfies :class:`MarketDataAdapter` by shape alone — no base class."""

    def __init__(self) -> None:
        self._capabilities = _full_capability_set(Venue.BINANCE)
        self._symbols = SymbolTable.of(
            [
                SymbolMapping(
                    symbol="PEPE",
                    venue_symbol="1000PEPEUSDT",
                    funding_interval_h=8,
                    mult=Decimal(1000),
                )
            ]
        )

    @property
    def venue(self) -> Venue:
        return Venue.BINANCE

    @property
    def capabilities(self) -> CapabilitySet:
        return self._capabilities

    @property
    def symbols(self) -> SymbolMap:
        return self._symbols

    def cost_of(
        self,
        capability: Capability,
        *,
        symbols: int | None = None,
        rows: int | None = None,
    ) -> CallCost:
        self._capabilities.require(capability)
        if capability is Capability.LONG_SHORT_RATIO or capability is Capability.TAKER_RATIO:
            return CallCost(
                bucket="binance:futures_data",
                weight=1,
                priority=LanePriority.RESIDENT,
                lane="ratios",
            )
        return CallCost(
            bucket="binance:fapi_weight",
            weight=10 if symbols is None else symbols,
            priority=LanePriority.FAST_LANE,
        )

    def stream_plan(self, capability: Capability, *, symbols: int | None = None) -> StreamPlan:
        return StreamPlan(
            capability=capability,
            group="market",
            streams=1,
            priority=LanePriority.FAST_LANE,
        )

    async def fetch_instruments(
        self, *, admission: AnyAdmission
    ) -> tuple[InstrumentRecord, ...]:
        raise NotImplementedError

    async def fetch_mark_prices(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        """The only method with a body: it shows the spend/settle sequence."""
        count = None if symbols is None else len(symbols)
        cost = self.cost_of(Capability.MARK_PRICE, symbols=count)
        grant = admission.acquire(cost)
        if not grant:
            return ()
        record = MarketRecord(
            venue=Venue.BINANCE,
            symbol="PEPE",
            ts=1_700_000_000_000,
            ingest_ts=1_700_000_000_050,
            source="binance_rest_premium_index",
            semantic=Semantic.MARK_PRICE,
            mark=Decimal("0.000012345"),
            obs_ts_fast=1_700_000_000_000,
        )
        admission.settle(grant, actual_cost=cost.weight, status=200)
        return (record,)

    async def fetch_funding(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        raise NotImplementedError

    async def fetch_open_interest(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        raise NotImplementedError

    async def fetch_ticker_24h(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> tuple[MarketRecord, ...]:
        raise NotImplementedError

    async def fetch_ls_ratio(
        self,
        *,
        admission: AnyAdmission,
        kind: LsRatioKind,
        symbol: str,
        limit: int | None = None,
    ) -> tuple[LsRatioPoint, ...]:
        raise NotImplementedError

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
        raise NotImplementedError

    async def stream_mark_prices(
        self, *, admission: AnyAdmission, symbols: Sequence[str] | None = None
    ) -> AsyncIterator[MarketRecord]:
        for record in await self.fetch_mark_prices(admission=admission, symbols=symbols):
            yield record

    async def stream_liquidations(
        self, *, admission: AnyAdmission
    ) -> AsyncIterator[NormalizedRecord]:
        for record in await self.fetch_mark_prices(admission=admission):
            yield record


def test_a_plain_object_satisfies_the_adapter_protocol() -> None:
    """Structural, not nominal: ``_StandInAdapter`` inherits nothing."""
    adapter: MarketDataAdapter = _StandInAdapter()
    assert adapter.venue is Venue.BINANCE
    assert adapter.capabilities.supports(Capability.MARK_PRICE)


def test_a_contract_record_satisfies_the_seam_one_floor() -> None:
    """``NormalizedRecord`` restates seam ①'s five mandatory attributes, so
    every contract model already satisfies it — it is not a second hierarchy."""
    record: NormalizedRecord = MarketRecord(
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        ts=1_700_000_000_000,
        ingest_ts=1_700_000_000_010,
        source="hyperliquid_ws_all_mids",
        semantic=Semantic.MARK_PRICE,
        mark=Decimal("64000.5"),
        obs_ts_fast=1_700_000_000_000,
    )
    assert isinstance(record, NormalizedRecord)


@pytest.mark.asyncio
async def test_the_stand_in_adapter_spends_and_settles() -> None:
    ledger = _ledger()
    admission: Admission[Priority, Grant] = Admission(ledger, _to_priority)
    adapter = _StandInAdapter()

    records = await adapter.fetch_mark_prices(admission=admission)
    assert len(records) == 1
    assert records[0].mark == Decimal("0.000012345")
    # 04 §2: the market-wide premiumIndex call is weight 10.
    assert ledger.snapshot("binance:fapi_weight").used_fast_per_min == 10
