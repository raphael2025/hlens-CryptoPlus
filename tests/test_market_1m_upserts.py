"""The three ``market_1m`` write statements, and the guards that keep them apart.

``docs/03-ARCHITECTURE.md`` §5: "三条独立的写入语句，永远不合并", each with its
own guard. This file runs the real statements — read from
``packages/hlens-collector/sql/`` rather than pasted here — through the real
batch path of §3's test rules: ``cursor.copy()`` into a temp table, then
``INSERT … ON CONFLICT``. Nothing here executes a row at a time.

What each guard has to do, in §5's words:

* fast lane  — ``excluded.obs_ts_fast > coalesce(market_1m.obs_ts_fast,'-infinity')
  OR market_1m.backfilled``: a newer fast observation wins, and a live
  observation always beats a backfilled row;
* slow lane  — the same on ``obs_ts_slow``;
* backfill   — ``market_1m.backfilled AND excluded.grid_s <= market_1m.grid_s``:
  a backfill may only touch a row that is itself backfilled, and only with a
  grid at least as fine.

The backfill guard is the one §5 calls "本次冻结最要紧的一条", because M2-1
backfills 30 days over 14 days of live collection and the overlap is certain.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg
import pytest

from conftest import collector_sql

pytestmark = pytest.mark.usefixtures("migrated_dsn")

#: The staging table's columns, in the order `market_1m_stage.sql` declares
#: them — which is also the order a `MarketRecord` dumps in.
STAGE_COLUMNS = (
    "venue",
    "symbol",
    "ts_ms",
    "ingest_ts_ms",
    "source",
    "semantic",
    "mark",
    "index_px",
    "premium",
    "funding_rate",
    "funding_interval_h",
    "next_funding_ts_ms",
    "oi_base",
    "oi_usd",
    "vol24h_usd",
    "chg24h_pct",
    "obs_ts_fast_ms",
    "obs_ts_slow_ms",
    "grid_s",
    "backfilled",
)

MINUTE_MS = 60_000


def _scalar(conn: psycopg.Connection[Any], sql: str, *params: Any) -> Any:
    row = conn.execute(sql, params or None).fetchone()
    assert row is not None
    return row[0]


def minute_bucket_ms(conn: psycopg.Connection[Any]) -> int:
    """A minute bucket that is inside one of the pre-created partitions."""
    return int(
        _scalar(
            conn,
            "SELECT (extract(epoch FROM date_trunc('minute', now())) * 1000)::bigint",
        )
    )


def stage(conn: psycopg.Connection[Any], *records: dict[str, Any]) -> None:
    """COPY a batch into the staging table — §3's batch rule, not a loop of INSERTs."""
    if not _scalar(conn, "SELECT to_regclass('pg_temp.market_1m_stage') IS NOT NULL"):
        conn.execute(collector_sql("market_1m_stage"))
    else:
        conn.execute("TRUNCATE market_1m_stage")
    columns = ", ".join(STAGE_COLUMNS)
    with conn.cursor().copy(f"COPY market_1m_stage ({columns}) FROM STDIN") as copy:
        for record in records:
            unknown = set(record) - set(STAGE_COLUMNS)
            assert not unknown, unknown
            copy.write_row(tuple(record.get(column) for column in STAGE_COLUMNS))


def fast_record(ts_ms: int, obs_ms: int, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "venue": "binance",
        "symbol": "BTC",
        "ts_ms": ts_ms,
        "ingest_ts_ms": obs_ms,
        "source": "binance_rest_premium_index",
        "semantic": "mark_price",
        "mark": Decimal("65000"),
        "index_px": Decimal("64990"),
        "funding_rate": Decimal("0.0001"),
        "funding_interval_h": 8,
        "obs_ts_fast_ms": obs_ms,
        "backfilled": False,
    }
    record.update(overrides)
    return record


def slow_record(ts_ms: int, obs_ms: int, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "venue": "binance",
        "symbol": "BTC",
        "ts_ms": ts_ms,
        "ingest_ts_ms": obs_ms,
        "source": "binance_rest_open_interest",
        "semantic": "mark_price",
        "oi_base": Decimal("1000"),
        "vol24h_usd": Decimal("123456789"),
        "chg24h_pct": Decimal("-1.25"),
        "obs_ts_slow_ms": obs_ms,
        "backfilled": False,
    }
    record.update(overrides)
    return record


