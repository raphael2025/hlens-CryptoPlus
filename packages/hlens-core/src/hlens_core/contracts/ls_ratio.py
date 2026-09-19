"""The long/short ratio point — seam ① for the ``ls_ratio`` row (F3).

§5: one row per ``(venue, symbol, kind, ts)``, carrying ``long_share`` and
``period``. Three kinds, no fourth — §15 struck the fourth because no confirmed
feature consumes it.

Only Binance publishes this. Hyperliquid declares the capability
``unsupported`` rather than deriving a substitute (``03`` §4 / AGENTS §3.4), so
a Hyperliquid long/short point should not exist. Enforcing *that* is the
adapter's capability declaration (seam ②), not this contract's job; the
contract's job is to make sure whatever is recorded states its kind, its share
and its sampling period.

``ts`` is the instant of the published point, not a bucket — see
:mod:`hlens_core.contracts.base`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from .base import SingleVenueRecord, UnitShare

__all__ = ["LsRatioKind", "LsRatioPoint"]


class LsRatioKind(StrEnum):
    """The three kinds §5 records, one per Binance ``/futures/data/*`` endpoint.

    All three are accounted against the separate ``futures_data`` request
    bucket, not the weight bucket (§6.1, AGENTS §2.2).
    """

    GLOBAL_LONG_SHORT_ACCOUNT = "global_long_short_account"
    """``/futures/data/globalLongShortAccountRatio`` — share of all accounts
    that are net long."""

    TOP_LONG_SHORT_POSITION = "top_long_short_position"
    """``/futures/data/topLongShortPositionRatio`` — share of top traders'
    position value that is long."""

    TAKER_LONG_SHORT = "taker_long_short"
    """``/futures/data/takerlongshortRatio`` — share of taker volume that
    bought, i.e. ``buyVol ÷ (buyVol + sellVol)``."""


#: The venue's sampling period in seconds. Binance's ``period=5m`` is 300; §6
#: polls every 10 minutes with ``limit`` so both 5-minute points in the window
#: come back, and each point keeps its own 300.
PeriodSeconds = Annotated[int, Field(gt=0)]


class LsRatioPoint(SingleVenueRecord):
    """One published long/short point, of one kind, for one coin."""

    semantic: None = None
    """Pinned to ``None``: a participation share is not a price, so the mark
    price / candle close distinction has no meaning for it. Pinned rather than
    defaulted so no caller can attach a misleading value."""

    kind: LsRatioKind
    """Which of the three ratios this is. Part of the primary key — the kinds
    are different measurements and are never averaged together."""

    long_share: UnitShare | None
    """The long side as a fraction in ``[0, 1]`` — never a percentage, so a
    reader cannot be off by a factor of 100. Required but nullable: an
    unobserved share is ``None``, never ``0``, because ``0`` would read as
    "nobody is long" (§5's general rule)."""

    period: PeriodSeconds
    """The venue's sampling period for this point, in seconds. Always known —
    it is a parameter of the request we made — so it is required and not
    nullable."""
