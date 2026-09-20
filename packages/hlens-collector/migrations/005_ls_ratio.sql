-- 005_ls_ratio.sql — the long/short series (§5). Writer: `collector`.
--
-- One row per (venue, symbol, kind, ts); three kinds, no fourth (§15 struck
-- the fourth because no confirmed feature consumes it). Only Binance publishes
-- this: Hyperliquid declares the capability `unsupported` rather than deriving
-- a substitute (AGENTS §3.4, F7 "多空比不进分歧榜"), so the venue column keeps
-- its two-value domain while in practice only one value is ever written.
--
-- §6 / decision A8: polled every 10 minutes with `limit`, so both 5-minute
-- points in the window come back and each keeps its own `period`.
CREATE TABLE ls_ratio (
    ts         timestamptz NOT NULL,
    ingest_ts  timestamptz NOT NULL,
    period     integer     NOT NULL,

    venue      text        NOT NULL,
    symbol     text        NOT NULL,
    kind       text        NOT NULL,
    source     text        NOT NULL,

    long_share numeric,

    PRIMARY KEY (venue, symbol, kind, ts),

    CONSTRAINT ls_ratio_venue_is_an_exchange
        CHECK (venue IN ('binance', 'hyperliquid')),
    CONSTRAINT ls_ratio_symbol_is_unified
        CHECK (symbol ~ '^[A-Z0-9]{1,15}$'),
    CONSTRAINT ls_ratio_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),

    -- Closed domain, one value per Binance /futures/data/* endpoint. Same
    -- three as contracts.ls_ratio.LsRatioKind. They are different
    -- measurements, they are part of the primary key, and they are never
    -- averaged together.
    CONSTRAINT ls_ratio_kind_is_closed
        CHECK (kind IN ('global_long_short_account', 'top_long_short_position', 'taker_long_short')),

    -- A fraction in [0, 1], never a percentage — a reader cannot then be out
    -- by a factor of 100. Nullable: an unobserved share is NULL, never 0,
    -- because 0 reads as "nobody is long".
    CONSTRAINT ls_ratio_long_share_is_a_fraction
        CHECK (long_share IS NULL OR (long_share >= 0 AND long_share <= 1)),

    -- §5: "period（整秒，Binance period=5m → 300；与 grid_s 同单位)". Whole
    -- seconds is the column type itself — an `integer` cannot hold 300.5 — and
    -- the CHECK adds the other half of the statement: a sampling period is
    -- positive, and it is always known, because it is a parameter of the
    -- request we made. Hence NOT NULL.
    CONSTRAINT ls_ratio_period_is_positive_whole_seconds
        CHECK (period > 0)
)
PARTITION BY RANGE (ts);

COMMENT ON TABLE ls_ratio IS
    'Binance long/short points, three kinds (F7 keeps them off the divergence board). '
    'Writer: `collector` (03 §4). Permanent, never downsampled.';
COMMENT ON COLUMN ls_ratio.long_share IS
    'The long side as a fraction in [0, 1], never a percentage. NULL when unobserved, never 0.';
COMMENT ON COLUMN ls_ratio.period IS
    'The venue''s sampling period in WHOLE SECONDS (Binance period=5m -> 300), same unit as grid_s.';

CREATE INDEX ls_ratio_ts_brin ON ls_ratio USING brin (ts) WITH (pages_per_range = 32);

CREATE TABLE ls_ratio_default PARTITION OF ls_ratio DEFAULT;

-- Three months pre-created (§5); `health` keeps two future months alive.
DO $$
DECLARE
    n integer;
BEGIN
    FOR n IN 0..2 LOOP
        PERFORM hlens_ensure_month_partition(
            'ls_ratio',
            (date_trunc('month', now() AT TIME ZONE 'UTC') + (n || ' months')::interval)::date
        );
    END LOOP;
END
$$;