def row(conn: psycopg.Connection[Any], symbol: str = "BTC") -> dict[str, Any]:
    cursor = conn.execute(
        "SELECT * FROM market_1m WHERE venue = 'binance' AND symbol = %s", (symbol,)
    )
    assert cursor.description is not None
    fetched = cursor.fetchall()
    assert len(fetched) == 1, f"expected exactly one row, got {len(fetched)}"
    return dict(zip([c.name for c in cursor.description], fetched[0], strict=True))


def run(conn: psycopg.Connection[Any], statement: str) -> int:
    return conn.execute(collector_sql(statement)).rowcount


def declare_instrument(
    conn: psycopg.Connection[Any],
    *,
    symbol: str = "BTC",
    venue_symbol: str = "BTCUSDT",
    mult: Decimal | None = Decimal(1),
) -> None:
    conn.execute(
        """
        INSERT INTO instruments
            (venue, venue_symbol, valid_from, symbol, mult, funding_interval_h,
             status, source, ingest_ts)
        VALUES ('binance', %s, now() - interval '1 day', %s, %s, 8, 'trading', 'test', now())
        """,
        (venue_symbol, symbol, mult),
    )


# --------------------------------------------------------------------------- #
# The minute bucket
# --------------------------------------------------------------------------- #
def test_the_boundary_buckets_the_observation_instant(db: psycopg.Connection[Any]) -> None:
    """§5 / contracts/base.py: the contract's `ts` is an instant; the row's is a bucket."""
    bucket = minute_bucket_ms(db)
    stage(db, fast_record(bucket + 37_500, bucket + 37_500))
    run(db, "market_1m_upsert_fast")

    written = row(db)
    assert int(written["ts"].timestamp() * 1000) == bucket
    assert int(written["obs_ts_fast"].timestamp() * 1000) == bucket + 37_500


def test_two_observations_in_one_minute_make_one_row_and_the_newest_wins(
    db: psycopg.Connection[Any],
) -> None:
    """Without DISTINCT ON this batch fails outright ("cannot affect row a second time")."""
    bucket = minute_bucket_ms(db)
    stage(
        db,
        fast_record(bucket + 1_000, bucket + 1_000, mark=Decimal("1")),
        fast_record(bucket + 31_000, bucket + 31_000, mark=Decimal("2")),
    )
    run(db, "market_1m_upsert_fast")

    written = row(db)
    assert written["mark"] == Decimal("2")
    assert int(written["obs_ts_fast"].timestamp() * 1000) == bucket + 31_000


# --------------------------------------------------------------------------- #
# Guard 1 — the fast lane
# --------------------------------------------------------------------------- #
def test_the_fast_guard_refuses_an_older_observation(db: psycopg.Connection[Any]) -> None:
    bucket = minute_bucket_ms(db)
    stage(db, fast_record(bucket + 30_000, bucket + 30_000, mark=Decimal("65000")))
    run(db, "market_1m_upsert_fast")

    stage(db, fast_record(bucket + 5_000, bucket + 5_000, mark=Decimal("1")))
    affected = run(db, "market_1m_upsert_fast")

    written = row(db)
    assert affected == 0, "the guard let an older fast observation through"
    assert written["mark"] == Decimal("65000")
    assert int(written["obs_ts_fast"].timestamp() * 1000) == bucket + 30_000


def test_the_fast_guard_accepts_a_newer_observation(db: psycopg.Connection[Any]) -> None:
    bucket = minute_bucket_ms(db)
    stage(db, fast_record(bucket + 5_000, bucket + 5_000, mark=Decimal("1")))
    run(db, "market_1m_upsert_fast")
    stage(db, fast_record(bucket + 35_000, bucket + 35_000, mark=Decimal("2")))
    assert run(db, "market_1m_upsert_fast") == 1
    assert row(db)["mark"] == Decimal("2")


