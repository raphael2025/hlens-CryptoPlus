"""Shared fixtures. The repository's real config files, and a clock that only
moves when a test moves it."""

from __future__ import annotations

from pathlib import Path

import pytest

from hlens_core.ratelimit import FakeClock, LedgerConfig, RateLimitLedger

REPO_ROOT = Path(__file__).resolve().parents[1]
VENUES_PATH = REPO_ROOT / "config" / "venues.yaml"
CONSUMERS_PATH = REPO_ROOT / "config" / "egress-consumers.yaml"


def reclaimed_consumers_yaml(text: str | None = None) -> str:
    """Step ② of §6.1's reclamation procedure, applied to the file's text.

    "`venues.yaml` 里 `reserved.hyperliquid.hub_legacy` 由 960 改 0" — the
    reservations moved to `config/egress-consumers.yaml` when that file was
    split out, but the step is the same one. Returning text rather than a
    patched object keeps the test honest: it goes through the real loader,
    including the rule that a reservation of 0 must carry its assertion.
    """
    source = CONSUMERS_PATH.read_text(encoding="utf-8") if text is None else text
    if "reserved_per_min: 960" not in source:
        raise AssertionError("the 960 placeholder is gone; update this helper and §6.1")
    return source.replace(
        "reserved_per_min: 960",
        "reserved_per_min: 0\n"
        "          assertion: >-\n"
        "            hub_legacy has been stopped and cannot be restarted\n"
        "            (03 §6.1 retirement step 1).\n"
        "          checked_by: preflight_each_start",
        1,
    )


@pytest.fixture
def venues_path() -> Path:
    return VENUES_PATH


@pytest.fixture
def consumers_path() -> Path:
    return CONSUMERS_PATH


@pytest.fixture
def config() -> LedgerConfig:
    """The real ``config/*.yaml``, not a fixture copy.

    These tests exist to pin the numbers the architecture decided, so they have
    to read the file the collector will read. A separate test copy would go
    stale exactly when it mattered.
    """
    return LedgerConfig.load(VENUES_PATH, CONSUMERS_PATH)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def ledger(config: LedgerConfig, clock: FakeClock) -> RateLimitLedger:
    return RateLimitLedger(config, clock=clock)
