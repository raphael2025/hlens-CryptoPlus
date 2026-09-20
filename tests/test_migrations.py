"""The schema of ``docs/03-ARCHITECTURE.md`` §5, checked against a real server.

§16's M1-C row is the list this file works through: "``\\dt`` 与第 5 节一致；下月
与下下月分区已存在、``DEFAULT`` 存在且为空；BRIN 索引在；``fillfactor`` 与
autovacuum 参数在". Everything here asks PostgreSQL what it actually built,
never what the ``.sql`` text says — a test that greps the migration for a
string it also wrote proves only that someone typed it twice.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from conftest import (
    ARCHITECTURE_TABLES,
    BRIN_TABLES,
    PARTITIONED_TABLES,
    SEAM_FOUR_TABLES,
    migration_files,
)

pytestmark = pytest.mark.usefixtures("migrated_dsn")


def _scalar(conn: psycopg.Connection[Any], sql: str, *params: Any) -> Any:
    row = conn.execute(sql, params or None).fetchone()
    assert row is not None
    return row[0]


# --------------------------------------------------------------------------- #
# The table list
# --------------------------------------------------------------------------- #
def test_public_tables_are_exactly_the_architecture_table_list(
    db: psycopg.Connection[Any],
) -> None:
    """`\\dt` in `public` is §5's table list — no more, no fewer.

    Partitions are excluded: they are storage for a table already in the list,
    not tables of their own.
    """
    rows = db.execute(
        """
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind IN ('r', 'p')
           AND NOT c.relispartition
        """
    ).fetchall()
    assert {name for (name,) in rows} == set(ARCHITECTURE_TABLES)


def test_the_migration_ledger_is_not_one_of_them(db: psycopg.Connection[Any]) -> None:
    """`schema_migrations` exists, and is deliberately outside `public`."""
    assert _scalar(db, "SELECT to_regclass('public.schema_migrations') IS NULL")
    assert _scalar(db, "SELECT to_regclass('hlens_meta.schema_migrations') IS NOT NULL")


def test_seam_four_high_frequency_tables_are_not_created(
    db: psycopg.Connection[Any],
) -> None:
    """Seam ④: M1 names `trade_tick` and friends in the documents only."""
    found = db.execute(
        """
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND c.relname = ANY(%s)
        """,
        (sorted(SEAM_FOUR_TABLES),),
    ).fetchall()
    assert found == [], f"seam ④ table created in M1: {found}"


def test_every_table_carries_source_and_ingest_ts(db: psycopg.Connection[Any]) -> None:
    """§5's general rule, on every table in the list without exception."""
    for table in sorted(ARCHITECTURE_TABLES):
        columns = {
            name
            for (name,) in db.execute(
                """
                SELECT a.attname
                  FROM pg_attribute a
                 WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
                """,
                (table,),
            ).fetchall()
        }
        assert "source" in columns, table
        assert "ingest_ts" in columns, table


