"""Binance's answer sheet: thirteen capabilities, three answers each (seam ②).

The rules being checked are not this adapter's: ``01`` §4.7 and ``03`` §2 fix
them, ``hlens_core.adapters.capabilities`` enforces them at construction, and
what is checked here is that Binance's sheet says what ``04`` says it should.
"""

from __future__ import annotations

import pytest

from hlens_core.adapters import (
    M4_CAPABILITIES,
    Capability,
    Completeness,
    LiquidationCapabilityDeclaration,
    Mode,
    Support,
    UnsupportedCapability,
)
from hlens_core.adapters.binance import BINANCE_CAPABILITIES, binance_capabilities
from hlens_core.contracts import Venue


def test_the_sheet_declares_every_capability_exactly_once() -> None:
    sheet = binance_capabilities()
    assert sheet.venue is Venue.BINANCE
    declared = [d.capability for d in sheet.declarations]
    assert len(declared) == len(set(declared)) == len(Capability) == 13


def test_the_three_m4_capabilities_are_unsupported_with_a_reason() -> None:
    """``03`` §2 seam ②: "枚举里现在就有 … 两所都标 unsupported，M4 只补实现、
    不改协议". A note is required, because an ``unsupported`` without one is how
    a substitute gets derived later by somebody who assumed it was an oversight.
    """
    sheet = binance_capabilities()
    assert sorted(c.value for c in M4_CAPABILITIES) == ["book_l2", "spot", "trade_stream"]
    for capability in sorted(M4_CAPABILITIES):
        declaration = sheet[capability]
        assert declaration.supported is Support.UNSUPPORTED
        assert declaration.mode is Mode.NOT_APPLICABLE
        assert declaration.completeness is Completeness.NOT_APPLICABLE
        assert declaration.note
        with pytest.raises(UnsupportedCapability):
            sheet.require(capability)


def test_the_liquidation_stream_is_a_throttled_lower_bound_forever() -> None:
    """``04`` §2: ``!forceOrder@arr`` pushes at most one order per symbol per
    second, so what arrives is a floor — and ``01`` §4.7 makes that the only
    honest completeness. F12's ``下界`` label hangs on this declaration."""
    declaration = binance_capabilities()[Capability.LIQUIDATION_STREAM]
    assert isinstance(declaration, LiquidationCapabilityDeclaration)
    assert declaration.supported is Support.SUPPORTED
    assert declaration.mode is Mode.PUSH_STREAM
    assert declaration.completeness is Completeness.LOWER_BOUND
    assert declaration.throttled_source is True


def test_everything_binance_publishes_is_supported_with_all_three_answers() -> None:
    sheet = binance_capabilities()
    published = [c for c in Capability if c not in M4_CAPABILITIES]
    assert len(published) == 10
    for capability in published:
        declaration = sheet.require(capability)
        assert declaration.mode is not Mode.NOT_APPLICABLE
        assert declaration.completeness is not Completeness.NOT_APPLICABLE
        assert declaration.note, f"{capability.value} has no citation"


def test_only_the_liquidation_stream_is_not_full() -> None:
    """Every other Binance capability really is complete: the market-wide
    endpoints cover every coin in one call and the per-coin ones are asked for
    every coin. Saying ``full`` about them and ``lower_bound`` about the
    liquidation feed is the difference the three answers exist to keep."""
    sheet = binance_capabilities()
    not_full = {
        d.capability
        for d in sheet.declarations
        if d.supported is Support.SUPPORTED and d.completeness is not Completeness.FULL
    }
    assert not_full == {Capability.LIQUIDATION_STREAM}


def test_the_ratio_capabilities_are_polled_history_not_snapshots() -> None:
    """决定 A8 polls every 10 minutes with ``limit`` so both of the window's
    5-minute points come back (``03`` §6, ``04`` §4). A "snapshot" would hide
    the one thing that keeps the stored grid at 5 minutes."""
    sheet = binance_capabilities()
    for capability in (Capability.LONG_SHORT_RATIO, Capability.TAKER_RATIO):
        assert sheet[capability].mode is Mode.POLL_HISTORY


def test_the_module_level_sheet_matches_a_freshly_built_one() -> None:
    assert BINANCE_CAPABILITIES.declarations == binance_capabilities().declarations
