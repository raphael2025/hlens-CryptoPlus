"""Instrument registry record -- the symbol/multiplier/tick source of truth."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, field_validator

from .base import Observation, check_finite, check_non_negative


class InstrumentStatus(StrEnum):
    trading = "trading"
    pending = "pending"
    halted = "halted"
    delisted = "delisted"
    unknown = "unknown"


class Instrument(Observation):
    """One perpetual contract at one venue.

    ``symbol`` is the hlens asset-level key (``PEPE``), ``venue_symbol`` is the raw
    exchange identity (``1000PEPE``/``kPEPE``/``PEPE_USDT``). Both are kept: they
    are not the same string and ``mult`` is what reconciles them.
    """

    venue_symbol: str
    base: str
    quote: str
    mult: Decimal = Field(default=Decimal(1), description="contract multiplier in base units")
    tick: Decimal | None = None
    qty_step: Decimal | None = None
    is_inverse: bool = False
    funding_interval_h: Decimal | None = Field(
        default=None, description="settlement period in hours; None = venue did not say"
    )
    listed_ts: int | None = None
    delisted_ts: int | None = None
    status: InstrumentStatus = InstrumentStatus.unknown

    @field_validator("mult")
    @classmethod
    def _mult_positive(cls, v: Decimal) -> Decimal:
        check_finite(v)
        if v <= 0:
            raise ValueError("contract multiplier must be > 0")
        return v

    @field_validator("tick", "qty_step")
    @classmethod
    def _steps(cls, v: Decimal | None) -> Decimal | None:
        return check_non_negative(v)

    @field_validator("funding_interval_h")
    @classmethod
    def _interval_positive(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v <= 0:
            raise ValueError("funding_interval_h must be > 0")
        return v

    @property
    def instrument_id(self) -> str:
        return f"{self.venue}:{self.venue_symbol}"