def test_coin_universe_has_no_venue_column(db: psycopg.Connection[Any]) -> None:
    """§5 rules it out by name: membership is a statement about both venues."""
    assert not _scalar(
        db,
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_attribute
             WHERE attrelid = 'coin_universe'::regclass AND attname = 'venue'
               AND attnum > 0 AND NOT attisdropped
        )
        """,
    )


def test_no_column_defaults_to_zero(db: psycopg.Connection[Any]) -> None:
    """§5: "未知写 NULL，绝不写 0" — there is no zero default to fall into."""
    zeros = db.execute(
        """
        SELECT c.relname, a.attname, pg_get_expr(d.adbin, d.adrelid)
          FROM pg_attrdef d
          JOIN pg_class c ON c.oid = d.adrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
         WHERE n.nspname = 'public'
           AND pg_get_expr(d.adbin, d.adrelid) ~ '^0(\\.0*)?$'
        """
    ).fetchall()
    assert zeros == []


# --------------------------------------------------------------------------- #
# Partitions
# --------------------------------------------------------------------------- #
def test_the_four_time_series_tables_are_partitioned_by_month(
    db: psycopg.Connection[Any],
) -> None:
    partitioned = {
        name
        for (name,) in db.execute(
            """
            SELECT c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'p'
            """
        ).fetchall()
    }
    assert partitioned == set(PARTITIONED_TABLES)


@pytest.mark.parametrize("table", sorted(PARTITIONED_TABLES))
def test_three_months_are_pre_created_plus_a_default(
    db: psycopg.Connection[Any], table: str
) -> None:
    """§5: "迁移预建 3 个月 …… 另设 DEFAULT 分区兜底"."""
    row = db.execute(
        "SELECT month_partitions, has_default FROM v_partition_coverage WHERE parent = %s",
        (table,),
    ).fetchone()
    assert row is not None, table
    months, has_default = row
    assert months == 3, table
    assert has_default is True, table


@pytest.mark.parametrize("table", sorted(PARTITIONED_TABLES))
def test_this_month_and_the_next_two_are_the_ones_that_exist(
    db: psycopg.Connection[Any], table: str
) -> None:
    """§16: "下月与下下月分区已存在"."""
    expected = {
        _scalar(
            db,
            "SELECT to_char(date_trunc('month', now() AT TIME ZONE 'UTC')"
            " + make_interval(months => %s), 'YYYY\"m\"MM')",
            offset,
        )
        for offset in (0, 1, 2)
    }
    found = {
        name.rsplit("_", 1)[-1]
        for (name,) in db.execute(
            """
            SELECT c.relname
              FROM pg_class c
              JOIN pg_inherits i ON i.inhrelid = c.oid
             WHERE i.inhparent = %s::regclass
               AND pg_get_expr(c.relpartbound, c.oid) <> 'DEFAULT'
            """,
            (table,),
        ).fetchall()
    }
    assert found == expected


def test_month_partitions_are_utc_month_boundaries(db: psycopg.Connection[Any]) -> None:
    """The `+00` literal, proved from a session that is deliberately not UTC.

    §5: "分区边界一律写成带 `+00` 的字面量，容器 TZ=UTC、timezone=UTC —— 否则
    timestamptz 边界按会话时区解析，月界整体平移几小时." Kathmandu is +05:45, so
    a boundary parsed in the session's zone would be out by 5 h 45 min and the
    two rows below would land in the same partition.
    """
    db.execute("SET TIME ZONE 'Asia/Kathmandu'")
    next_month_start = _scalar(
        db,
        "SELECT (date_trunc('month', now() AT TIME ZONE 'UTC') + interval '1 month')"
        " AT TIME ZONE 'UTC'",
    )

    landed = []
    for offset_minutes in (-1, 0):
        landed.append(
            _scalar(
                db,
                """
                INSERT INTO market_1m (venue, symbol, ts, ingest_ts, source, semantic)
                VALUES ('binance', 'BTC', %s + make_interval(mins => %s), now(),
                        'test', 'mark_price')
                RETURNING tableoid::regclass::text
                """,
                next_month_start,
                offset_minutes,
            )
        )

    before, at = landed
    assert before != at, "the UTC month boundary did not separate the two rows"
    assert not before.endswith("_default") and not at.endswith("_default")


def test_a_row_with_no_month_partition_lands_in_default_and_raises_the_alarm(
    db: psycopg.Connection[Any],
) -> None:
    """§5: "DEFAULT 分区行数 > 0 是立即告警，不是状态页上的一个数字"."""
    assert db.execute("SELECT parent, rows FROM v_default_partition_alarm").fetchall() == []

    where = _scalar(
        db,
        """
        INSERT INTO market_1m (venue, symbol, ts, ingest_ts, source, semantic)
        VALUES ('binance', 'BTC', date_trunc('minute', now() + interval '5 years'),
                now(), 'test', 'mark_price')
        RETURNING tableoid::regclass::text
        """,
    )
    assert where == "market_1m_default"
    assert db.execute("SELECT parent, rows FROM v_default_partition_alarm").fetchall() == [
        ("market_1m", 1)
    ]


def test_every_default_partition_is_in_the_alarm_view(db: psycopg.Connection[Any]) -> None:
    """A new partitioned table must join both views in its own migration."""
    watched = {name for (name,) in db.execute("SELECT parent FROM v_default_partition_rows")}
    assert watched == set(PARTITIONED_TABLES)


# --------------------------------------------------------------------------- #
# Indexes and storage parameters
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("table", sorted(BRIN_TABLES))
def test_brin_on_ts_with_pages_per_range_32(db: psycopg.Connection[Any], table: str) -> None:
    """§5 names these three, and the parameter that makes them worth having."""
    rows = db.execute(
        """
        SELECT c.relname, c.reloptions, am.amname
          FROM pg_class c
          JOIN pg_index i ON i.indexrelid = c.oid
          JOIN pg_am am ON am.oid = c.relam
         WHERE i.indrelid = %s::regclass AND am.amname = 'brin'
        """,
        (table,),
    ).fetchall()
    assert len(rows) == 1, f"{table}: expected exactly one BRIN index, got {rows}"
    _, reloptions, _ = rows[0]
    assert reloptions == ["pages_per_range=32"], table


@pytest.mark.parametrize("table", sorted(BRIN_TABLES))
def test_the_brin_index_reaches_every_partition(
    db: psycopg.Connection[Any], table: str
) -> None:
    """Declared on the parent so future partitions inherit it automatically."""
    partitions, indexed = db.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1
                     FROM pg_index ix
                     JOIN pg_class ic ON ic.oid = ix.indexrelid
                     JOIN pg_am am ON am.oid = ic.relam
                    WHERE ix.indrelid = part.oid
                      AND am.amname = 'brin'
                      AND ic.reloptions = ARRAY['pages_per_range=32']
               ))
          FROM pg_inherits inh
          JOIN pg_class part ON part.oid = inh.inhrelid
         WHERE inh.inhparent = %s::regclass
        """,
        (table,),
    ).fetchone() or (0, 0)
    assert partitions == 4, table
    assert indexed == partitions, table