def test_the_fast_lane_writes_only_its_own_column_group(
    db: psycopg.Connection[Any],
) -> None:
    """The statements are not merged: a slow-lane payload in the batch is ignored."""
    bucket = minute_bucket_ms(db)
    stage(
        db,
        fast_record(
            bucket + 10_000,
            bucket + 10_000,
            oi_base=Decimal("999"),
            vol24h_usd=Decimal("42"),
            chg24h_pct=Decimal("7"),
            obs_ts_slow_ms=bucket + 10_000,
        ),
    )
    run(db, "market_1m_upsert_fast")

    written = row(db)
    assert written["mark"] == Decimal("65000")
    assert written["oi_base"] is None
    assert written["vol24h_usd"] is None
    assert written["chg24h_pct"] is None
    assert written["obs_ts_slow"] is None


# --------------------------------------------------------------------------- #
# Guard 2 — the slow lane
# --------------------------------------------------------------------------- #
def test_the_slow_guard_refuses_an_older_observation(db: psycopg.Connection[Any]) -> None:
    bucket = minute_bucket_ms(db)
    stage(db, slow_record(bucket + 30_000, bucket + 30_000, oi_base=Decimal("1000")))
    run(db, "market_1m_upsert_slow")

    stage(db, slow_record(bucket + 5_000, bucket + 5_000, oi_base=Decimal("1")))
    affected = run(db, "market_1m_upsert_slow")

    written = row(db)
    assert affected == 0, "the guard let an older slow observation through"
    assert written["oi_base"] == Decimal("1000")
    assert int(written["obs_ts_slow"].timestamp() * 1000) == bucket + 30_000


def test_the_slow_lane_does_not_touch_the_fast_columns(
    db: psycopg.Connection[Any],
) -> None:
    bucket = minute_bucket_ms(db)
    stage(db, fast_record(bucket + 1_000, bucket + 1_000, mark=Decimal("65000")))
    run(db, "market_1m_upsert_fast")
    stage(db, slow_record(bucket + 2_000, bucket + 2_000))
    run(db, "market_1m_upsert_slow")

    written = row(db)
    assert written["mark"] == Decimal("65000")
    assert int(written["obs_ts_fast"].timestamp() * 1000) == bucket + 1_000
    assert written["oi_base"] == Decimal("1000")


# --------------------------------------------------------------------------- #
# Guard 3 — the backfill
# --------------------------------------------------------------------------- #
def backfill_record(ts_ms: int, grid_s: int, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "venue": "binance",
        "symbol": "BTC",
        "ts_ms": ts_ms,
        "ingest_ts_ms": ts_ms,
        "source": "binance_rest_klines",
        "semantic": "candle_close",
        "mark": Decimal("64000"),
        "obs_ts_fast_ms": ts_ms,
        "grid_s": grid_s,
        "backfilled": True,
    }
    record.update(overrides)
    return record


def test_the_backfill_guard_refuses_to_touch_a_live_row(
    db: psycopg.Connection[Any],
) -> None:
    """§5's most important guard: a 5-minute backfill must not flatten live minutes."""
    bucket = minute_bucket_ms(db)
    stage(db, fast_record(bucket + 1_000, bucket + 1_000, mark=Decimal("65000")))
    run(db, "market_1m_upsert_fast")

    stage(db, backfill_record(bucket + 2_000, 300, mark=Decimal("1")))
    affected = run(db, "market_1m_upsert_backfill")

    written = row(db)
    assert affected == 0, "a backfill overwrote a live minute"
    assert written["mark"] == Decimal("65000")
    assert written["backfilled"] is False
    assert written["grid_s"] == 60
    assert written["semantic"] == "mark_price"


def test_the_backfill_guard_refuses_a_coarser_grid(db: psycopg.Connection[Any]) -> None:
    bucket = minute_bucket_ms(db)
    stage(db, backfill_record(bucket + 1_000, 60, mark=Decimal("64000")))
    run(db, "market_1m_upsert_backfill")

    stage(db, backfill_record(bucket + 2_000, 300, mark=Decimal("1")))
    affected = run(db, "market_1m_upsert_backfill")

    written = row(db)
    assert affected == 0, "a coarser backfill grid overwrote a finer one"
    assert written["mark"] == Decimal("64000")
    assert written["grid_s"] == 60


