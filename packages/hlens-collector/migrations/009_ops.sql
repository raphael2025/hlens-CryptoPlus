-- 009_ops.sql — the operational tables of §5: `source_health`, `ingest_gap`,
-- `collector_run`, `backfill_cursor`, `notify_log`, `ops_event`.
--
-- None of these is partitioned. §5's general rule is "每张时序表按月 PARTITION
-- BY RANGE (ts)", and the time-series tables are the four with a `ts` in their
-- key and a per-minute or per-event row rate — `market_1m`, `ls_ratio`,
-- `liquidations`, `divergence_1m`, which are exactly the tables §5 attaches
-- BRIN indexes and DETACH CONCURRENTLY drops to. The tables below are
-- overwrite-in-place (~25 to ~900 rows) or append-at-a-few-rows-a-day
-- (`collector_run` ~5,000/day, `notify_log` < 300/day, `ops_event` ~40/day,
-- `ingest_gap` 0 when healthy). Monthly partitions on those would add DDL that
-- can fail at 03:00 in exchange for nothing. Stated here so the omission reads
-- as a decision; see the PR.

-- --------------------------------------------------------------------------
-- source_health — one row per (venue, capability, transport), overwritten.
--
-- §4: "source_health 的观测列与判定列分属两个写方" — `collector` and
-- `preflight` write the observation columns, `health` writes the verdict
-- columns. One table, two writers, disjoint column sets; that is the one
-- exception §4 names to "每张表只有一个写方", and it is why the two groups are
-- separated and labelled here rather than interleaved.
--
-- Bloat parameters per §5, same reasoning as metric_pctl.
-- --------------------------------------------------------------------------
CREATE TABLE source_health (
    -- observation columns — writers: `collector`, `preflight`
    last_ok_ts       timestamptz,
    latency_ms       integer,
    last_error_class text,

    -- verdict columns — writer: `health`
    ok               boolean,
    consecutive_fail integer,

    ingest_ts        timestamptz NOT NULL,

    venue            text        NOT NULL,
    capability       text        NOT NULL,
    transport        text        NOT NULL,
    source           text        NOT NULL,

    PRIMARY KEY (venue, capability, transport),

    CONSTRAINT source_health_venue_is_an_exchange
        CHECK (venue IN ('binance', 'hyperliquid')),
    -- Not a closed domain on purpose: the vocabulary is
    -- adapters.capabilities.Capability (seam ②), which M4 and M5 extend by
    -- design. A CHECK here would turn opening a declared-but-unsupported
    -- capability into a migration.
    CONSTRAINT source_health_capability_is_a_tag
        CHECK (capability ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT source_health_transport_is_closed
        CHECK (transport IN ('rest', 'ws')),
    CONSTRAINT source_health_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT source_health_latency_is_not_negative
        CHECK (latency_ms IS NULL OR latency_ms >= 0),
    CONSTRAINT source_health_consecutive_fail_is_not_negative
        CHECK (consecutive_fail IS NULL OR consecutive_fail >= 0)
)
WITH (
    fillfactor = 70,
    autovacuum_vacuum_scale_factor = 0,
    autovacuum_vacuum_threshold = 100,
    autovacuum_analyze_threshold = 100
);

COMMENT ON TABLE source_health IS
    'One row per (venue, capability, transport). Observation columns written by `collector` and '
    '`preflight`; verdict columns (ok, consecutive_fail) written by `health` (03 §4).';

-- --------------------------------------------------------------------------
-- ingest_gap — one row per gap. Writers: `collector` (venue_error,
-- power_loss, host_restart, unknown), `ratelimit` (rate_limit, ip_ban),
-- `liquidation` (backpressure, ws_reconnect), `health` (egress_down),
-- `preflight`/`health` (egress_change). §5 has the full table of who writes
-- which value when.
-- --------------------------------------------------------------------------
CREATE TABLE ingest_gap (
    from_ts          timestamptz NOT NULL,
    to_ts            timestamptz,
    ingest_ts        timestamptz NOT NULL,
    minutes          integer,
    symbols_expected integer,
    symbols_present  integer,

    venue            text        NOT NULL,
    metric           text        NOT NULL,
    cause            text        NOT NULL,
    source           text        NOT NULL,

    PRIMARY KEY (venue, metric, from_ts),

    CONSTRAINT ingest_gap_venue_is_an_exchange
        CHECK (venue IN ('binance', 'hyperliquid')),
    CONSTRAINT ingest_gap_metric_is_a_tag
        CHECK (metric ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT ingest_gap_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),

    -- The ten-value closed enumeration of §5. The review that produced it
    -- found the original five-value version could not decide either of §16's
    -- acceptance measures, because both referred to values the enumeration did
    -- not contain. All ten, in §5's order:
    --   venue_error · rate_limit · ip_ban · backpressure · ws_reconnect ·
    --   power_loss · host_restart · egress_down · egress_change · unknown
    CONSTRAINT ingest_gap_cause_is_closed
        CHECK (cause IN (
            'venue_error',
            'rate_limit',
            'ip_ban',
            'backpressure',
            'ws_reconnect',
            'power_loss',
            'host_restart',
            'egress_down',
            'egress_change',
            'unknown'
        )),

    CONSTRAINT ingest_gap_is_an_interval
        CHECK (to_ts IS NULL OR to_ts > from_ts),
    CONSTRAINT ingest_gap_minutes_is_not_negative
        CHECK (minutes IS NULL OR minutes >= 0),
    CONSTRAINT ingest_gap_symbol_counts_are_not_negative
        CHECK (symbols_expected IS NULL OR symbols_expected >= 0),
    CONSTRAINT ingest_gap_present_is_within_expected
        CHECK (
            symbols_present IS NULL
            OR (symbols_present >= 0
                AND (symbols_expected IS NULL OR symbols_present <= symbols_expected))
        )
);

COMMENT ON TABLE ingest_gap IS
    'One row per ingestion gap, with a cause from §5''s ten-value closed enumeration. '
    'Permanent; 0 rows is the healthy state.';
COMMENT ON COLUMN ingest_gap.symbols_expected IS
    'How many coins the lane should have written in this span (counts, not a list).';
COMMENT ON COLUMN ingest_gap.symbols_present IS
    'How many it actually wrote. "180 expected / 3 present" is the diagnosis the status page shows.';

-- §16's availability measure A excludes the minutes in which the whole
-- machine was unavailable. Spelling that set once, here, keeps the acceptance
-- query from drifting away from §5's table.
--
-- `unknown` is NOT in the set, on purpose. §5: "把它计进排除集，它就会变成逃生
-- 口，什么都往 unknown 里塞就能让绿色率好看."
CREATE FUNCTION hlens_gap_excluded_from_availability_a(p_cause text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
RETURNS NULL ON NULL INPUT
AS $$
    SELECT p_cause IN ('power_loss', 'host_restart', 'egress_down', 'egress_change')
$$;

COMMENT ON FUNCTION hlens_gap_excluded_from_availability_a(text) IS
    'True for the four causes 03 §16''s measure A removes from its denominator. '
    '`unknown` is excluded from this set deliberately — it would otherwise be an escape hatch.';

-- --------------------------------------------------------------------------
-- collector_run — one row per collection round. Writer: `collector`.
-- --------------------------------------------------------------------------
CREATE TABLE collector_run (
    started_at   timestamptz NOT NULL,
    ingest_ts    timestamptz NOT NULL,
    rows_written integer,
    errors       integer,
    duration_ms  integer,

    lane         text        NOT NULL,
    status       text        NOT NULL,
    source       text        NOT NULL,

    PRIMARY KEY (lane, started_at),

    CONSTRAINT collector_run_lane_is_a_tag
        CHECK (lane ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT collector_run_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    -- Closed domain (§5). `running` is written when the round starts and is
    -- what makes a round that never finished visible afterwards.
    CONSTRAINT collector_run_status_is_closed
        CHECK (status IN ('running', 'ok', 'failed')),
    CONSTRAINT collector_run_counts_are_not_negative
        CHECK (
            (rows_written IS NULL OR rows_written >= 0)
            AND (errors IS NULL OR errors >= 0)
            AND (duration_ms IS NULL OR duration_ms >= 0)
        )
);

COMMENT ON TABLE collector_run IS
    'One row per collection round, per lane. Writer: `collector`. Permanent (small).';

-- --------------------------------------------------------------------------
-- backfill_cursor — where a resumable backfill job got to. Writer:
-- `backfill` (M2). §5 / decision A7: the backfill starts and stops
-- independently of the collector and must be resumable.
-- --------------------------------------------------------------------------
CREATE TABLE backfill_cursor (
    cursor_ts timestamptz,
    updated   timestamptz NOT NULL,
    ingest_ts timestamptz NOT NULL,
    done      boolean     NOT NULL DEFAULT false,

    job       text        NOT NULL,
    venue     text        NOT NULL,
    symbol    text        NOT NULL,
    metric    text        NOT NULL,
    source    text        NOT NULL,

    PRIMARY KEY (job, venue, symbol, metric),

    CONSTRAINT backfill_cursor_job_is_a_tag
        CHECK (job ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT backfill_cursor_venue_is_an_exchange
        CHECK (venue IN ('binance', 'hyperliquid')),
    CONSTRAINT backfill_cursor_symbol_is_unified
        CHECK (symbol ~ '^[A-Z0-9]{1,15}$'),
    CONSTRAINT backfill_cursor_metric_is_a_tag
        CHECK (metric ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT backfill_cursor_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$')
);

COMMENT ON TABLE backfill_cursor IS
    'Resume point per (job, venue, symbol, metric). Writer: `backfill` (M2, decision A7).';

-- --------------------------------------------------------------------------
-- notify_log — one row per message, with two-phase deduplication. Writer:
-- `notify`. 90-day retention (§5).
--
-- Two-phase: the row is claimed with ok = NULL before the send and updated to
-- true/false after it. The primary key (kind, dedupe_key) is what makes the
-- claim atomic, and it is why M2-6's liquidation push can restart without
-- either re-sending or silently dropping (F12).
-- --------------------------------------------------------------------------
CREATE TABLE notify_log (
    sent_at    timestamptz,
    ingest_ts  timestamptz NOT NULL,
    ok         boolean,

    kind       text        NOT NULL,
    dedupe_key text        NOT NULL,
    target     text        NOT NULL,
    source     text        NOT NULL,

    PRIMARY KEY (kind, dedupe_key),

    CONSTRAINT notify_log_kind_is_a_tag
        CHECK (kind ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT notify_log_dedupe_key_is_not_empty
        CHECK (dedupe_key <> ''),
    -- The channel, not an address. No secret, token, phone number or mailbox
    -- goes in this column — AGENTS §3, and this table is inside the backups
    -- that leave the machine.
    CONSTRAINT notify_log_target_is_a_tag
        CHECK (target ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT notify_log_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    -- A claimed row has not been sent yet; a resolved row has a time.
    CONSTRAINT notify_log_resolved_rows_have_a_time
        CHECK ((ok IS NULL) = (sent_at IS NULL))
);

COMMENT ON TABLE notify_log IS
    'One row per message, claimed with ok = NULL and resolved after the send (two-phase dedupe). '
    'Writer: `notify`. 90-day retention. `target` names a channel, never an address.';

-- --------------------------------------------------------------------------
-- ops_event — backups, pulls, drills, checks. Writers: `preflight`, `health`,
-- and the host-side scripts through the same tables (§4).
-- --------------------------------------------------------------------------
CREATE TABLE ops_event (
    ts        timestamptz NOT NULL,
    ingest_ts timestamptz NOT NULL,
    ok        boolean,

    kind      text        NOT NULL,
    source    text        NOT NULL,

    detail    jsonb,

    PRIMARY KEY (kind, ts),

    CONSTRAINT ops_event_kind_is_a_tag
        CHECK (kind ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT ops_event_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT ops_event_detail_is_an_object
        CHECK (detail IS NULL OR jsonb_typeof(detail) = 'object')
);

COMMENT ON TABLE ops_event IS
    'Backups, remote pulls, drills, mail self-test, partition checks. Permanent (small). '
    'The status page (F6/F18) reads its latest row per kind.';
