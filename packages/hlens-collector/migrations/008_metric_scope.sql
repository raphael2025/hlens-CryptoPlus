-- 008_metric_scope.sql — `metric_pctl` and `metric_coverage` (§5). Writer:
-- `compute` (M2); `metric_coverage` is also written by `backfill`.
--
-- ===========================================================================
-- `scope` — one syntax, two tables, one CHECK spelled identically
-- ===========================================================================
-- §5 settles this at length ("scope 值域的裁决（两张表共用一套，本文一次性定义）"):
--
--     scope := <venue>:<symbol>
--     venue  ∈ { binance | hyperliquid | x }     -- x = 跨所
--     symbol ∈ { 统一币名 | * }                   -- * = 全市场（不指定币）
--     CHECK (scope ~ '^(binance|hyperliquid|x):([A-Z0-9]{1,15}|\*)$')
--
-- `binance:BTC` · `x:BTC` · `x:*` (F10's market row `market_funding_median`
-- lives here) · `hyperliquid:*` (M5). The two tables MUST agree, or M2 gets
-- two reading rules for one string and the join that asks "does this
-- percentile have enough coverage" stops lining up;
-- tests/test_migrations.py::test_the_two_scope_checks_are_identical compares
-- the two constraint bodies character by character.
--
-- ===========================================================================
-- Bloat parameters (§5, 2026-09-19 校核) — part of the CREATE TABLE, not an
-- afterthought: `metric_pctl` is a ~900-row table taking roughly two million
-- updates a day, and with default settings it bloats without fail.
-- `fillfactor = 70` leaves room for HOT updates in the page;
-- `autovacuum_vacuum_scale_factor = 0` with a threshold of 100 makes
-- autovacuum trigger on an absolute row count instead of a fraction of a tiny
-- table (5 % of 900 rows would be 45 dead tuples — the default never fires in
-- time on a table this small and this hot).
-- ===========================================================================

CREATE TABLE metric_pctl (
    window_from timestamptz,
    window_to   timestamptz,
    updated     timestamptz NOT NULL,
    ingest_ts   timestamptz NOT NULL,
    n           integer,
    grid_s      smallint,

    scope       text        NOT NULL,
    metric      text        NOT NULL,
    source      text        NOT NULL,

    value       numeric,
    pctl        numeric,

    PRIMARY KEY (scope, metric),

    CONSTRAINT metric_pctl_scope_is_two_segment
        CHECK (scope ~ '^(binance|hyperliquid|x):([A-Z0-9]{1,15}|\*)$'),
    CONSTRAINT metric_pctl_metric_is_a_tag
        CHECK (metric ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT metric_pctl_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    -- A fraction in [0, 1], never a percentage; the "91%" of F8's sentence is
    -- produced by the export layer, which is also where the language lives.
    CONSTRAINT metric_pctl_pctl_is_a_fraction
        CHECK (pctl IS NULL OR (pctl >= 0 AND pctl <= 1)),
    CONSTRAINT metric_pctl_n_is_not_negative
        CHECK (n IS NULL OR n >= 0),
    CONSTRAINT metric_pctl_grid_is_positive
        CHECK (grid_s IS NULL OR grid_s > 0),
    CONSTRAINT metric_pctl_window_is_an_interval
        CHECK (window_from IS NULL OR window_to IS NULL OR window_to > window_from),
    -- AGENTS §8, made structural: "n < 30 shows no percentage". A percentile
    -- stored against fewer than 30 observations is a number that no layer is
    -- allowed to display, so it is not storable either. The export layer still
    -- suppresses it as well — this constraint is there so that a future writer
    -- cannot put one in the table and leave a reader to remember the rule.
    CONSTRAINT metric_pctl_percentile_needs_thirty_observations
        CHECK (pctl IS NULL OR (n IS NOT NULL AND n >= 30))
)
WITH (
    fillfactor = 70,
    autovacuum_vacuum_scale_factor = 0,
    autovacuum_vacuum_threshold = 100,
    autovacuum_analyze_threshold = 100
);

COMMENT ON TABLE metric_pctl IS
    'Latest percentile per (scope, metric), overwritten in place (F8). Writer: `compute` (M2).';
COMMENT ON COLUMN metric_pctl.pctl IS
    'Fraction in [0, 1], never a percentage. NULL until n >= 30 (AGENTS §8).';
COMMENT ON COLUMN metric_pctl.n IS
    'Observation points in the window, NOT days — 30 days of minutes is n = 43,200 (F8).';

CREATE TABLE metric_coverage (
    first_ts       timestamptz,
    self_from      timestamptz,
    backfill_from  timestamptz,
    ingest_ts      timestamptz NOT NULL,
    days_available integer,
    grid_s_min     smallint,
    enough         boolean     NOT NULL,

    scope          text        NOT NULL,
    metric         text        NOT NULL,
    source         text        NOT NULL,

    PRIMARY KEY (scope, metric),

    -- Identical to metric_pctl's, by §5's ruling. Do not "tidy" one of them.
    CONSTRAINT metric_coverage_scope_is_two_segment
        CHECK (scope ~ '^(binance|hyperliquid|x):([A-Z0-9]{1,15}|\*)$'),
    CONSTRAINT metric_coverage_metric_is_a_tag
        CHECK (metric ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT metric_coverage_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT metric_coverage_days_is_not_negative
        CHECK (days_available IS NULL OR days_available >= 0),
    CONSTRAINT metric_coverage_grid_is_positive
        CHECK (grid_s_min IS NULL OR grid_s_min > 0)
)
WITH (
    fillfactor = 70,
    autovacuum_vacuum_scale_factor = 0,
    autovacuum_vacuum_threshold = 100,
    autovacuum_analyze_threshold = 100
);

COMMENT ON TABLE metric_coverage IS
    'How much history each (scope, metric) actually has (F8). Writers: `compute` and `backfill`. '
    'enough = false means the page says "数据积累中，已有 N 天" and metric_pctl carries no pctl.';
COMMENT ON COLUMN metric_coverage.self_from IS
    'Earliest self-collected point. Kept apart from backfill_from so that F8''s "n 是怎么来的" '
    'stays answerable per metric.';
