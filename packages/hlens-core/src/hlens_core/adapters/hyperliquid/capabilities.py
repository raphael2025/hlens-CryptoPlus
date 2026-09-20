"""Hyperliquid's answer sheet: thirteen capabilities, three answers each.

Seam ② requires all thirteen, and
:class:`~hlens_core.adapters.capabilities.CapabilitySet` refuses an incomplete
sheet, so the list below is exhaustive by construction rather than by care.

**Four of the thirteen say no, and saying no is this step's main job.**

``long_short_ratio`` / ``taker_ratio``
    ``04`` §1 puts it in the table header — "多空比 / 主动买卖比（**仅
    Binance**）" — and adds "F3：HL 该所不发布". AGENTS §3.4 turns that into an
    instruction: "Hyperliquid publishes no long/short ratio and no usable
    public liquidation stream — declare both as unsupported rather than
    **deriving a substitute**."

    The substitutes are easy to reach for and each one would be a different
    measurement wearing this one's name: a long/short share assembled from
    tracked wallets' positions is a share of *our sample*, not of the venue;
    one assembled from open interest and price direction is a model, not an
    observation; a taker buy/sell share reconstructed from the ``trades``
    channel's ``side`` field would be a number we computed from a feed we do
    not have complete (and it would need M4's trade collection, which F19 has
    not gated yet). None of them is written here, and
    :data:`~hlens_core.adapters.capabilities.VENUE_MUST_DECLARE_UNSUPPORTED`
    refuses the declaration at construction time so that none of them can be
    written later either.

``liquidation_stream``
    ``04`` §8, two measured findings: Hyperliquid's WebSocket ``trades``
    channel **carries no liquidation field at all** (the messages name both
    counterparties but nothing marks a forced fill), and forced fills appear
    only on the per-wallet ``userFills`` endpoints — which are seam ⑤'s, M5's,
    and not this adapter's. The hub machine's existing Hyperliquid liquidation
    dataset is a *derivative of tracked wallets' fills* with 1.6–8 % historical
    capture, which ``04`` §8 says in as many words may be used as a reference
    for large-wallet events and **not** as an exchange-wide total.

    So: no stream, no lower bound derived from wallet sampling, and F12's
    liquidation figures carry a named venue. ``01`` §4.9's ban on a cross-venue
    "whole market" total is the other half of the same rule.

``trade_stream`` / ``book_l2`` / ``spot``
    M4 (F20–F22), declared now and refused as ``supported`` until then, exactly
    as on Binance — seam ②: "M4 只补实现、不改协议".

That is four ``unsupported`` answers Binance does not have, plus the same three
M4 ones. **No wallet capability appears here**, because there is none in the
enum: seam ⑤ keeps them in :mod:`hlens_core.wallet`, a module this one neither
imports nor is imported by. The temptation is larger on this venue than on
Binance — M5's wallet collection is Hyperliquid's too, and the endpoints sit on
the same ``/info`` path this adapter posts to — which is precisely why the
boundary is a different protocol and not a flag.

Where the ``yes`` answers differ from Binance's
-----------------------------------------------
``klines`` is the only *supported* capability on either venue whose
completeness is not ``full``: ``candleSnapshot`` retains about 5000 bars, which
is ~3.5 days at 1m (``04`` §5, §13 第 3 行), so it is ``partial_history``. That
one word is what keeps F8 showing "数据积累中" instead of extrapolating a
30-day percentile out of three and a half days.
"""

from __future__ import annotations

from typing import Final

from hlens_core.adapters.capabilities import (
    Capability,
    CapabilitySet,
    Completeness,
    MarketCapabilityDeclaration,
    MarketCapabilityName,
    Mode,
    Support,
    unsupported,
)
from hlens_core.contracts import Venue

__all__ = ["HYPERLIQUID_CAPABILITIES", "hyperliquid_capabilities"]


def _supported(
    capability: MarketCapabilityName, mode: Mode, completeness: Completeness, note: str
) -> MarketCapabilityDeclaration:
    """One supported, non-liquidation capability.

    ``capability`` is typed :data:`~hlens_core.adapters.capabilities.
    MarketCapabilityName`, so passing the liquidation stream or one of the M4
    three is a **type** error here and not only the runtime error the
    constructor already raises.
    """
    return MarketCapabilityDeclaration(
        capability=capability,
        supported=Support.SUPPORTED,
        mode=mode,
        completeness=completeness,
        note=note,
    )


