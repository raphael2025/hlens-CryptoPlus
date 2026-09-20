"""Shared fixtures. The repository's real config files, and a clock that only
moves when a test moves it."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from hlens_core.ratelimit import FakeClock, LedgerConfig, RateLimitLedger

REPO_ROOT = Path(__file__).resolve().parents[1]
VENUES_PATH = REPO_ROOT / "config" / "venues.yaml"
CONSUMERS_PATH = REPO_ROOT / "config" / "egress-consumers.yaml"


#: Marks ``hub_legacy``'s Hyperliquid reservation in
#: ``config/egress-consumers.yaml``. Until M1-B that reservation was the
#: literal 960 placeholder and these helpers matched on the digits; now it is a
#: measured value that will be re-measured (04 §11 第 14 項 asks for another
#: pass a week before retirement and 24 h after), so the tests anchor on a
#: marker that survives the number changing instead.
HL_RESERVATION_MARKER = "# M1-B-MEASURED-HL-RESERVATION"

_HL_RESERVATION_RE = re.compile(
    re.escape(HL_RESERVATION_MARKER) + r"\n(?P<indent>[ ]*)reserved_per_min: \d+"
)


def set_hl_reservation(text: str | None, body: str) -> str:
    """Rewrite ``hub_legacy``'s Hyperliquid reservation to ``body``.

    ``body`` is the YAML that replaces the ``reserved_per_min:`` line, given
    without indentation; each line is indented to match the file. Returning
    text rather than a patched object keeps the test honest: it goes back
    through the real loader, including the rule that a reservation of 0 must
    carry its assertion.
    """
    source = CONSUMERS_PATH.read_text(encoding="utf-8") if text is None else text
    match = _HL_RESERVATION_RE.search(source)
    if match is None:
        raise AssertionError(
            f"{HL_RESERVATION_MARKER} is not where these helpers expect it in "
            "config/egress-consumers.yaml; update this helper and the M1-B report"
        )
    indent = match.group("indent")
    replacement = HL_RESERVATION_MARKER + "\n" + "\n".join(
        indent + line if line else "" for line in body.splitlines()
    )
    return source[: match.start()] + replacement + source[match.end() :]


def reclaimed_consumers_yaml(text: str | None = None) -> str:
    """Step ② of §6.1's reclamation procedure, applied to the file's text.

    "`venues.yaml` 里 `reserved.hyperliquid.hub_legacy` 由 960 改 0" — the
    reservations moved to `config/egress-consumers.yaml` when that file was
    split out, but the step is the same one, and it is still one number.
    """
    return set_hl_reservation(
        text,
        "reserved_per_min: 0\n"
        "assertion: >-\n"
        "  hub_legacy has been stopped and cannot be restarted\n"
        "  (03 §6.1 retirement step 1).\n"
        "checked_by: preflight_each_start",
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


# --------------------------------------------------------------------------- #
# M1-A6 — the Hyperliquid adapter's offline fixtures
# --------------------------------------------------------------------------- #
HYPERLIQUID_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "hyperliquid"


def hyperliquid_payload(name: str) -> Any:
    """One trimmed Hyperliquid fixture's payload, checked for its provenance tag.

    Every file under ``tests/fixtures/hyperliquid/`` is tagged ``source:
    documented`` and hand-built from ``docs/04-DATA-SOURCES.md`` §3's field
    table. AGENTS §3.3 requires a *recording* to be made from the production
    host's egress (task ``M1-G``); this development machine shares that egress
    with a still-running collector **of this same venue**, and M1-A6 sent no
    request of any kind. Reading the tag here is where it will show up the day
    ``M1-G`` overwrites these files and retags them ``live-recorded``.
    """
    document = json.loads(
        (HYPERLIQUID_FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    )
    assert document["source"] in {"documented", "live-recorded"}, document["source"]
    return document["payload"]


def hyperliquid_fixture_source(name: str) -> str:
    document = json.loads(
        (HYPERLIQUID_FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    )
    source: str = document["source"]
    return source
