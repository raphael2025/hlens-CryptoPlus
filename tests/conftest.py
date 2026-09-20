"""Shared fixtures. The repository's real config files, and a clock that only
moves when a test moves it."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
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


# --------------------------------------------------------------------------- #
# M1-A3 — preflight's own fixtures
# --------------------------------------------------------------------------- #
PREFLIGHT_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "preflight"


def preflight_fixture(name: str) -> tuple[int, Any]:
    """One trimmed preflight fixture: ``(status_code, payload)``.

    Hand-built from ``docs/04-DATA-SOURCES.md`` §2/§3/§6 and tagged ``source:
    documented``. M1-A3 sent no request of any kind: this development machine
    shares its public egress IP with the production host, and that egress
    already carries a collector that is not ours (``03`` §8, M1-B), so a single
    curiosity probe would spend the budget production is using. The 451 fixture
    in particular could not have been recorded from here at all — it is what a
    US egress sees (``04`` §6).
    """
    document = json.loads(
        (PREFLIGHT_FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    )
    assert document["source"] in {"documented", "live-recorded"}, document["source"]
    status_code: int = document["status_code"]
    return status_code, document["payload"]


# --------------------------------------------------------------------------- #
# M1-C — the schema, on a real PostgreSQL
#
# These tests need a server, because what they assert is what PostgreSQL does
# with the DDL: which partition a row lands in, whether a CHECK fires, what
# `ON CONFLICT ... WHERE` refuses to overwrite. A mock would only assert that
# the .sql files contain the strings someone typed.
#
# Connection: `HLENS_TEST_ADMIN_DSN`, defaulting to the local unix socket. No
# host, no database name and no credential is written into this repository
# (AGENTS §3) — the default is a socket path libpq builds itself. When no
# server answers, the whole group skips with the reason, so `uv run pytest -q`
# stays green on a machine without one.
# --------------------------------------------------------------------------- #
MIGRATIONS_DIR = REPO_ROOT / "packages" / "hlens-collector" / "migrations"
COLLECTOR_SQL_DIR = REPO_ROOT / "packages" / "hlens-collector" / "sql"

ADMIN_DSN_ENV = "HLENS_TEST_ADMIN_DSN"
DEFAULT_ADMIN_DSN = "postgresql:///postgres"

#: The table list of docs/03-ARCHITECTURE.md §5, which `\dt` must match.
#: `hlens_meta.schema_migrations` is not here: it is infrastructure and lives
#: in its own schema precisely so that this list stays exactly §5's.
ARCHITECTURE_TABLES: frozenset[str] = frozenset(
    {
        "instruments",
        "coin_universe",
        "market_1m",
        "ls_ratio",
        "liquidations",
        "divergence_1m",
        "metric_pctl",
        "metric_coverage",
        "source_health",
        "ingest_gap",
        "collector_run",
        "backfill_cursor",
        "notify_log",
        "ops_event",
    }
)

#: The four §5 tables partitioned monthly by `ts`.
PARTITIONED_TABLES: frozenset[str] = frozenset(
    {"market_1m", "ls_ratio", "liquidations", "divergence_1m"}
)

#: The three §5 names that carry `USING brin (ts) WITH (pages_per_range=32)`.
BRIN_TABLES: frozenset[str] = frozenset({"market_1m", "ls_ratio", "liquidations"})

#: Seam ④ (03 §2): M4's tables are named in the documents and created by no
#: M1 migration.
SEAM_FOUR_TABLES: frozenset[str] = frozenset(
    {"trade_tick", "cvd_1m", "book_l2_1m", "spot_1m", "hf_whitelist"}
)


def migration_files() -> list[Path]:
    """The numbered migrations, in the order `scripts/migrate.sh` applies them."""
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def collector_sql(name: str) -> str:
    """One statement from `packages/hlens-collector/sql/`, read from disk.

    The tests run the same text the collector will run. A copy pasted into a
    test would pass while the file it is meant to pin drifts away from it.
    """
    return (COLLECTOR_SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def migrated_dsn() -> Iterator[str]:
    """A scratch database with every migration applied, dropped afterwards.

    Session-scoped: the migrations run once. Each test gets its own
    transaction and rolls it back, so the tests do not see each other's rows.
    """
    admin_dsn = os.environ.get(ADMIN_DSN_ENV, DEFAULT_ADMIN_DSN)
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5)
    except psycopg.Error as exc:  # pragma: no cover - depends on the machine
        pytest.skip(
            f"no PostgreSQL reachable for the M1-C schema tests "
            f"(set {ADMIN_DSN_ENV}; tried {admin_dsn!r}): {exc}"
        )

    name = f"hlens_m1c_test_{os.getpid()}"
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.execute(f'CREATE DATABASE "{name}"')

    dsn = psycopg.conninfo.make_conninfo(admin_dsn, dbname=name)
    try:
        with psycopg.connect(dsn) as conn:
            for path in migration_files():
                conn.execute(path.read_text(encoding="utf-8"))
            conn.commit()
        yield dsn
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as cleanup:
            cleanup.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def db(migrated_dsn: str) -> Iterator[psycopg.Connection[Any]]:
    """A connection in a transaction that is always rolled back.

    Deliberately NOT autocommit: `market_1m_stage.sql` creates its temp table
    `ON COMMIT DROP`, which is the shape §3's batch-write rule requires, and
    the rollback is what keeps one test's staged batch out of the next one.
    """
    with psycopg.connect(migrated_dsn) as conn:
        try:
            yield conn
        finally:
            conn.rollback()
