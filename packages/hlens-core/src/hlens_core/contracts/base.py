"""Normalized-data foundations: the source envelope every observation carries.

ADAPTER-RESEARCH §4.2/§4.3 hard rules encoded here:
  * prices and sizes are ``Decimal``, never float;
  * time is Unix **milliseconds UTC**, always;
  * ``ts`` is the data event time, ``ingest_ts`` is when this host received it --
    both are kept so clock skew stays measurable;
  * unknown is ``None``, never ``0``.
"""

from __future__ import annotations

import time
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now_ms() -> int:
    """Current wall clock in UTC milliseconds."""
    return int(time.time() * 1000)


class Transport(StrEnum):
    rest = "rest"
    ws = "ws"
    file = "file"


class Quality(StrEnum):
    fresh = "fresh"
    stale = "stale"
    partial = "partial"
    estimated = "estimated"
    error = "error"


class Completeness(StrEnum):
    full = "full"
    lower_bound = "lower_bound"
    unknown = "unknown"


class Semantic(StrEnum):
    exchange_reported = "exchange_reported"
    normalized = "normalized"
    derived = "derived"
    modeled = "modeled"


Price = Annotated[Decimal, Field(description="Decimal, never float")]

_FINITE_MSG = "must be a finite number"


class Observation(BaseModel):
    """Base of every normalized record.

    ``source`` names the endpoint the value came from (``/fapi/v1/premiumIndex``,
    ``ws:!markPrice@arr@1s``, ``info:metaAndAssetCtxs``) so a stored row can always
    be traced back to one request.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    venue: str
    symbol: str
    ts: int = Field(description="event time, UTC ms")
    ingest_ts: int = Field(default_factory=now_ms, description="local receipt time, UTC ms")
    source: str = Field(description="endpoint that produced this record")
    transport: Transport = Transport.rest
    quality: Quality = Quality.fresh
    completeness: Completeness = Completeness.full
    semantic: Semantic = Semantic.exchange_reported

    @field_validator("ts", "ingest_ts")
    @classmethod
    def _plausible_ms(cls, v: int) -> int:
        # 2015-01-01 .. 2100-01-01 in ms. Catches seconds/microseconds mix-ups,
        # which are the single most common normalization bug across venues.
        if not (1_420_070_400_000 <= v <= 4_102_444_800_000):
            raise ValueError(f"timestamp {v} is not plausible UTC milliseconds")
        return v

    @property
    def age_ms(self) -> int:
        return self.ingest_ts - self.ts


def check_finite(v: Decimal | None) -> Decimal | None:
    if v is None:
        return None
    if not v.is_finite():
        raise ValueError(_FINITE_MSG)
    return v


def check_non_negative(v: Decimal | None) -> Decimal | None:
    check_finite(v)
    if v is not None and v < 0:
        raise ValueError("must be >= 0")
    return v
