-- market_1m_stage.sql — the staging table the three upsert statements read.
--
-- docs/03-ARCHITECTURE.md §3, 测试规范, last line: "批量写库走 cursor.copy() 进
-- 临时表 + INSERT … ON CONFLICT，不逐行 execute". This is that temp table. The
-- write path is always:
--
--     BEGIN
--     <this file>                                  -- create the staging table
--     COPY market_1m_stage (...) FROM STDIN        -- cursor.copy(), one stream
--     <market_1m_upsert_fast.sql>   (or _slow / _backfill)
--     COMMIT                                       -- the temp table drops here
--
-- The columns are the contract's own fields (MarketRecord.model_dump()), in
-- the contract's own units: seam ① carries timestamps as UTC millisecond
-- integers, so they arrive here as `bigint` and are converted to timestamptz
-- by `hlens_epoch_ms()` inside the upsert — the conversion §5 puts "在入库边
-- 界". Nothing client-side ever builds a timestamp, so no client timezone can
-- enter the path.
--
-- ON COMMIT DROP, so a connection returned to the pool cannot carry a stale
-- batch into the next round.

CREATE TEMP TABLE market_1m_stage (
    venue              text     NOT NULL,
    symbol             text     NOT NULL,
    -- The OBSERVATION INSTANT from the contract, not a minute bucket. The
    -- upsert computes `date_trunc('minute', ...)` from it.
    ts_ms              bigint   NOT NULL,
    ingest_ts_ms       bigint   NOT NULL,
    source             text     NOT NULL,
    semantic           text     NOT NULL,

    mark               numeric,
    index_px           numeric,
    premium            numeric,
    funding_rate       numeric,
    funding_interval_h smallint,
    next_funding_ts_ms bigint,

    oi_base            numeric,
    -- Present so that a MarketRecord can be COPY'd field for field, and
    -- pinned to NULL so that it cannot be used. §5: "oi_usd 是派生值，不是观测
    -- 值…… 适配器一律对这两列写 None". The upsert computes the column itself
    -- (004_oi_usd.sql); an adapter that ever filled this field would be
    -- silently overruled, so it fails loudly here instead.
    oi_usd             numeric  CHECK (oi_usd IS NULL),
    vol24h_usd         numeric,
    chg24h_pct         numeric,

    obs_ts_fast_ms     bigint,
    obs_ts_slow_ms     bigint,
    grid_s             smallint,
    backfilled         boolean  NOT NULL
) ON COMMIT DROP;