def test_the_backfill_guard_accepts_an_equal_or_finer_grid(
    db: psycopg.Connection[Any],
) -> None:
    bucket = minute_bucket_ms(db)
    stage(db, backfill_record(bucket + 1_000, 300, mark=Decimal("1")))
    run(db, "market_1m_upsert_backfill")

    stage(db, backfill_record(bucket + 2_000, 60, mark=Decimal("64000")))
    assert run(db, "market_1m_upsert_backfill") == 1

    written = row(db)
    assert written["mark"] == Decimal("64000")
    assert written["grid_s"] == 60
    assert written["backfilled"] is True


def test_a_live_observation_always_beats_a_backfilled_row(
    db: psycopg.Connection[Any],
) -> None:
    """The `OR market_1m.backfilled` half of the two live guards.

    The live row also stops being backfilled, which is what keeps the backfill
    statement away from it afterwards.
    """
    bucket = minute_bucket_ms(db)
    stage(db, backfill_record(bucket + 50_000, 300, mark=Decimal("1")))
    run(db, "market_1m_upsert_backfill")

    # An OLDER observation instant than the backfilled row's, on purpose: the
    # guard's second half must carry it anyway.
    stage(db, fast_record(bucket + 1_000, bucket + 1_000, mark=Decimal("65000")))
    assert run(db, "market_1m_upsert_fast") == 1

    written = row(db)
    assert written["mark"] == Decimal("65000")
    assert written["backfilled"] is False
    assert written["grid_s"] == 60


def test_a_backfill_does_not_erase_the_other_group(db: psycopg.Connection[Any]) -> None:
    """Two backfill jobs meet on one minute; neither wipes the other's columns."""
    bucket = minute_bucket_ms(db)
    stage(db, backfill_record(bucket + 1_000, 300, mark=Decimal("64000")))
    run(db, "market_1m_upsert_backfill")

    stage(
        db,
        backfill_record(
            bucket + 2_000,
            300,
            mark=None,
            obs_ts_fast_ms=None,
            oi_base=Decimal("1000"),
            obs_ts_slow_ms=bucket + 2_000,
            source="binance_rest_open_interest_hist",
        ),
    )
    assert run(db, "market_1m_upsert_backfill") == 1

    written = row(db)
    assert written["mark"] == Decimal("64000")
    assert written["oi_base"] == Decimal("1000")


# --------------------------------------------------------------------------- #
# oi_usd — the derived column (004_oi_usd.sql)
# --------------------------------------------------------------------------- #
def test_oi_usd_is_null_when_the_slow_lane_lands_first(
    db: psycopg.Connection[Any],
) -> None:
    """Case (a): no mark for this minute yet, so the derived value is unknown."""
    declare_instrument(db)
    bucket = minute_bucket_ms(db)
    stage(db, slow_record(bucket + 1_000, bucket + 1_000, oi_base=Decimal("1000")))
    run(db, "market_1m_upsert_slow")

    written = row(db)
    assert written["oi_base"] == Decimal("1000")
    assert written["oi_usd"] is None, "unknown must be NULL, never 0"


def test_the_late_fast_lane_fills_in_oi_usd(db: psycopg.Connection[Any]) -> None:
    """Case (b): the answer to §5's question, demonstrated."""
    declare_instrument(db)
    bucket = minute_bucket_ms(db)
    stage(db, slow_record(bucket + 1_000, bucket + 1_000, oi_base=Decimal("1000")))
    run(db, "market_1m_upsert_slow")
    stage(db, fast_record(bucket + 2_000, bucket + 2_000, mark=Decimal("65000")))
    run(db, "market_1m_upsert_fast")

    assert row(db)["oi_usd"] == Decimal("65000000")


def test_the_late_slow_lane_computes_oi_usd(db: psycopg.Connection[Any]) -> None:
    """Case (c): the mirror image."""
    declare_instrument(db)
    bucket = minute_bucket_ms(db)
    stage(db, fast_record(bucket + 1_000, bucket + 1_000, mark=Decimal("65000")))
    run(db, "market_1m_upsert_fast")
    stage(db, slow_record(bucket + 2_000, bucket + 2_000, oi_base=Decimal("1000")))
    run(db, "market_1m_upsert_slow")

    assert row(db)["oi_usd"] == Decimal("65000000")


