"""Seam ② — the capability declaration refuses what the documents refuse.

Three answers, never merged (``01`` §4.7); ``trade_stream`` ``book_l2``
``spot`` already in the enum and unsupported on both venues until M4
(``03`` §2 seam ②); Hyperliquid's missing ratios and missing liquidation stream
declared rather than derived (AGENTS §3.4, ``04`` §1/§8); and a liquidation
completeness that cannot be ``full`` — at type level first, and here at runtime
as the second fence.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest

from hlens_core.adapters import (
    LIQUIDATION_CAPABILITIES,
    M4_CAPABILITIES,
    VENUE_MUST_DECLARE_UNSUPPORTED,
    Capability,
    CapabilityError,
    CapabilitySet,
    Completeness,
    LiquidationCapabilityDeclaration,
    LiquidationCompleteness,
    MarketCapabilityDeclaration,
    Mode,
    Support,
    UnsupportedCapability,
    capability_of_ls_ratio_kind,
    unsupported,
)
from hlens_core.contracts import LsRatioKind, Venue
from hlens_core.wallet import WalletCapability

VENUES = (Venue.BINANCE, Venue.HYPERLIQUID)


def declarations_for(venue: Any, **overrides: Any) -> tuple[Any, ...]:
    """A complete, legal declaration sheet, with room to break one entry.

    Built from the rules rather than from a venue's real endpoint list: no
    venue is implemented in this step, and these tests are about what any
    adapter may declare, not about what Binance happens to publish.
    """
    declarations = []
    for capability in Capability:
        if capability in overrides:
            declarations.append(overrides[capability])
        elif capability in LIQUIDATION_CAPABILITIES:
            if capability in VENUE_MUST_DECLARE_UNSUPPORTED[venue]:
                declarations.append(unsupported(capability, note="venue publishes none"))
            else:
                declarations.append(
                    LiquidationCapabilityDeclaration(
                        capability=capability,
                        supported=Support.SUPPORTED,
                        mode=Mode.PUSH_STREAM,
                        completeness=Completeness.LOWER_BOUND,
                        throttled_source=True,
                        note="one order per symbol per second",
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
    return tuple(declarations)


# --------------------------------------------------------------------------- #
# The enum
# --------------------------------------------------------------------------- #
def test_the_m4_capabilities_are_already_in_the_enum() -> None:
    """Seam ②: "枚举里现在就有 trade_stream book_l2 spot"."""
    assert {"trade_stream", "book_l2", "spot"} <= {c.value for c in Capability}


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("capability", sorted(M4_CAPABILITIES))
def test_both_venues_declare_the_m4_capabilities_unsupported(
    venue: Venue, capability: Capability
) -> None:
    """Not a convention — the declaration refuses to be built otherwise, so
    "两所都标 unsupported" holds for every adapter that ever exists."""
    sheet = CapabilitySet(venue=venue, declarations=declarations_for(venue))
    assert sheet[capability].supported is Support.UNSUPPORTED
    assert sheet[capability].mode is Mode.NOT_APPLICABLE
    assert sheet[capability].completeness is Completeness.NOT_APPLICABLE

    with pytest.raises(CapabilityError, match="until M4"):
        MarketCapabilityDeclaration(
            capability=capability,  # type: ignore[arg-type]
            supported=Support.SUPPORTED,
            mode=Mode.PUSH_STREAM,
            completeness=Completeness.FULL,
        )


def test_no_wallet_capability_is_in_the_market_data_enum() -> None:
    """Seam ⑤: "钱包类能力不在这个枚举里" — and none of the market ones is in
    the wallet enum either."""
    assert {c.value for c in Capability}.isdisjoint({w.value for w in WalletCapability})


# --------------------------------------------------------------------------- #
# Three answers, never merged
# --------------------------------------------------------------------------- #
def test_the_three_answers_are_three_fields() -> None:
    declaration = MarketCapabilityDeclaration(
        capability=Capability.KLINES,
        supported=Support.SUPPORTED,
        mode=Mode.POLL_HISTORY,
        completeness=Completeness.PARTIAL_HISTORY,
    )
    assert declaration.supported is Support.SUPPORTED
    assert declaration.mode is Mode.POLL_HISTORY
    assert declaration.completeness is Completeness.PARTIAL_HISTORY


def test_a_supported_capability_must_answer_all_three() -> None:
    with pytest.raises(CapabilityError, match="all three"):
        MarketCapabilityDeclaration(
            capability=Capability.MARK_PRICE,
            supported=Support.SUPPORTED,
            mode=Mode.NOT_APPLICABLE,
            completeness=Completeness.FULL,
        )


def test_an_unsupported_capability_describes_nothing() -> None:
    with pytest.raises(CapabilityError, match="no mode"):
        MarketCapabilityDeclaration(
            capability=Capability.MARK_PRICE,
            supported=Support.UNSUPPORTED,
            mode=Mode.POLL_SNAPSHOT,
            completeness=Completeness.NOT_APPLICABLE,
        )


# --------------------------------------------------------------------------- #
# Liquidations
# --------------------------------------------------------------------------- #
def test_full_is_not_a_liquidation_completeness_at_all() -> None:
    """Demo ③, the runtime half. The static half is in
    ``tests/test_adapter_admission.py`` and is enforced by ``uv run mypy``."""
    assert Completeness.FULL in tuple(Completeness)
    assert Completeness.FULL not in get_args(LiquidationCompleteness)
    assert set(get_args(LiquidationCompleteness)) == {
        Completeness.LOWER_BOUND,
        Completeness.PARTIAL_HISTORY,
        Completeness.NOT_APPLICABLE,
    }


def test_a_throttled_feed_is_always_a_lower_bound() -> None:
    """``01`` §4.7: "交易所限流的爆仓一律标 lower_bound"."""
    with pytest.raises(CapabilityError, match="lower_bound"):
        LiquidationCapabilityDeclaration(
            capability=Capability.LIQUIDATION_STREAM,
            supported=Support.SUPPORTED,
            mode=Mode.PUSH_STREAM,
            completeness=Completeness.PARTIAL_HISTORY,
            throttled_source=True,
        )


def test_a_feed_we_do_not_have_cannot_be_throttled() -> None:
    with pytest.raises(CapabilityError, match="cannot be throttled"):
        LiquidationCapabilityDeclaration(
            capability=Capability.LIQUIDATION_STREAM,
            supported=Support.UNSUPPORTED,
            mode=Mode.NOT_APPLICABLE,
            completeness=Completeness.NOT_APPLICABLE,
            throttled_source=True,
        )


def test_a_liquidation_capability_needs_its_own_declaration_type() -> None:
    """Otherwise the narrowed completeness could be walked around by using the
    market declaration for a liquidation capability."""
    smuggled = MarketCapabilityDeclaration(
        capability=Capability.LIQUIDATION_STREAM,  # type: ignore[arg-type]
        supported=Support.SUPPORTED,
        mode=Mode.PUSH_STREAM,
        completeness=Completeness.FULL,
    )
    with pytest.raises(CapabilityError, match="LiquidationCapabilityDeclaration"):
        CapabilitySet(
            venue=Venue.BINANCE,
            declarations=declarations_for(
                Venue.BINANCE, **{Capability.LIQUIDATION_STREAM: smuggled}
            ),
        )


# --------------------------------------------------------------------------- #
# What Hyperliquid may not claim
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "capability",
    sorted(VENUE_MUST_DECLARE_UNSUPPORTED[Venue.HYPERLIQUID]),
)
def test_hyperliquid_cannot_declare_what_it_does_not_publish(capability: Capability) -> None:
    """AGENTS §3.4: "declare both as unsupported rather than deriving a
    substitute". ``04`` §1: the ratios are Binance-only. ``04`` §8: Hyperliquid
    has no usable public liquidation stream, and the per-wallet fills that
    carry forced trades are M5 and seam ⑤."""
    sheet = CapabilitySet(
        venue=Venue.HYPERLIQUID, declarations=declarations_for(Venue.HYPERLIQUID)
    )
    assert sheet[capability].supported is Support.UNSUPPORTED

    if capability in LIQUIDATION_CAPABILITIES:
        claimed: Any = LiquidationCapabilityDeclaration(
            capability=capability,  # type: ignore[arg-type]
            supported=Support.SUPPORTED,
            mode=Mode.PUSH_STREAM,
            completeness=Completeness.LOWER_BOUND,
            throttled_source=False,
        )
    else:
        claimed = MarketCapabilityDeclaration(
            capability=capability,  # type: ignore[arg-type]
            supported=Support.SUPPORTED,
            mode=Mode.POLL_SNAPSHOT,
            completeness=Completeness.FULL,
        )
    with pytest.raises(CapabilityError, match="no substitute"):
        CapabilitySet(
            venue=Venue.HYPERLIQUID,
            declarations=declarations_for(Venue.HYPERLIQUID, **{capability: claimed}),
        )


# --------------------------------------------------------------------------- #
# The sheet as a whole
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("venue", VENUES)
def test_every_capability_must_be_declared(venue: Venue) -> None:
    complete = declarations_for(venue)
    short = tuple(d for d in complete if d.capability is not Capability.KLINES)
    with pytest.raises(CapabilityError, match="klines"):
        CapabilitySet(venue=venue, declarations=short)


@pytest.mark.parametrize("venue", VENUES)
def test_a_capability_cannot_be_declared_twice(venue: Venue) -> None:
    complete = declarations_for(venue)
    with pytest.raises(CapabilityError, match="declared twice"):
        CapabilitySet(venue=venue, declarations=(*complete, complete[0]))


def test_requiring_an_unsupported_capability_raises_before_anything_is_spent() -> None:
    sheet = CapabilitySet(
        venue=Venue.HYPERLIQUID, declarations=declarations_for(Venue.HYPERLIQUID)
    )
    with pytest.raises(UnsupportedCapability, match="long_short_ratio"):
        sheet.require(Capability.LONG_SHORT_RATIO)
    assert sheet.require(Capability.MARK_PRICE).supported is Support.SUPPORTED


def test_the_three_ratio_kinds_map_onto_the_two_ratio_capabilities() -> None:
    assert capability_of_ls_ratio_kind(LsRatioKind.TAKER_LONG_SHORT) is Capability.TAKER_RATIO
    for kind in (LsRatioKind.GLOBAL_LONG_SHORT_ACCOUNT, LsRatioKind.TOP_LONG_SHORT_POSITION):
        assert capability_of_ls_ratio_kind(kind) is Capability.LONG_SHORT_RATIO