def hyperliquid_capabilities() -> CapabilitySet:
    """The sheet, built fresh so a caller cannot mutate a shared one."""
    return CapabilitySet(
        venue=Venue.HYPERLIQUID,
        declarations=(
            _supported(
                Capability.INSTRUMENTS,
                Mode.POLL_SNAPSHOT,
                Completeness.FULL,
                "meta.universe lists every perpetual in one call with szDecimals and "
                "maxLeverage (04 §3). No status vocabulary is documented, so status is "
                "None rather than a guess",
            ),
            _supported(
                Capability.MARK_PRICE,
                Mode.POLL_SNAPSHOT,
                Completeness.FULL,
                "metaAndAssetCtxs carries markPx, oraclePx and premium for the whole "
                "market in one W=20 call (04 §3). premium is a real field here, unlike "
                "on Binance where the column stays None",
            ),
            _supported(
                Capability.FUNDING_RATE,
                Mode.POLL_SNAPSHOT,
                Completeness.FULL,
                "metaAndAssetCtxs.funding, settled HOURLY (04 §3). The raw hourly value "
                "is stored with funding_interval_h=1; the 8-hour figure is derived by "
                "the contract on read and never stored (03 §5). predictedFundings and "
                "fundingHistory extend the same capability forward and back",
            ),
            _supported(
                Capability.OPEN_INTEREST,
                Mode.POLL_SNAPSHOT,
                Completeness.FULL,
                "metaAndAssetCtxs.openInterest, whole market in the same call — unlike "
                "Binance, where openInterest is per symbol and grows linearly with the "
                "coin count (04 §1). Not backfillable: 04 §5 says HL has no OI history "
                "endpoint, which is why F8 waits 30 days rather than extrapolating",
            ),
            unsupported(
                Capability.LONG_SHORT_RATIO,
                note="Hyperliquid does not publish it (04 §1: 多空比 仅 Binance; F3: 该所"
                "不发布). AGENTS §3.4: declare it unsupported rather than deriving a "
                "substitute — a share computed from tracked wallets would be a share of "
                "our sample, not of the venue, and it would need seam ⑤ data to exist",
            ),
            unsupported(
                Capability.TAKER_RATIO,
                note="Same as long_short_ratio (04 §1: 主动买卖比 仅 Binance). "
                "Reconstructing it from the ws trades channel's side field would be a "
                "different measurement under this name, and would need M4's trade "
                "collection, which F19 has not gated",
            ),
            _supported(
                Capability.KLINES,
                Mode.POLL_HISTORY,
                Completeness.PARTIAL_HISTORY,
                "candleSnapshot retains ~5000 bars — about 3.5 days at 1m, 208 days at "
                "1h (04 §5) — and a ranged response returns at most 500 elements. NOT "
                "full history: this is the one supported capability on either venue that "
                "is not `full`, and F8 shows 数据积累中 because of it",
            ),
            _supported(
                Capability.TICKER_24H,
                Mode.POLL_SNAPSHOT,
                Completeness.FULL,
                "metaAndAssetCtxs.dayNtlVlm, the same one call. 04 §3's field list has "
                "no previous-day price, so chg24h_pct is None — unknown, never 0",
            ),
            _supported(
                Capability.MARK_PRICE_STREAM,
                Mode.PUSH_STREAM,
                Completeness.FULL,
                "ws allMids, one subscription for the whole market (03 §6.1 budgets "
                "exactly 1). READ THIS: 04 §3 defines allMids as 币→中间价 — the MID "
                "price, not the venue's mark. 04 §1 still names this channel as the push "
                "source for the mark column and 03 §6's fast lane polls metaAndAssetCtxs "
                "for the real markPx, so rows from this stream are tagged "
                "source=hyperliquid_ws_all_mids and F7's cross-venue mark spread must be "
                "computed from the REST rows. Raised in the PR's Doc corrections",
            ),
            unsupported(
                Capability.LIQUIDATION_STREAM,
                note="No exchange-wide public stream exists (04 §8, 实测): the ws trades "
                "channel carries no liquidation field, and forced fills appear only on "
                "per-wallet userFills — which is seam ⑤ and M5, not this adapter. The "
                "hub dataset is a tracked-wallet derivative with 1.6–8% historical "
                "capture and may not stand in for a venue total. No lower bound is "
                "derived here; F12 names the venue instead (01 §4.9)",
            ),
            unsupported(
                Capability.TRADE_STREAM,
                note="M4 (F20). ws trades{coin} exists and costs no /info weight, but the "
                "whitelist, the table and the 7-day retention are seam ④ decisions gated "
                "by F19 — 03 §2: M4 只补实现、不改协议. 04 §8 also records two traps for "
                "that step: ~28% of trade hashes are all zeros and tid is not unique",
            ),
            unsupported(
                Capability.BOOK_L2,
                note="M4 (F21). l2Book is only W=2 — the cheapest weight group, not the "
                "most expensive (04 §13 第 8 行 corrects the opposite reading) — but it "
                "returns at most 20 levels per side, which is the real limit and an open "
                "question for ±0.5% coverage (04 §11 第 5 项)",
            ),
            unsupported(
                Capability.SPOT,
                note="M4 (F22). HL spot has its own symbol system (TOKEN/USDC and "
                "@<index>) and 04 §9.3 states that no document says how a spot token "
                "pairs with the same-named perpetual; the table is hand-maintained from "
                "an on-machine spotMeta dump (04 §11 第 6 项). Matching by similar names "
                "is forbidden outright",
            ),
        ),
    )


#: The sheet, built once for callers that only read it.
HYPERLIQUID_CAPABILITIES: Final[CapabilitySet] = hyperliquid_capabilities()
