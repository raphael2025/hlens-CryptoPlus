"""Seam ② — the capability declaration: three answers, never merged.

``01-PRODUCT.md`` §4.7 and ``03-ARCHITECTURE.md`` §2 seam ② both say the same
thing in different words: ``supported`` / ``mode`` / ``completeness`` are
**three separate answers**, and a venue-throttled liquidation feed is always
``lower_bound``. They are three fields here, on purpose, and nothing in this
package ever collapses them into one enum or one boolean: "does the venue
publish it", "how does it reach us" and "how much of it do we get" are
independent facts, and the moment they are merged the third one disappears —
which is exactly the number F12 has to show beside every liquidation figure.

What this module fixes, so that no adapter can quietly disagree
--------------------------------------------------------------
* :class:`Capability` already contains ``trade_stream`` ``book_l2`` ``spot``
  (seam ②: "枚举里现在就有 … 两所都标 unsupported，M4 只补实现、不改协议").
  :data:`M4_CAPABILITIES` makes "unsupported until M4" a rule the constructor
  enforces rather than a comment: declaring one of them ``supported`` raises.
* Hyperliquid publishes no long/short ratio, no taker ratio, and no usable
  public liquidation stream (``04`` §1, §8; AGENTS §3.4: "declare both as
  unsupported rather than deriving a substitute"). :data:`VENUE_MUST_DECLARE_
  UNSUPPORTED` turns that sentence into a refusal at construction time — a
  derived substitute cannot be declared into existence.
* Liquidation completeness is a **narrower type** than every other
  capability's: :data:`LiquidationCompleteness` has no ``full`` member, so
  ``Completeness.FULL`` on a liquidation declaration is a static type error,
  not a runtime one. ``04`` §2: ``!forceOrder@arr`` pushes at most one order
  per symbol per second and is therefore a floor; ``04`` §8: Hyperliquid has no
  exchange-wide public stream at all, and the M5 imported segment is a sample
  (``partial_history``), never a total.
* Wallet capabilities are **not in this enum**, not even as a spare value
  (seam ⑤). They live in :mod:`hlens_core.wallet`, which this module neither
  imports nor is imported by.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

from hlens_core.contracts import LsRatioKind, Venue

__all__ = [
    "LIQUIDATION_CAPABILITIES",
    "M4_CAPABILITIES",
    "VENUE_MUST_DECLARE_UNSUPPORTED",
    "AdapterVenue",
    "Capability",
    "CapabilityDeclaration",
    "CapabilityError",
    "CapabilitySet",
    "Completeness",
    "LiquidationCapabilityDeclaration",
    "LiquidationCapabilityName",
    "LiquidationCompleteness",
    "MarketCapabilityDeclaration",
    "MarketCapabilityName",
    "Mode",
    "Support",
    "UnsupportedCapability",
    "capability_of_ls_ratio_kind",
    "unsupported",
]

#: A market-data adapter is one venue's. ``Venue.CROSS`` is §5's cross-venue
#: scope marker, not an exchange, so it cannot own an adapter.
AdapterVenue = Literal[Venue.BINANCE, Venue.HYPERLIQUID]


class CapabilityError(ValueError):
    """A capability set that contradicts the documents. Raised at construction:
    an adapter that declares something it may not declare must not start."""


class UnsupportedCapability(LookupError):
    """Asked for something this venue does not publish. Not an error condition
    of the venue — an error in the caller, which should have read the
    declaration first."""


class Capability(StrEnum):
    """Everything a market-data adapter can be asked about.

    One enum for both venues and every milestone: the M4 entries exist now and
    are refused as ``supported`` until M4 opens them, so M4 adds code and
    changes no declaration shape (seam ②).
    """

    INSTRUMENTS = "instruments"
    """Contract discovery: Binance ``exchangeInfo`` + ``fundingInfo``,
    Hyperliquid ``meta`` (``04`` §1). Feeds ``instruments`` / ``coin_universe``."""

    MARK_PRICE = "mark_price"
    """Mark / index / premium snapshot (``fapi/v1/premiumIndex``,
    ``metaAndAssetCtxs``)."""

    FUNDING_RATE = "funding_rate"
    """Current funding over the venue's **native** interval, plus the next
    settlement time. The 8-hour figure is derived by the contract, never
    stored (seam ①)."""

    OPEN_INTEREST = "open_interest"
    """Open interest, base and USD."""

    LONG_SHORT_RATIO = "long_short_ratio"
    """Account- and position-based long/short shares. Binance only
    (``/futures/data/*``); Hyperliquid does not publish it (``04`` §1)."""

    TAKER_RATIO = "taker_ratio"
    """Taker buy/sell share (``/futures/data/takerlongshortRatio``). Binance
    only, and accounted against the separate ``futures_data`` request bucket,
    like the other ratios."""

    KLINES = "klines"
    """OHLCV bars. Used for backfill, which is why a venue can support this
    with ``partial_history``: Hyperliquid keeps only ~5000 bars, about 3.5 days
    at 1m (``04`` §5)."""

    TICKER_24H = "ticker_24h"
    """Rolling 24 h change and quote volume."""

    MARK_PRICE_STREAM = "mark_price_stream"
    """Pushed mark prices: Binance ``!markPrice@arr@1s`` on the ``/market``
    group, Hyperliquid ``allMids``."""

    LIQUIDATION_STREAM = "liquidation_stream"
    """Pushed forced orders. Binance ``!forceOrder@arr`` — throttled to one
    order per symbol per second, hence always a lower bound. Hyperliquid has no
    public exchange-wide equivalent (``04`` §8)."""

    TRADE_STREAM = "trade_stream"
    """M4 (F20). Declared now, refused as supported until then."""

    BOOK_L2 = "book_l2"
    """M4 (F21). Declared now, refused as supported until then."""

    SPOT = "spot"
    """M4 (F22). Declared now, refused as supported until then."""


class Support(StrEnum):
    """Answer 1 of 3: **does this venue publish it at all?**"""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


class Mode(StrEnum):
    """Answer 2 of 3: **how does it reach us?**

    Not a synonym for answer 1 and not a synonym for answer 3: a pushed stream
    can be a lower bound (Binance liquidations) and a polled snapshot can be
    complete (open interest).
    """

    PUSH_STREAM = "push_stream"
    """The venue pushes it over a WebSocket; we do not choose the instants."""

    POLL_SNAPSHOT = "poll_snapshot"
    """We pull the current state over REST on our own schedule."""

    POLL_HISTORY = "poll_history"
    """We pull past points over REST, paginated — the backfill shape."""

    NOT_APPLICABLE = "not_applicable"
    """The only legal answer when ``supported`` is ``unsupported``."""


class Completeness(StrEnum):
    """Answer 3 of 3: **how much of it do we get?**

    ``full`` means every item the venue publishes reaches us, not that it is
    the whole market's truth — no venue can answer that, and ``01`` §4.9 bans
    the cross-venue total that such a claim would invite.
    """

    FULL = "full"
    LOWER_BOUND = "lower_bound"
    PARTIAL_HISTORY = "partial_history"
    NOT_APPLICABLE = "not_applicable"


#: Declared now, refused as ``supported`` until M4 opens them (seam ②).
M4_CAPABILITIES: Final[frozenset[Capability]] = frozenset(
    {Capability.TRADE_STREAM, Capability.BOOK_L2, Capability.SPOT}
)

#: The capabilities whose completeness is bounded by :data:`LiquidationCompleteness`.
LIQUIDATION_CAPABILITIES: Final[frozenset[Capability]] = frozenset(
    {Capability.LIQUIDATION_STREAM}
)

#: What a venue may not claim, however tempting the substitute (AGENTS §3.4).
#: Hyperliquid: no long/short ratio and no taker ratio (``04`` §1 — "仅
#: Binance"), and no usable public liquidation stream (``04`` §8: ``trades``
#: carries no liquidation field; forced fills are visible only per wallet, which
#: is M5 and seam ⑤). Deriving one of these from wallet sampling and presenting
#: it as a venue capability is the specific mistake this table refuses.
VENUE_MUST_DECLARE_UNSUPPORTED: Final[Mapping[Venue, frozenset[Capability]]] = {
    Venue.HYPERLIQUID: frozenset(
        {
            Capability.LONG_SHORT_RATIO,
            Capability.TAKER_RATIO,
            Capability.LIQUIDATION_STREAM,
        }
    ),
    Venue.BINANCE: frozenset(),
}

#: Every capability except the liquidation ones. Written out so that the
#: liquidation narrowing below is a type error rather than a convention.
MarketCapabilityName = Literal[
    Capability.INSTRUMENTS,
    Capability.MARK_PRICE,
    Capability.FUNDING_RATE,
    Capability.OPEN_INTEREST,
    Capability.LONG_SHORT_RATIO,
    Capability.TAKER_RATIO,
    Capability.KLINES,
    Capability.TICKER_24H,
    Capability.MARK_PRICE_STREAM,
    Capability.TRADE_STREAM,
    Capability.BOOK_L2,
    Capability.SPOT,
]

LiquidationCapabilityName = Literal[Capability.LIQUIDATION_STREAM]

#: **There is no ``full`` here, and that is the point.** ``01`` §4.7: a
#: venue-throttled liquidation feed is always ``lower_bound``; ``04`` §8: the M5
#: Hyperliquid segment is a sample of a derived history, ``partial_history``.
#: Because the type has no third option, ``Completeness.FULL`` on a liquidation
#: declaration fails type checking — it never reaches the runtime guard below.
LiquidationCompleteness = Literal[
    Completeness.LOWER_BOUND,
    Completeness.PARTIAL_HISTORY,
    Completeness.NOT_APPLICABLE,
]


def _check_three_answers(
    capability: Capability, supported: Support, mode: Mode, completeness: Completeness
) -> None:
    """The three answers stay independent, but not arbitrary.

    Only one combination is forced, and it is forced in both directions:
    ``unsupported`` means there is nothing to describe, so ``mode`` and
    ``completeness`` are ``not_applicable``; ``supported`` means there is, so
    neither may be ``not_applicable``. Everything else is the adapter's honest
    answer and this function does not second-guess it.
    """
    if supported is Support.UNSUPPORTED:
        if mode is not Mode.NOT_APPLICABLE or completeness is not Completeness.NOT_APPLICABLE:
            raise CapabilityError(
                f"{capability.value}: an unsupported capability has no mode and no "
                f"completeness; got mode={mode.value} completeness={completeness.value}"
            )
        return
    if mode is Mode.NOT_APPLICABLE or completeness is Completeness.NOT_APPLICABLE:
        raise CapabilityError(
            f"{capability.value}: a supported capability must answer all three "
            f"questions; got mode={mode.value} completeness={completeness.value}"
        )
    if capability in M4_CAPABILITIES:
        raise CapabilityError(
            f"{capability.value} is declared on every adapter but stays unsupported "
            "until M4 (03 §2 seam ②: M4 只补实现、不改协议)"
        )


@dataclass(frozen=True, slots=True)
class MarketCapabilityDeclaration:
    """Three answers about one non-liquidation capability."""

    capability: MarketCapabilityName
    supported: Support
    mode: Mode
    completeness: Completeness
    note: str | None = None
    """Why the answers are what they are — the venue sentence a reviewer can
    check against ``04``. Not a fourth answer; it is never parsed."""

    def __post_init__(self) -> None:
        _check_three_answers(self.capability, self.supported, self.mode, self.completeness)


@dataclass(frozen=True, slots=True)
class LiquidationCapabilityDeclaration:
    """Three answers about a liquidation capability, plus the throttle fact.

    ``completeness`` is typed :data:`LiquidationCompleteness`, which has no
    ``full``. ``throttled_source`` is the venue fact behind the answer and is
    carried separately because ``05``'s ``liquidations`` table has both columns:
    the number that gets published is ``completeness``, and ``throttled_source``
    is why.
    """

    capability: LiquidationCapabilityName
    supported: Support
    mode: Mode
    completeness: LiquidationCompleteness
    throttled_source: bool
    note: str | None = None

    def __post_init__(self) -> None:
        _check_three_answers(self.capability, self.supported, self.mode, self.completeness)
        if self.supported is Support.UNSUPPORTED and self.throttled_source:
            raise CapabilityError(
                f"{self.capability.value}: a feed we do not have cannot be throttled"
            )
        if self.throttled_source and self.completeness is not Completeness.LOWER_BOUND:
            raise CapabilityError(
                f"{self.capability.value}: a venue-throttled liquidation feed is always "
                f"lower_bound (01 §4.7); got {self.completeness.value}"
            )


CapabilityDeclaration = MarketCapabilityDeclaration | LiquidationCapabilityDeclaration


def unsupported(capability: Capability, *, note: str) -> CapabilityDeclaration:
    """Declare a capability this venue does not publish.

    ``note`` is required: "unsupported" without a reason is how a substitute
    gets derived later by someone who assumed it was an oversight.
    """
    if capability is Capability.LIQUIDATION_STREAM:
        return LiquidationCapabilityDeclaration(
            capability=capability,
            supported=Support.UNSUPPORTED,
            mode=Mode.NOT_APPLICABLE,
            completeness=Completeness.NOT_APPLICABLE,
            throttled_source=False,
            note=note,
        )
    return MarketCapabilityDeclaration(
        capability=capability,
        supported=Support.UNSUPPORTED,
        mode=Mode.NOT_APPLICABLE,
        completeness=Completeness.NOT_APPLICABLE,
        note=note,
    )


def capability_of_ls_ratio_kind(kind: LsRatioKind) -> Capability:
    """Which capability covers one of seam ①'s three ``ls_ratio`` kinds.

    The contract enumerates the kinds and this enumerates the capabilities; a
    caller that wants the taker kind has to find ``taker_ratio`` declared
    ``supported``, not ``long_short_ratio``.
    """
    if kind is LsRatioKind.TAKER_LONG_SHORT:
        return Capability.TAKER_RATIO
    return Capability.LONG_SHORT_RATIO


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    """One venue adapter's complete, checked answer sheet.

    **Complete** is load-bearing: every member of :class:`Capability` must be
    declared exactly once. An omitted capability would read as "we never
    thought about it", which is the state seam ② exists to make impossible —
    and it is what lets ``/sources`` and the status page say "该所不发布"
    instead of leaving a blank.
    """

    venue: AdapterVenue
    declarations: tuple[CapabilityDeclaration, ...]

    def __post_init__(self) -> None:
        seen: dict[Capability, CapabilityDeclaration] = {}
        for declaration in self.declarations:
            capability = declaration.capability
            if capability in seen:
                raise CapabilityError(f"{capability.value} is declared twice")
            is_liquidation = capability in LIQUIDATION_CAPABILITIES
            if is_liquidation != isinstance(declaration, LiquidationCapabilityDeclaration):
                wanted = (
                    "LiquidationCapabilityDeclaration"
                    if is_liquidation
                    else "MarketCapabilityDeclaration"
                )
                raise CapabilityError(f"{capability.value} must be declared with {wanted}")
            seen[capability] = declaration

        missing = sorted(c.value for c in Capability if c not in seen)
        if missing:
            raise CapabilityError(
                f"{self.venue.value}: every capability must be declared, "
                f"including the ones this venue does not publish; missing {missing}"
            )

        for capability in sorted(VENUE_MUST_DECLARE_UNSUPPORTED[self.venue]):
            if seen[capability].supported is not Support.UNSUPPORTED:
                raise CapabilityError(
                    f"{self.venue.value} does not publish {capability.value} and no "
                    "substitute may be derived for it (AGENTS §3.4, 04 §1/§8)"
                )

    def __getitem__(self, capability: Capability) -> CapabilityDeclaration:
        for declaration in self.declarations:
            if declaration.capability is capability:
                return declaration
        raise UnsupportedCapability(f"{capability.value} is not declared")

    def supports(self, capability: Capability) -> bool:
        return self[capability].supported is Support.SUPPORTED

    def require(self, capability: Capability) -> CapabilityDeclaration:
        """The declaration, or :class:`UnsupportedCapability` if we do not have it.

        Adapters call this before spending anything: a request for data the
        venue does not publish must cost zero weight on a shared egress IP.
        """
        declaration = self[capability]
        if declaration.supported is not Support.SUPPORTED:
            raise UnsupportedCapability(
                f"{self.venue.value} declares {capability.value} unsupported"
                + (f": {declaration.note}" if declaration.note else "")
            )
        return declaration

    def unsupported_capabilities(self) -> tuple[Capability, ...]:
        return tuple(
            d.capability for d in self.declarations if d.supported is Support.UNSUPPORTED
        )
