from __future__ import annotations

import json
import pathlib

import pytest

from hlens_core.ratelimit import Budget

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def fx():
    """Load a recorded response: ``fx("binance/premiumIndex")``."""

    def load(name: str):
        return json.loads((FIXTURES / f"{name}.json").read_text())

    return load


@pytest.fixture(autouse=True)
def _clean_budget_registry():
    # Budgets are process-wide singletons per (egress_ip, venue); tests must not
    # inherit another test's ledger.
    Budget.reset_registry()
    yield
    Budget.reset_registry()


class FakeClock:
    """Manual monotonic clock; ``sleep`` advances it instead of waiting."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