def test_a_guarded_out_observation_does_not_move_oi_usd(
    db: psycopg.Connection[Any],
) -> None:
    """Case (d): a stale mark cannot revalue a fresh open interest."""
    declare_instrument(db)
    bucket = minute_bucket_ms(db)
    stage(db, fast_record(bucket + 30_000, bucket + 30_000, mark=Decimal("65000")))
    run(db, "market_1m_upsert_fast")
    stage(db, slow_record(bucket + 31_000, bucket + 31_000, oi_base=Decimal("1000")))
    run(db, "market_1m_upsert_slow")
    assert row(db)["oi_usd"] == Decimal("65000000")

    stage(db, fast_record(bucket + 1_000, bucket + 1_000, mark=Decimal("1")))
    assert run(db, "market_1m_upsert_fast") == 0
    assert row(db)["oi_usd"] == Decimal("65000000")


def test_oi_usd_divides_by_the_multiplier(db: psycopg.Connection[Any]) -> None:
    """The correction to §5's formula, on the case that distinguishes them.

    `1000PEPEUSDT`: the venue quotes open interest in contracts and the mark
    price per contract, and both adapters already convert open interest to base
    units (`mapping.to_base_units`). 5,000 contracts at 0.0074 USD each is 37
    USD; stored as 5,000,000 PEPE, the honest arithmetic is
    `oi_base * mark / mult`. §5's `* mult` would record 37,000,000 USD.
    """
    declare_instrument(db, symbol="PEPE", venue_symbol="1000PEPEUSDT", mult=Decimal(1000))
    bucket = minute_bucket_ms(db)
    stage(
        db,
        fast_record(
            bucket + 1_000, bucket + 1_000, symbol="PEPE", mark=Decimal("0.0074")
        ),
    )
    run(db, "market_1m_upsert_fast")
    stage(
        db,
        slow_record(
            bucket + 2_000, bucket + 2_000, symbol="PEPE", oi_base=Decimal("5000000")
        ),
    )
    run(db, "market_1m_upsert_slow")

    assert row(db, symbol="PEPE")["oi_usd"] == Decimal("37")


def test_oi_usd_is_null_when_the_multiplier_is_unknown(
    db: psycopg.Connection[Any],
) -> None:
    """§5, same rule as `notional_usd`: unknown multiplier, NULL — never 0, never 1."""
    bucket = minute_bucket_ms(db)
    stage(db, fast_record(bucket + 1_000, bucket + 1_000, mark=Decimal("65000")))
    run(db, "market_1m_upsert_fast")
    stage(db, slow_record(bucket + 2_000, bucket + 2_000, oi_base=Decimal("1000")))
    run(db, "market_1m_upsert_slow")

    written = row(db)
    assert written["oi_base"] == Decimal("1000")
    assert written["mark"] == Decimal("65000")
    assert written["oi_usd"] is None


def test_an_adapter_cannot_supply_oi_usd(db: psycopg.Connection[Any]) -> None:
    """§5: "适配器一律对这两列写 None". The staging table refuses anything else."""
    bucket = minute_bucket_ms(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        stage(
            db,
            slow_record(bucket + 1_000, bucket + 1_000, oi_usd=Decimal("123")),
        )
    db.rollback()


def test_oi_usd_uses_the_multiplier_in_force_for_that_minute(
    db: psycopg.Connection[Any],
) -> None:
    """The cross-table read is as-of the row's own minute, not as-of now."""
    bucket = minute_bucket_ms(db)
    db.execute(
        """
        INSERT INTO instruments
            (venue, venue_symbol, valid_from, valid_to, symbol, mult, status, source, ingest_ts)
        VALUES ('binance', 'KPEPEUSDT', now() - interval '10 days', now() - interval '1 day',
                'PEPE', 1000, 'delisted', 'test', now()),
               ('binance', '1000PEPEUSDT', now() - interval '1 day', NULL,
                'PEPE', 1, 'trading', 'test', now())
        """
    )
    old_minute = bucket - 5 * 24 * 60 * MINUTE_MS

    value = _scalar(
        db,
        "SELECT hlens_oi_usd('binance', 'PEPE', hlens_epoch_ms(%s), 5000000, 0.0074)",
        old_minute,
    )
    assert value == Decimal("37")

    value_now = _scalar(
        db,
        "SELECT hlens_oi_usd('binance', 'PEPE', hlens_epoch_ms(%s), 5000000, 0.0074)",
        bucket,
    )
    assert value_now == Decimal("37000")
