-- 007_divergence_1m.sql — F7's four cross-venue metrics, per coin per minute
-- (§5). Writer: `compute` (M2). Derived and recomputable: §5 requires any
-- backfill job to recompute the affected span of this table and of
-- `metric_pctl` afterwards.
--
-- Retention is 35 rolling days, old partitions dropped — and §5 names the
-- procedure: `DETACH CONCURRENTLY` then `DROP`, so that the drop does not take
-- an ACCESS EXCLUSIVE lock on the parent while the collector is writing.
--
-- No `venue` column, for the same reason `coin_universe` has none: every row
-- is a statement about both venues at once, so naming one would be false and
-- storing the cross-venue marker `x` in every row would be dead weight. §5's
-- primary key for this table is (symbol, ts), without a venue.
CREATE TABLE divergence_1m (
    ts                timestamptz NOT NULL,
    ingest_ts         timestamptz NOT NULL,
    n_venues          smallint,

    symbol            text        NOT NULL,
    source            text        NOT NULL,

    funding_spread_8h numeric,
    mark_spread_bps   numeric,
    oi_share_bn       numeric,
    vol_share_bn      numeric,

    PRIMARY KEY (symbol, ts),

    CONSTRAINT divergence_1m_ts_is_a_minute_bucket
        CHECK (ts = date_trunc('minute', ts)),
    CONSTRAINT divergence_1m_symbol_is_unified
        CHECK (symbol ~ '^[A-Z0-9]{1,15}$'),
    CONSTRAINT divergence_1m_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    -- Two venues exist; a minute can have 0, 1 or 2 of them. With fewer than
    -- two the spreads are NULL rather than computed against a missing side —
    -- F8: "绝不用单所数据冒充两所分位".
    CONSTRAINT divergence_1m_n_venues_is_plausible
        CHECK (n_venues IS NULL OR (n_venues >= 0 AND n_venues <= 2)),
    -- Shares of the two-venue total, as fractions in [0, 1]. F7's metric is
    -- the CHANGE of these series; storing the level and differencing on read
    -- keeps one number in the table with one reading.
    CONSTRAINT divergence_1m_oi_share_is_a_fraction
        CHECK (oi_share_bn IS NULL OR (oi_share_bn >= 0 AND oi_share_bn <= 1)),
    CONSTRAINT divergence_1m_vol_share_is_a_fraction
        CHECK (vol_share_bn IS NULL OR (vol_share_bn >= 0 AND vol_share_bn <= 1))
)
PARTITION BY RANGE (ts);

COMMENT ON TABLE divergence_1m IS
    'F7''s four cross-venue metrics per coin per minute. Writer: `compute` (M2). '
    '35-day rolling retention; drop old partitions with DETACH CONCURRENTLY then DROP (03 §5).';
COMMENT ON COLUMN divergence_1m.oi_share_bn IS
    'Binance''s share of the two-venue open interest, a fraction in [0, 1]. F7 reports its change.';
COMMENT ON COLUMN divergence_1m.vol_share_bn IS
    'Binance''s share of the two-venue volume, a fraction in [0, 1]. F7 reports its change.';
COMMENT ON COLUMN divergence_1m.n_venues IS
    'How many venues contributed to this minute. Below 2 the spreads are NULL, never one-sided.';

-- No BRIN here: §5 names the three time-series tables that get one
-- (market_1m, ls_ratio, liquidations). This table is read by coin and minute
-- through its primary key, and it is 35 days long, not permanent.

CREATE TABLE divergence_1m_default PARTITION OF divergence_1m DEFAULT;

-- Three months pre-created (§5); `health` keeps two future months alive and
-- drops the ones past the 35-day retention with DETACH CONCURRENTLY + DROP.
DO $$
DECLARE
    n integer;
BEGIN
    FOR n IN 0..2 LOOP
        PERFORM hlens_ensure_month_partition(
            'divergence_1m',
            (date_trunc('month', now() AT TIME ZONE 'UTC') + (n || ' months')::interval)::date
        );
    END LOOP;
END
$$;
