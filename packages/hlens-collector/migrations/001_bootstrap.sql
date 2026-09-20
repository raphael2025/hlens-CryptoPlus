-- 001_bootstrap.sql — M1-C step 1: the version gate, the migration ledger and
-- the two helper functions every later migration uses.
--
-- docs/03-ARCHITECTURE.md §3 / AGENTS §9: PostgreSQL 18.6+, no ORM, numbered
-- .sql migrations applied in numeric order. Nothing in this repository may
-- assume a connection pooler, an extension or a superuser beyond what is here.
--
-- Why a `hlens_meta` schema
-- ------------------------
-- §16's M1-C acceptance is "`\dt` 与第 5 节一致" — the table list in `public`
-- must be exactly §5's table list and nothing else. A migration ledger is not
-- one of §5's tables (it is infrastructure: it answers "which migrations has
-- this database had", which §5 has no opinion about), so it lives in its own
-- schema and `\dt` in `public` stays exactly §5. See the PR's "Doc
-- corrections" — this is the one table in the database that §5 does not name.

-- --------------------------------------------------------------------------
-- The version gate. preflight (M1-A3) asserts the same number before the
-- collector starts; asserting it here as well means a database that was
-- created by hand on an older server cannot silently receive this schema.
-- 180006 is the release that fixes the pg_dump overflow CVE-2026-19385, and
-- §3 requires dev and prod to run the same major version so that the restore
-- drill is not fake.
-- --------------------------------------------------------------------------
DO $$
BEGIN
    IF current_setting('server_version_num')::int < 180006 THEN
        RAISE EXCEPTION
            'hlens requires PostgreSQL 18.6+ (server_version_num >= 180006); this server is %',
            current_setting('server_version_num');
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS hlens_meta;

COMMENT ON SCHEMA hlens_meta IS
    'Infrastructure that is not part of docs/03-ARCHITECTURE.md §5''s data model. '
    'Kept out of `public` so that `\dt` lists exactly §5''s tables.';

CREATE TABLE hlens_meta.schema_migrations (
    filename    text        PRIMARY KEY,
    sha256      text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    applied_at  timestamptz NOT NULL DEFAULT now(),
    -- §5's general rule ("每张表都带 source 与 ingest_ts 两列") is about the
    -- data model, not about this ledger; carrying them anyway costs two
    -- columns and removes the question.
    source      text        NOT NULL,
    ingest_ts   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE hlens_meta.schema_migrations IS
    'One row per applied migration file, with the sha256 of the text that was applied. '
    'scripts/migrate.sh refuses to run when a recorded file''s text has changed since.';

-- --------------------------------------------------------------------------
-- UTC milliseconds -> timestamptz. Seam ① carries timestamps as UTC
-- millisecond integers; §5's general rule is "时间一律 timestamptz（契约的 UTC
-- 毫秒在入库边界转换）". This function IS that conversion, and it is written
-- once here so that every write path converts identically.
--
-- Doing it in SQL rather than in Python is deliberate: the COPY stream that
-- feeds the staging table then carries the contract's own integers, with no
-- client-side timezone in the path at all. `timestamptz 'epoch'` is an
-- absolute instant and the interval carries only microseconds, so the result
-- does not depend on the session's TimeZone.
--
-- STABLE, not IMMUTABLE: `timestamptz + interval` is marked stable by
-- PostgreSQL (interval addition can involve days and months, which are
-- timezone-dependent — not here, but volatility is a property of the operator,
-- not of the argument). That is fine for use in an INSERT ... SELECT; it does
-- mean this function must never appear in an index or a CHECK constraint.
-- --------------------------------------------------------------------------
CREATE FUNCTION hlens_epoch_ms(p_ms bigint)
RETURNS timestamptz
LANGUAGE sql
STABLE
PARALLEL SAFE
RETURNS NULL ON NULL INPUT
AS $$
    SELECT timestamptz 'epoch' + p_ms * interval '1 millisecond'
$$;

COMMENT ON FUNCTION hlens_epoch_ms(bigint) IS
    'Seam ①''s UTC millisecond integer -> timestamptz, at the persistence boundary (§5).';

-- --------------------------------------------------------------------------
-- Monthly partition DDL.
--
-- §5: "迁移预建 3 个月；health 拥有分区 DDL，启动时与每天各查一次，保证未来两个
-- 月分区存在，另设 DEFAULT 分区兜底" and "分区边界一律写成带 `+00` 的字面量,
-- 容器 TZ=UTC、timezone=UTC —— 否则 timestamptz 边界按会话时区解析，月界整体
-- 平移几小时".
--
-- The bound literals this function builds always carry `+00`, so a partition
-- created from a session in any timezone lands on the same UTC month boundary.
-- That is the belt; `TZ=UTC` in the container is the braces.
-- `tests/test_migrations.py::test_month_partitions_are_utc_month_boundaries`
-- creates one from a deliberately non-UTC session and checks where a row lands.
--
-- Storage parameters are copied from the parent's DEFAULT partition. §5 puts
-- `fillfactor=80` on `market_1m`, and PostgreSQL refuses storage parameters on
-- a partitioned parent ("cannot specify storage parameters for a partitioned
-- table"), so every partition has to carry them itself. Copying them from the
-- DEFAULT partition — which the migration creates with the right values and
-- which always exists — means the `health` module cannot create a partition
-- with the wrong ones, and cannot create one with none at all by forgetting a
-- parameter this file knows about.
-- --------------------------------------------------------------------------
CREATE FUNCTION hlens_ensure_month_partition(p_parent regclass, p_month date)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_parent  text;
    v_start   timestamp;
    v_name    text;
    v_options text;
BEGIN
    SELECT c.relname INTO v_parent FROM pg_class c WHERE c.oid = p_parent;
    v_start := date_trunc('month', p_month::timestamp);
    v_name := v_parent || '_' || to_char(v_start, 'YYYY') || 'm' || to_char(v_start, 'MM');

    IF to_regclass(quote_ident(v_name)) IS NOT NULL THEN
        RETURN v_name;
    END IF;

    SELECT array_to_string(c.reloptions, ', ')
      INTO v_options
      FROM pg_class c
      JOIN pg_inherits i ON i.inhrelid = c.oid
     WHERE i.inhparent = p_parent
       AND c.relname = v_parent || '_default';

    EXECUTE format(
        'CREATE TABLE %I PARTITION OF %s FOR VALUES FROM (%L) TO (%L)%s',
        v_name,
        p_parent::text,
        to_char(v_start, 'YYYY-MM-DD') || ' 00:00:00+00',
        to_char(v_start + interval '1 month', 'YYYY-MM-DD') || ' 00:00:00+00',
        CASE
            WHEN coalesce(v_options, '') = '' THEN ''
            ELSE ' WITH (' || v_options || ')'
        END
    );
    RETURN v_name;
END
$$;

COMMENT ON FUNCTION hlens_ensure_month_partition(regclass, date) IS
    'Create the monthly RANGE partition containing p_month if it does not exist. '
    'Bounds are +00 literals (§5); storage parameters are copied from the parent''s '
    'DEFAULT partition. Owned by the `health` module from M1-E onwards.';