def test_market_1m_partitions_carry_fillfactor_80(db: psycopg.Connection[Any]) -> None:
    """§5: `market_1m` `fillfactor=80`.

    On the partitions, because PostgreSQL refuses storage parameters on a
    partitioned parent ("cannot specify storage parameters for a partitioned
    table"). Every partition, including the DEFAULT one and the ones
    `hlens_ensure_month_partition` creates later.
    """
    rows = db.execute(
        """
        SELECT part.relname, part.reloptions
          FROM pg_inherits inh
          JOIN pg_class part ON part.oid = inh.inhrelid
         WHERE inh.inhparent = 'market_1m'::regclass
        """
    ).fetchall()
    assert len(rows) == 4
    for name, reloptions in rows:
        assert reloptions == ["fillfactor=80"], name


def test_a_partition_created_later_inherits_the_storage_parameters(
    db: psycopg.Connection[Any],
) -> None:
    """The reason the helper copies them instead of taking them as an argument.

    `health` owns the partition DDL from M1-E. If it had to pass `fillfactor`
    itself, `market_1m` would silently go back to the default 100 the first
    time somebody wrote the call from memory at 03:00.
    """
    name = _scalar(
        db,
        "SELECT hlens_ensure_month_partition('market_1m',"
        " (date_trunc('month', now() AT TIME ZONE 'UTC') + interval '11 months')::date)",
    )
    assert _scalar(db, "SELECT reloptions FROM pg_class WHERE relname = %s", name) == [
        "fillfactor=80"
    ]


@pytest.mark.parametrize("table", ["metric_pctl", "metric_coverage", "source_health"])
def test_hot_small_tables_carry_the_bloat_parameters(
    db: psycopg.Connection[Any], table: str
) -> None:
    """§5, 2026-09-19 校核: fillfactor 70 plus absolute autovacuum thresholds."""
    reloptions = _scalar(db, "SELECT reloptions FROM pg_class WHERE oid = %s::regclass", table)
    assert set(reloptions or []) == {
        "fillfactor=70",
        "autovacuum_vacuum_scale_factor=0",
        "autovacuum_vacuum_threshold=100",
        "autovacuum_analyze_threshold=100",
    }, table


