"""The measured bytes per ``market_1m`` row — §12's 340 B estimate, checked.

``docs/03-ARCHITECTURE.md`` §12 fixes how this must be measured and says why:

    M1-C 实测必须在 ≥ 100 万行、经历过覆盖式 upsert、VACUUM 之后测，否则量出来
    的就是没有更新痕迹的乐观值——等于自我验证一个错数。

So this test seeds at least a million rows through the real COPY-plus-upsert
path, writes every logical row three times (§12: "每逻辑行每分钟被写 3 次" —
slow lane once, fast lane twice), runs a plain ``VACUUM`` (never ``VACUUM
FULL``, which would compact away exactly the dead space being measured) and
then divides ``pg_total_relation_size`` over the partitions by the row count.

It is skipped unless ``HLENS_ROW_SIZE_ROWS`` is set, because a million rows
take minutes and every other test in this repository takes milliseconds. Run
it deliberately:

    HLENS_ROW_SIZE_ROWS=1000000 uv run pytest tests/test_market_1m_row_size.py -s

The data is synthetic and generated locally — no network request of any kind
(§3, AGENTS §2). The `source` values are the real tags the two adapters emit,
because the column is `text` and its length is part of the answer.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from conftest import ADMIN_DSN_ENV, DEFAULT_ADMIN_DSN, collector_sql, migration_files
from test_market_1m_upserts import STAGE_COLUMNS

#: §12's estimate, and the band M1's acceptance allows around it.
ESTIMATE_BYTES_PER_ROW = 340
TOLERANCE = 0.20

#: 180 coins on two venues is §5's shape (518,400 rows/day = 360 * 1440).
SYMBOLS = 180
VENUES = ("binance", "hyperliquid")
#: The tags the adapters actually write, per venue's fast lane.
SOURCES = {
    "binance": "binance_rest_premium_index",
    "hyperliquid": "hyperliquid_rest_meta_and_asset_ctxs",
}
SLOW_SOURCES = {
    "binance": "binance_rest_open_interest",
    "hyperliquid": "hyperliquid_rest_meta_and_asset_ctxs",
}

MINUTE_MS = 60_000
CHUNK_MINUTES = 120


def _requested_rows() -> int:
    raw = os.environ.get("HLENS_ROW_SIZE_ROWS")
    if raw is None:
        pytest.skip(
            "set HLENS_ROW_SIZE_ROWS=1000000 to run the §12 row-size measurement "
            "(minutes, not milliseconds)"
        )
    rows = int(raw)
    if rows < 1_000_000:
        pytest.fail(
            "03 §12 requires the measurement to be taken at >= 1,000,000 rows; "
            f"HLENS_ROW_SIZE_ROWS={rows} would measure an optimistic number"
        )
    return rows


@contextmanager
def _scratch_database() -> Iterator[str]:
    """A database of its own: VACUUM cannot run inside a transaction block."""
    admin_dsn = os.environ.get(ADMIN_DSN_ENV, DEFAULT_ADMIN_DSN)
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5)
    except psycopg.Error as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no PostgreSQL reachable (set {ADMIN_DSN_ENV}): {exc}")

    name = f"hlens_m1c_rowsize_{os.getpid()}"
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


def _symbol(index: int) -> str:
    return f"C{index:04d}"


def _declare_instruments(conn: psycopg.Connection[Any]) -> None:
    """One SCD-2 row per venue per coin, with a realistic mix of multipliers."""
    with conn.cursor().copy(
        "COPY instruments (venue, venue_symbol, valid_from, symbol, mult,"
        " funding_interval_h, tick, status, source, ingest_ts) FROM STDIN"
    ) as copy:
        for index in range(SYMBOLS):
            symbol = _symbol(index)
            mult = Decimal(1000) if index % 10 == 0 else Decimal(1)
            for venue in VENUES:
                copy.write_row(
                    (
                        venue,
                        f"{symbol}USDT" if venue == "binance" else symbol,
                        "2026-01-01 00:00:00+00",
                        symbol,
                        mult,
                        8 if venue == "binance" else 1,
                        Decimal("0.001"),
                        "trading",
                        "test_row_size",
                        "2026-01-01 00:00:00+00",
                    )
                )
    conn.commit()


def _rows(
    anchor_ms: int, minutes: range, *, lane: str, offset_ms: int
) -> Iterator[tuple[Any, ...]]:
    for minute in minutes:
        ts_ms = anchor_ms + minute * MINUTE_MS + offset_ms
        for venue_index, venue in enumerate(VENUES):
            for index in range(SYMBOLS):
                mark = Decimal("65000.12345678") + Decimal(index)
                record: dict[str, Any] = {
                    "venue": venue,
                    "symbol": _symbol(index),
                    "ts_ms": ts_ms,
                    "ingest_ts_ms": ts_ms + 120,
                    "semantic": "mark_price",
                    "backfilled": False,
                }
                if lane == "fast":
                    record |= {
                        "source": SOURCES[venue],
                        "mark": mark,
                        "index_px": mark - Decimal("3.5"),
                        "premium": (
                            Decimal("0.00012345") if venue == "hyperliquid" else None
                        ),
                        "funding_rate": Decimal("0.00010000"),
                        "funding_interval_h": 8 if venue == "binance" else 1,
                        "next_funding_ts_ms": ts_ms + 3_600_000,
                        "obs_ts_fast_ms": ts_ms,
                    }
                else:
                    record |= {
                        "source": SLOW_SOURCES[venue],
                        "oi_base": Decimal("123456.789") + Decimal(index),
                        "vol24h_usd": Decimal("987654321.12"),
                        "chg24h_pct": Decimal("-1.2345"),
                        "obs_ts_slow_ms": ts_ms,
                    }
                assert venue_index >= 0
                yield tuple(record.get(column) for column in STAGE_COLUMNS)


def _write_pass(
    conn: psycopg.Connection[Any],
    *,
    anchor_ms: int,
    total_minutes: int,
    lane: str,
    offset_ms: int,
) -> None:
    statement = collector_sql(f"market_1m_upsert_{lane}")
    stage_ddl = collector_sql("market_1m_stage")
    columns = ", ".join(STAGE_COLUMNS)
    for start in range(0, total_minutes, CHUNK_MINUTES):
        window = range(start, min(start + CHUNK_MINUTES, total_minutes))
        conn.execute(stage_ddl)
        with conn.cursor().copy(f"COPY market_1m_stage ({columns}) FROM STDIN") as copy:
            for row in _rows(anchor_ms, window, lane=lane, offset_ms=offset_ms):
                copy.write_row(row)
        conn.execute(statement)
        conn.commit()


def _sizes(conn: psycopg.Connection[Any]) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT sum(pg_total_relation_size(part.oid))::bigint  AS total,
               sum(pg_relation_size(part.oid))::bigint        AS heap,
               sum(pg_indexes_size(part.oid))::bigint         AS indexes
          FROM pg_inherits inh
          JOIN pg_class part ON part.oid = inh.inhrelid
         WHERE inh.inhparent = 'market_1m'::regclass
        """
    ).fetchone()
    assert row is not None
    total, heap, indexes = row
    count = (conn.execute("SELECT count(*) FROM market_1m").fetchone() or (0,))[0]
    return {"total": total, "heap": heap, "indexes": indexes, "rows": count}


