-- 010_views.sql — the two things §11's status page has to be able to ask
-- about partitions, and the one of them that is an alarm rather than a number.
--
-- ===========================================================================
-- "DEFAULT 分区行数 > 0 是立即告警，不是状态页上的一个数字" (§5)
-- ===========================================================================
-- §5 spells out why it is an alarm: once the DEFAULT partition holds rows for
-- a month, creating that month's real partition FAILS OUTRIGHT, and the
-- recovery is "lock DEFAULT -> create a table with a CHECK -> DELETE ...
-- RETURNING to move the rows -> ATTACH PARTITION", during which writes for
-- that span block. The smaller the DEFAULT partition, the cheaper that is —
-- and the alarm is what keeps it small.
--
-- `v_default_partition_alarm` returns NO ROWS when everything is well. That is
-- the shape an alarm query should have: something to page on is something
-- that came back. `v_default_partition_rows` is the status-page number, which
-- is always present and usually 0.
--
-- These are UNION ALL over the four parents that exist, not a dynamic scan of
-- the catalogue: a view cannot execute dynamic SQL, and counting rows needs a
-- real scan of a real table. A new partitioned table (M4's `trade_tick`, for
-- instance) adds its branch to both views in the migration that creates it.
-- tests/test_migrations.py::test_every_default_partition_is_in_the_alarm_view
-- fails if a parent is ever added without doing so.
-- ===========================================================================

CREATE VIEW v_default_partition_rows AS
    SELECT 'market_1m'::text AS parent, count(*) AS rows FROM market_1m_default
    UNION ALL
    SELECT 'ls_ratio'::text, count(*) FROM ls_ratio_default
    UNION ALL
    SELECT 'liquidations'::text, count(*) FROM liquidations_default
    UNION ALL
    SELECT 'divergence_1m'::text, count(*) FROM divergence_1m_default;

COMMENT ON VIEW v_default_partition_rows IS
    'Rows sitting in each DEFAULT partition. The status page number (03 §11). Normally all 0.';

CREATE VIEW v_default_partition_alarm AS
    SELECT parent, rows FROM v_default_partition_rows WHERE rows > 0;

COMMENT ON VIEW v_default_partition_alarm IS
    'ALARM, not a number (03 §5): any row returned means a month partition is missing and that '
    'month can no longer be created without moving rows under a lock. Empty is healthy.';

-- How far ahead the partitions reach, per parent — §11's "分区备到哪个月".
-- `health` keeps two future months alive; this is the view that says whether
-- it did, and the host-side systemd timer checks it without needing the
-- collector to be alive.
CREATE VIEW v_partition_coverage AS
    SELECT
        parent.relname                                        AS parent,
        count(*) FILTER (WHERE part.relispartition
                           AND pg_get_expr(part.relpartbound, part.oid) <> 'DEFAULT') AS month_partitions,
        max(
            CASE
                WHEN pg_get_expr(part.relpartbound, part.oid) = 'DEFAULT' THEN NULL
                ELSE substring(part.relname FROM '(\d{4}m\d{2})$')
            END
        )                                                     AS last_month,
        bool_or(pg_get_expr(part.relpartbound, part.oid) = 'DEFAULT') AS has_default
    FROM pg_class parent
    JOIN pg_inherits inh ON inh.inhparent = parent.oid
    JOIN pg_class part ON part.oid = inh.inhrelid
    WHERE parent.relkind = 'p'
    GROUP BY parent.relname;

COMMENT ON VIEW v_partition_coverage IS
    'Per partitioned table: how many month partitions exist, the last month covered (YYYYmMM), '
    'and whether the DEFAULT backstop is present. 03 §11''s "分区备到哪个月".';