# --------------------------------------------------------------------------- #
# Closed domains
# --------------------------------------------------------------------------- #
def _check_clause(conn: psycopg.Connection[Any], table: str, constraint: str) -> str:
    clause = _scalar(
        conn,
        """
        SELECT pg_get_constraintdef(oid)
          FROM pg_constraint
         WHERE conrelid = %s::regclass AND conname = %s
        """,
        table,
        constraint,
    )
    assert clause is not None, f"{table}.{constraint} does not exist"
    return str(clause)


def test_the_two_scope_checks_are_identical(db: psycopg.Connection[Any]) -> None:
    """§5: one syntax, two tables. Two spellings would be two reading rules."""
    pctl = _check_clause(db, "metric_pctl", "metric_pctl_scope_is_two_segment")
    coverage = _check_clause(db, "metric_coverage", "metric_coverage_scope_is_two_segment")
    assert pctl == coverage
    assert "(binance|hyperliquid|x)" in pctl


@pytest.mark.parametrize(
    "scope,accepted",
    [
        ("binance:BTC", True),
        ("x:BTC", True),
        ("x:*", True),
        ("hyperliquid:*", True),
        ("BTC", False),
        ("market", False),
        ("binance:btc", False),
        ("bybit:BTC", False),
        ("x:", False),
        ("x:*extra", False),
    ],
)
def test_the_scope_domain(db: psycopg.Connection[Any], scope: str, accepted: bool) -> None:
    statement = (
        "INSERT INTO metric_pctl (scope, metric, updated, ingest_ts, source)"
        " VALUES (%s, 'funding_spread_8h', now(), now(), 'test')"
    )
    if accepted:
        db.execute(statement, (scope,))
    else:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(statement, (scope,))
    db.rollback()


def test_ingest_gap_cause_is_the_ten_value_enumeration(
    db: psycopg.Connection[Any],
) -> None:
    """All ten of §5's values, and nothing else."""
    clause = _check_clause(db, "ingest_gap", "ingest_gap_cause_is_closed")
    for cause in (
        "venue_error",
        "rate_limit",
        "ip_ban",
        "backpressure",
        "ws_reconnect",
        "power_loss",
        "host_restart",
        "egress_down",
        "egress_change",
        "unknown",
    ):
        assert f"'{cause}'" in clause, cause
    assert clause.count("::text") == 10, clause

    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO ingest_gap (venue, metric, from_ts, cause, ingest_ts, source)"
            " VALUES ('binance', 'market_1m', now(), 'oops', now(), 'test')"
        )
    db.rollback()


def test_unknown_is_not_excluded_from_availability_measure_a(
    db: psycopg.Connection[Any],
) -> None:
    """§5: counting it would turn `unknown` into an escape hatch."""
    excluded = {
        cause
        for (cause,) in db.execute(
            "SELECT c FROM unnest(ARRAY['venue_error','rate_limit','ip_ban','backpressure',"
            "'ws_reconnect','power_loss','host_restart','egress_down','egress_change',"
            "'unknown']) AS c WHERE hlens_gap_excluded_from_availability_a(c)"
        ).fetchall()
    }
    assert excluded == {"power_loss", "host_restart", "egress_down", "egress_change"}


def test_liquidation_completeness_cannot_be_full(db: psycopg.Connection[Any]) -> None:
    """F12: the feed is throttled, so `full` is not in the domain at all."""
    for completeness, ok in (("lower_bound", True), ("partial_history", True), ("full", False)):
        statement = (
            "INSERT INTO liquidations (venue, symbol, ts, event_id, side, throttled_source,"
            " completeness, ingest_path, ingest_ts, source)"
            " VALUES ('binance', 'BTC', date_trunc('minute', now()), 'e1', 'long', true,"
            " %s, 'binance_ws', now(), 'test')"
        )
        if ok:
            db.execute(statement, (completeness,))
        else:
            with pytest.raises(psycopg.errors.CheckViolation):
                db.execute(statement, (completeness,))
        db.rollback()