def _seed(dsn: str, *, first_minute: int, minutes: int, anchor_ms: int) -> None:
    """One generation of rows: insert, then overwrite twice (§12's 3 writes)."""
    with psycopg.connect(dsn) as conn:
        base = anchor_ms + first_minute * MINUTE_MS
        for lane, offset in (("fast", 5_000), ("slow", 8_000), ("fast", 35_000)):
            _write_pass(
                conn, anchor_ms=base, total_minutes=minutes, lane=lane, offset_ms=offset
            )


def test_measured_bytes_per_row_matches_the_capacity_estimate() -> None:
    requested = _requested_rows()
    minutes = -(-requested // (SYMBOLS * len(VENUES)))

    with _scratch_database() as dsn:
        with psycopg.connect(dsn) as conn:
            _declare_instruments(conn)
            anchor_ms = int(
                (
                    conn.execute(
                        "SELECT (extract(epoch FROM date_trunc('month', now()"
                        " AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') * 1000)::bigint"
                    ).fetchone()
                    or (0,)
                )[0]
            )

        # Generation 1: the table as it looks after the first million rows.
        _seed(dsn, first_minute=0, minutes=minutes, anchor_ms=anchor_ms)
        with psycopg.connect(dsn, autocommit=True) as conn:
            # Plain VACUUM. VACUUM FULL would rewrite the heap and report the
            # optimistic number §12 forbids.
            conn.execute("VACUUM (ANALYZE) market_1m")
            first = _sizes(conn)

        # Generation 2: another million rows, written the same way. What the
        # capacity model actually needs is GROWTH per row — §12's own acceptance
        # is "磁盘增长在第 12 节估算的 ±20% 内". Measuring a single generation
        # counts the space the two overwrites freed but nothing has re-used yet;
        # in a table that runs for a year, generation N+1 inserts into it.
        _seed(dsn, first_minute=minutes, minutes=minutes, anchor_ms=anchor_ms)
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("VACUUM (ANALYZE) market_1m")
            second = _sizes(conn)
            filled = (
                conn.execute(
                    "SELECT oi_usd IS NOT NULL FROM market_1m"
                    " WHERE symbol = %s AND venue = 'binance' LIMIT 1",
                    (_symbol(0),),
                ).fetchone()
                or (False,)
            )[0]

    assert first["rows"] >= 1_000_000
    assert filled, "oi_usd stayed NULL; the measured row is not a fully written row"

    added_rows = second["rows"] - first["rows"]
    growth = (second["total"] - first["total"]) / added_rows
    one_shot = first["total"] / first["rows"]
    deviation = (growth - ESTIMATE_BYTES_PER_ROW) / ESTIMATE_BYTES_PER_ROW

    print(
        f"\ngeneration 1 rows           : {first['rows']:,}"
        f"\ngeneration 1 total size     : {first['total']:,} B"
        f"  (heap {first['heap']:,} / indexes {first['indexes']:,})"
        f"\ngeneration 2 rows           : {second['rows']:,}"
        f"\ngeneration 2 total size     : {second['total']:,} B"
        f"  (heap {second['heap']:,} / indexes {second['indexes']:,})"
        f"\n--"
        f"\nGROWTH per row (gen 2)      : {growth:.1f} B      <- the capacity number"
        f"\nfirst-generation size/row   : {one_shot:.1f} B      (includes freed-but-unreused space)"
        f"\n03 §12 estimate             : {ESTIMATE_BYTES_PER_ROW} B"
        f"\ndeviation of growth vs §12  : {deviation * 100:+.1f} %"
        f"\nprojected market_1m / year  : {growth * 518_400 * 365 / 1024**3:.1f} GB"
    )
    assert abs(deviation) <= TOLERANCE, (
        f"measured growth {growth:.1f} B/row is {deviation * 100:+.1f} % from §12's "
        f"{ESTIMATE_BYTES_PER_ROW} B estimate, outside the +/-{TOLERANCE:.0%} band "
        "M1's acceptance allows"
    )
