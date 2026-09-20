"""Hyperliquid's answer sheet: thirteen capabilities, three answers each (seam ②).

The rules being checked are not this adapter's. ``01`` §4.7 and ``03`` §2 fix
them, :mod:`hlens_core.adapters.capabilities` enforces them at construction,
and what is checked here is that Hyperliquid's sheet says what ``04`` says it
should — above all in the four places where the honest answer is **no**.
"""

from __future__ import annotations

import pytest

from hlens_core.adapters import (
    M4_CAPABILITIES,
    VENUE_MUST_DECLARE_UNSUPPORTED,
    Capability,
    CapabilityError,
    CapabilitySet,
    Completeness,
    MarketCapabilityDeclaration,
    Mode,
    Support,
    UnsupportedCapability,
)
from hlens_core.adapters.hyperliquid import (
    HYPERLIQUID_CAPABILITIES,
    hyperliquid_capabilities,
)
from hlens_core.contracts import Venue

#: The four this venue must refuse, and why. Written out here rather than
#: imported so that the test states the requirement instead of restating the
#: implementation.
MUST_REFUSE = {
    Capability.LONG_SHORT_RATIO: "04 §1: 多空比 仅 Binance",
    Capability.TAKER_RATIO: "04 §1: 主动买卖比 仅 Binance",
    Capability.LIQUIDATION_STREAM: "04 §8: no public exchange-wide feed",
}


def test_the_sheet_declares_every_capability_exactly_once() -> None:
    sheet = hyperliquid_capabilities()
    assert sheet.venue is Venue.HYPERLIQUID
    declared = [d.capability for d in sheet.declarations]
    assert len(declared) == len(set(declared)) == len(Capability) == 13


def test_the_three_answers_are_three_answers_on_every_row() -> None:
    """``supported`` / ``mode`` / ``completeness`` never collapse into one. A
    supported capability answers all three; an unsupported one answers the
    other two with ``not_applicable`` and nothing else."""
    for declaration in hyperliquid_capabilities().declarations:
        if declaration.supported is Support.SUPPORTED:
            assert declaration.mode is not Mode.NOT_APPLICABLE
            assert declaration.completeness is not Completeness.NOT_APPLICABLE
        else:
            assert declaration.mode is Mode.NOT_APPLICABLE
            assert declaration.completeness is Completeness.NOT_APPLICABLE
        assert declaration.note, f"{declaration.capability.value} has no citation"


def test_the_ratios_and_the_liquidation_stream_are_unsupported() -> None:
    """AGENTS §3.4 in as many words: "Hyperliquid publishes no long/short ratio
    and no usable public liquidation stream — declare both as unsupported
    rather than deriving a substitute"."""
    sheet = hyperliquid_capabilities()
    for capability, why in MUST_REFUSE.items():
        declaration = sheet[capability]
        assert declaration.supported is Support.UNSUPPORTED, why
        with pytest.raises(UnsupportedCapability):
            sheet.require(capability)


def test_a_substitute_cannot_be_declared_into_existence() -> None:
    """The refusal is structural, not a habit: building a sheet that claims one
    of the three raises at construction, so no later step can quietly turn a
    derived number into a venue capability."""
    assert VENUE_MUST_DECLARE_UNSUPPORTED[Venue.HYPERLIQUID] == frozenset(MUST_REFUSE)

    declarations = list(hyperliquid_capabilities().declarations)
    for index, declaration in enumerate(declarations):
        if declaration.capability is Capability.LONG_SHORT_RATIO:
            declarations[index] = MarketCapabilityDeclaration(
                capability=Capability.LONG_SHORT_RATIO,
                supported=Support.SUPPORTED,
                mode=Mode.POLL_SNAPSHOT,
                completeness=Completeness.FULL,
                note="derived from tracked wallets — exactly what §3.4 forbids",
            )
    with pytest.raises(CapabilityError, match="no substitute"):
        CapabilitySet(venue=Venue.HYPERLIQUID, declarations=tuple(declarations))


def test_the_liquidation_declaration_carries_no_throttle_claim() -> None:
    """A feed we do not have cannot be throttled. Binance's stream is a lower
    bound because the venue throttles a real feed; Hyperliquid has no feed, so
    ``lower_bound`` here would be a floor on a sample of wallets presented as a
    floor on the venue (``04`` §8)."""
    declaration = hyperliquid_capabilities()[Capability.LIQUIDATION_STREAM]
    assert declaration.supported is Support.UNSUPPORTED
    assert declaration.completeness is Completeness.NOT_APPLICABLE
    assert declaration.throttled_source is False


def test_the_three_m4_capabilities_are_unsupported_with_a_reason() -> None:
    sheet = hyperliquid_capabilities()
    assert sorted(c.value for c in M4_CAPABILITIES) == ["book_l2", "spot", "trade_stream"]
    for capability in sorted(M4_CAPABILITIES):
        declaration = sheet[capability]
        assert declaration.supported is Support.UNSUPPORTED
        assert declaration.note
        with pytest.raises(UnsupportedCapability):
            sheet.require(capability)


def test_exactly_six_capabilities_are_unsupported_on_this_venue() -> None:
    """Three more than Binance, and each of the three is a fact about the venue
    rather than a gap in this step."""
    sheet = hyperliquid_capabilities()
    unsupported = set(sheet.unsupported_capabilities())
    assert unsupported == set(MUST_REFUSE) | M4_CAPABILITIES
    assert len(unsupported) == 6


def test_klines_are_partial_history_and_nothing_else_is() -> None:
    """``04`` §5: ``candleSnapshot`` retains ~5000 bars — about 3.5 days at 1m.
    This is the only *supported* capability on either venue that is not
    ``full``, and F8's "数据积累中" hangs on it."""
    sheet = hyperliquid_capabilities()
    not_full = {
        d.capability
        for d in sheet.declarations
        if d.supported is Support.SUPPORTED and d.completeness is not Completeness.FULL
    }
    assert not_full == {Capability.KLINES}
    assert sheet[Capability.KLINES].completeness is Completeness.PARTIAL_HISTORY


def test_the_mark_price_stream_declaration_says_it_pushes_a_mid() -> None:
    """``04`` §3 defines ``allMids`` as 币→中间价 while ``04`` §1 names it as the
    push source for the mark column. The declaration is where a reader finds
    that out, which is the whole purpose of the note field."""
    declaration = hyperliquid_capabilities()[Capability.MARK_PRICE_STREAM]
    assert declaration.supported is Support.SUPPORTED
    assert declaration.mode is Mode.PUSH_STREAM
    assert declaration.note is not None
    assert "MID" in declaration.note or "mid" in declaration.note


def test_no_capability_on_this_sheet_is_a_wallet_one() -> None:
    """Seam ⑤. There is no wallet member in the enum at all, so this is a
    statement about the enum rather than about the sheet — which is the
    strongest form it could take."""
    banned = ("wallet", "user", "position", "fill", "clearinghouse", "address")
    for declaration in hyperliquid_capabilities().declarations:
        assert not any(word in declaration.capability.value for word in banned)


def test_the_module_level_sheet_matches_a_freshly_built_one() -> None:
    assert HYPERLIQUID_CAPABILITIES.declarations == hyperliquid_capabilities().declarations
