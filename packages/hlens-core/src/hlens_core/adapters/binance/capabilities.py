"""Binance's answer sheet: thirteen capabilities, three answers each.

Seam ② requires all thirteen — the ones this venue publishes, the ones it does
not, and the three M4 entries that exist now so that M4 adds code and changes no
protocol. :class:`~hlens_core.adapters.capabilities.CapabilitySet` refuses an
incomplete sheet, so the list below is exhaustive by construction, not by care.

Where each answer comes from
----------------------------
``mode`` is "how does it reach us", and the three ratio endpoints are
``poll_history`` rather than ``poll_snapshot`` on purpose: 决定 A8 polls them
every 10 minutes **with ``limit``** so that both 5-minute points of the window
come back (``03`` §6, ``04`` §4). The call returns a series; calling that a
snapshot would hide the one thing that keeps the stored grid at 5 minutes.

``completeness`` is "how much of it do we get", and only one entry is not
``full``: ``liquidation_stream``. ``04`` §2 — ``!forceOrder@arr`` pushes **at
most one order per symbol per second** — so what arrives is a floor, the
declaration says ``lower_bound``, ``throttled_source`` is ``True``, and
``LiquidationCompleteness`` has no ``full`` to write even if somebody wanted
one. F12 hangs its permanent ``下界`` label on exactly this field.
"""

from __future__ import annotations

from typing import Final

from hlens_core.adapters.capabilities import (
    Capability,
    CapabilitySet,
    Completeness,
    LiquidationCapabilityDeclaration,
    MarketCapabilityDeclaration,
    MarketCapabilityName,
    Mode,
    Support,
    unsupported,
)
from hlens_core.contracts import Venue

__all__ = ["BINANCE_CAPABILITIES", "binance_capabilities"]


def _supported(
    capability: MarketCapabilityName, mode: Mode, completeness: Completeness, note: str
) -> MarketCapabilityDeclaration:
    """One supported, non-liquidation capability.

    ``capability`` is typed :data:`MarketCapabilityName`, so the M4 three and the
    liquidation stream are a **type** error here, not only the runtime error the
    constructor already raises.
    """
    return MarketCapabilityDeclaration(
        capability=capability,
        supported=Support.SUPPORTED,
        mode=mode,
        completeness=completeness,
        note=note,
    )


def binance_capabilities() -> CapabilitySet:
    """The sheet, built fresh so a caller cannot mutate a shared one."""
    return CapabilitySet(
        venue=Venue.BINANCE,
        declarations=(
            _supported(
                Capability.INSTRUMENTS,
                Mode.POLL_SNAPSHOT,
                Completeness.FULL,
                "exchangeInfo (contractType=PERPETUAL) + fundingInfo for the symbols "
                "whose interval is not the 8-hour default (04 §2)",
            ),
            _supported(
                Capability.MARK_PRICE,
                Mode.POLL_SNAPSHOT,
                Completeness.FULL,
                "premiumIndex covers the whole market in one call (04 §1); the 30-second "
                "lane runs on the !markPrice@arr@1s stream and this is its 60-second "
                "fallback (03 §6)",
            ),
            _supported(
                Capability.FUNDING_RATE,
                Mode.POLL_SNAPSHOT,
                Completeness.FULL,
                "premiumIndex.lastFundingRate over the venue's native interval, plus "
                "fundingInfo for the non-default intervals; history via fundingRate "
                "(04 §2, §5). The 8-hour figure is derived by the contract, never stored",
            ),
            _supported(
                Capability.OPEN_INTEREST,
                Mode.POLL_SNAPSHOT,
                Completeness.FULL,
                "openInterest requires a symbol, so this one grows linearly with the coin "
                "count — 180 coins = 180 weight/min (04 §1, §2). Base units only: the USD "
                "figure exists on openInterestHist, not here",
            ),
            _supported(
                Capability.LONG_SHORT_RATIO,
                Mode.POLL_HISTORY,
                Completeness.FULL,
                "/futures/data/{globalLongShortAccountRatio,topLongShortPositionRatio} on "
                "the separate futures_data request bucket; polled every 10 min with limit "
                "so both 5-minute points come back (决定 A8). The endpoint's own history "
                "stops at 30 days, which is a backfill fact (M2), not a live-lane gap",
            ),
            _supported(
                Capability.TAKER_RATIO,
                Mode.POLL_HISTORY,
                Completeness.FULL,
                "/futures/data/takerlongshortRatio, same bucket and same 10-minute lane; "
                "long_share = buyVol / (buyVol + sellVol) (04 §2)",
            ),
            _supported(
                Capability.KLINES,
                Mode.POLL_HISTORY,
                Completeness.FULL,
                "klines reaches full history and the bulk archive is unmetered "
                "(04 §2, §5) — unlike Hyperliquid, which keeps ~5000 bars. M1 collects "
                "none of them (F2); M2's backfill is the only caller",
            ),
            _supported(
                Capability.TICKER_24H,
                Mode.POLL_SNAPSHOT,
                Completeness.FULL,
                "ticker/24hr covers the whole market in one call, W=40 (04 §2)",
            ),
            _supported(
                Capability.MARK_PRICE_STREAM,
                Mode.PUSH_STREAM,
                Completeness.FULL,
                "!markPrice@arr@1s on the /market group. The legacy .../stream endpoint "
                "stopped pushing permanently on 2026-04-23 (04 §2 change notice)",
            ),
            LiquidationCapabilityDeclaration(
                capability=Capability.LIQUIDATION_STREAM,
                supported=Support.SUPPORTED,
                mode=Mode.PUSH_STREAM,
                completeness=Completeness.LOWER_BOUND,
                throttled_source=True,
                note="!forceOrder@arr on the /market group: the venue pushes at most one "
                "order per symbol per second (04 §2), so every total built on it is a "
                "floor and F12 labels it 下界 forever",
            ),
            unsupported(
                Capability.TRADE_STREAM,
                note="M4 (F20). @aggTrade is on the /market group and costs no REST "
                "weight, but the whitelist, the table and the 7-day retention are seam ④ "
                "decisions gated by F19 — 03 §2: M4 只补实现、不改协议",
            ),
            unsupported(
                Capability.BOOK_L2,
                note="M4 (F21). @depth is on the /public group — a second connection, "
                "never shared with /market (04 §2)",
            ),
            unsupported(
                Capability.SPOT,
                note="M4 (F22). Spot is a different host (api.binance.com) with its own "
                "weight bucket (04 §9.3); it is not this adapter's venue",
            ),
        ),
    )


#: The sheet, built once for callers that only read it.
BINANCE_CAPABILITIES: Final[CapabilitySet] = binance_capabilities()
