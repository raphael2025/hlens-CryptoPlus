"""Shared fixtures. The repository's real config files, and a clock that only
moves when a test moves it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


# --------------------------------------------------------------------------- #
# M1-A5 — the Binance adapter's offline fixtures
# --------------------------------------------------------------------------- #
BINANCE_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "binance"


def binance_payload(name: str) -> Any:
    """One trimmed Binance fixture's payload, checked for its provenance tag.

    Every file under ``tests/fixtures/binance/`` is tagged ``source:
    documented`` and hand-built from ``docs/04-DATA-SOURCES.md`` §2's field
    table. AGENTS §3.3 requires a *recording* to be made from the production
    host's egress (task ``M1-G``), and M1-A5 sent no request of any kind;
    reading the tag here is where it will show up the day ``M1-G`` overwrites
    these files with real recordings and retags them ``live-recorded``.
    """
    document = json.loads((BINANCE_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    assert document["source"] in {"documented", "live-recorded"}, document["source"]
    return document["payload"]


def binance_fixture_source(name: str) -> str:
    document = json.loads((BINANCE_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    source: str = document["source"]
    return source