@pytest.mark.parametrize(
    "table,constraint,values",
    [
        ("market_1m", "market_1m_semantic_is_closed", ("mark_price", "candle_close")),
        (
            "ls_ratio",
            "ls_ratio_kind_is_closed",
            ("global_long_short_account", "top_long_short_position", "taker_long_short"),
        ),
        ("collector_run", "collector_run_status_is_closed", ("running", "ok", "failed")),
        ("instruments", "instruments_status_is_closed", ("trading", "suspended", "delisted")),
        (
            "liquidations",
            "liquidations_ingest_path_is_closed",
            ("binance_ws", "hl_wallet_derived"),
        ),
        ("source_health", "source_health_transport_is_closed", ("rest", "ws")),
    ],
)
def test_closed_domains_hold_exactly_their_values(
    db: psycopg.Connection[Any], table: str, constraint: str, values: tuple[str, ...]
) -> None:
    clause = _check_clause(db, table, constraint)
    for value in values:
        assert f"'{value}'" in clause, (table, value)
    assert clause.count("::text") == len(values), clause


# --------------------------------------------------------------------------- #
# The two structural guarantees market_1m carries in its own CHECKs
# --------------------------------------------------------------------------- #
def test_ts_must_be_a_minute_bucket(db: psycopg.Connection[Any]) -> None:
    """The failure §5 warns about, made unreachable.

    Writing the exchange timestamp straight into `ts` degrades the primary key
    into "one row per observation" and voids the overwrite semantics of all
    three statements. Here it is a constraint violation instead.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO market_1m (venue, symbol, ts, ingest_ts, source, semantic)"
            " VALUES ('binance', 'BTC', now(), now(), 'test', 'mark_price')"
        )
    db.rollback()
    db.execute(
        "INSERT INTO market_1m (venue, symbol, ts, ingest_ts, source, semantic)"
        " VALUES ('binance', 'BTC', date_trunc('minute', now()), now(), 'test', 'mark_price')"
    )


def test_premium_is_hyperliquid_only(db: psycopg.Connection[Any]) -> None:
    """§5's 2026-09-20 ruling, enforced rather than remembered."""
    db.execute(
        "INSERT INTO market_1m (venue, symbol, ts, ingest_ts, source, semantic, premium)"
        " VALUES ('hyperliquid', 'BTC', date_trunc('minute', now()), now(), 'test',"
        " 'mark_price', 0.0001)"
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO market_1m (venue, symbol, ts, ingest_ts, source, semantic, premium)"
            " VALUES ('binance', 'BTC', date_trunc('minute', now()), now(), 'test',"
            " 'mark_price', 0.0001)"
        )
    db.rollback()


def test_a_percentile_needs_thirty_observations(db: psycopg.Connection[Any]) -> None:
    """AGENTS §8's "n < 30 shows no percentage", made unstorable."""
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO metric_pctl (scope, metric, pctl, n, updated, ingest_ts, source)"
            " VALUES ('x:BTC', 'funding_spread_8h', 0.91, 29, now(), now(), 'test')"
        )
    db.rollback()
    db.execute(
        "INSERT INTO metric_pctl (scope, metric, pctl, n, updated, ingest_ts, source)"
        " VALUES ('x:BTC', 'funding_spread_8h', 0.91, 43200, now(), now(), 'test')"
    )


# --------------------------------------------------------------------------- #
# The boundary conversion itself
# --------------------------------------------------------------------------- #
def test_epoch_ms_round_trips_a_contract_timestamp(db: psycopg.Connection[Any]) -> None:
    """Seam ①'s UTC millisecond integer, converted where §5 says it is."""
    db.execute("SET TIME ZONE 'Asia/Kathmandu'")
    ms = 1_758_312_345_678
    back = _scalar(db, "SELECT (extract(epoch FROM hlens_epoch_ms(%s)) * 1000)::bigint", ms)
    assert back == ms


def test_the_version_gate_is_satisfied_by_this_server(db: psycopg.Connection[Any]) -> None:
    """AGENTS §9 / §3: 18.6+, asserted by 001 and again by preflight."""
    assert int(_scalar(db, "SELECT current_setting('server_version_num')")) >= 180006


def test_every_migration_is_numbered_and_unique() -> None:
    """The applied order is the file order; a duplicate number has no order."""
    numbers = [path.name[:3] for path in migration_files()]
    assert numbers, "no migrations found"
    assert len(numbers) == len(set(numbers)), numbers
    assert numbers == sorted(numbers)
