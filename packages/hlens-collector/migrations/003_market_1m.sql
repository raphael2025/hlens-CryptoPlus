-- 003_market_1m.sql — the minute series (§5), its partitions, and the one
-- derived column in the schema: `oi_usd`.
--
-- Writers: `collector` (live) and `backfill` (rows with backfilled = true).
-- Three independent write statements, which live in
-- packages/hlens-collector/sql/market_1m_upsert_{fast,slow,backfill}.sql and
-- are NEVER merged (§5). Read that directory before changing anything here.
--
-- ===========================================================================
-- `ts` IS A MINUTE BUCKET, NOT AN OBSERVATION INSTANT
-- ===========================================================================
-- §5: "market_1m.ts 是分钟桶：ts = date_trunc('minute', 观测时刻)，原始观测时刻
-- 只存在 obs_ts_* 里 …… 照契约「ts 是这条记录的时间」直译会把交易所时间戳写进
-- ts，主键立刻退化成「每次观测一行」，整套覆盖语义作废."
--
-- The contracts carry the observation instant in `MarketRecord.ts` and do no
-- bucketing at all (see contracts/base.py's module docstring). The truncation
-- happens in the three upsert statements — that is the persistence boundary —
-- and `market_1m_ts_is_a_minute_bucket` below is the structural guarantee that
-- no other write path can get it wrong.
--
-- ===========================================================================
-- SEAM ④ — the high-frequency tables are NAMED HERE AND NOT CREATED
-- ===========================================================================
-- §2 seam ④ / §5: `trade_tick` (daily partitions, ≤ 7 day rolling retention,
-- dropped whole), `cvd_1m` (derived minute series, permanent), `book_l2_1m`,
-- `spot_1m` and `hf_whitelist` belong to M4 and are named in the documents
-- only. M1 creates none of them, and this is the file where someone would be
-- tempted to: F19/F20 turn on the minute-level derived series, not the raw
-- ticks, and putting per-trade rows into `market_1m` is the exact failure §2
-- prices at "M4 一开分钟链路跟着崩".
-- tests/test_migrations.py::test_seam_four_high_frequency_tables_are_not_created
-- fails if any of those five names appears in the database.
--
-- ===========================================================================
-- COLUMN ORDER
-- ===========================================================================
-- Fixed-width columns first, then the variable-length ones. §12's 340 B/row
-- estimate budgets 10 bytes of alignment padding; putting the five 8-byte
-- timestamps ahead of the varlena columns is how that stays a budget rather
-- than an underestimate. The reading order of §5's column list is preserved
-- inside each group.
CREATE TABLE market_1m (
    -- ---- fixed width, 8 bytes ------------------------------------------
    ts                 timestamptz NOT NULL,
    obs_ts_fast        timestamptz,
    obs_ts_slow        timestamptz,
    next_funding_ts    timestamptz,
    ingest_ts          timestamptz NOT NULL,

    -- ---- fixed width, small --------------------------------------------
    funding_interval_h smallint,
    grid_s             smallint,
    backfilled         boolean     NOT NULL DEFAULT false,

    -- ---- variable width -------------------------------------------------
    venue              text        NOT NULL,
    symbol             text        NOT NULL,
    semantic           text        NOT NULL,
    source             text        NOT NULL,

    mark               numeric,
    index_px           numeric,
    premium            numeric,
    funding_rate       numeric,
    oi_base            numeric,
    oi_usd             numeric,
    vol24h_usd         numeric,
    chg24h_pct         numeric,

    PRIMARY KEY (venue, symbol, ts),

    -- The minute bucket, enforced. A row whose `ts` still carries seconds is
    -- a contract timestamp that was written through unconverted, and it would
    -- turn the primary key into "one row per observation".
    CONSTRAINT market_1m_ts_is_a_minute_bucket
        CHECK (ts = date_trunc('minute', ts)),

    CONSTRAINT market_1m_venue_is_an_exchange
        CHECK (venue IN ('binance', 'hyperliquid')),
    CONSTRAINT market_1m_symbol_is_unified
        CHECK (symbol ~ '^[A-Z0-9]{1,15}$'),
    CONSTRAINT market_1m_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),

    -- Closed domain (§5: "闭值域 mark_price | candle_close，M1-C 的 CHECK 用这
    -- 两个字面量"). Same two values as contracts.base.Semantic.
    CONSTRAINT market_1m_semantic_is_closed
        CHECK (semantic IN ('mark_price', 'candle_close')),

    -- §5's premium ruling (2026-09-20, after M1-A5/A6): "premium 只有
    -- Hyperliquid 有…… Binance 的 premiumIndex 发的是 markPrice indexPrice
    -- lastFundingRate nextFundingTime，没有 premium…… 禁止用 mark − index 顶替
    -- …… Binance 侧永久写 NULL." Enforced rather than documented, because the
    -- substitution it forbids is a one-line change that nobody would notice in
    -- review — and a single Binance premium row would make the column mean two
    -- different things forever.
    CONSTRAINT market_1m_premium_is_hyperliquid_only
        CHECK (premium IS NULL OR venue = 'hyperliquid'),

    -- Signs. Prices, sizes and volumes cannot be negative; funding rates,
    -- premia and 24 h changes are signed and have no bound here.
    CONSTRAINT market_1m_mark_is_not_negative
        CHECK (mark IS NULL OR mark >= 0),
    CONSTRAINT market_1m_oi_base_is_not_negative
        CHECK (oi_base IS NULL OR oi_base >= 0),
    CONSTRAINT market_1m_oi_usd_is_not_negative
        CHECK (oi_usd IS NULL OR oi_usd >= 0),
    CONSTRAINT market_1m_vol24h_is_not_negative
        CHECK (vol24h_usd IS NULL OR vol24h_usd >= 0),

    CONSTRAINT market_1m_funding_interval_is_positive
        CHECK (funding_interval_h IS NULL OR (funding_interval_h > 0 AND funding_interval_h <= 168)),
    -- Whole seconds, positive. 60 for M1's own collection, 300 for M2's
    -- 5-minute backfill grid, 28800 for the 8-hour funding grid — all within
    -- smallint, which is also what §12's byte estimate budgets for it.
    CONSTRAINT market_1m_grid_is_positive
        CHECK (grid_s IS NULL OR grid_s > 0)
)
PARTITION BY RANGE (ts);

COMMENT ON TABLE market_1m IS
    'The permanent minute series (F2), one row per venue per coin per minute bucket. '
    'Writers: `collector` and `backfill`, through three upsert statements that are never merged (03 §5).';
COMMENT ON COLUMN market_1m.ts IS
    'MINUTE BUCKET — date_trunc(''minute'', observation instant). The raw instants are '
    'obs_ts_fast / obs_ts_slow. 03 §5; contracts/base.py.';
COMMENT ON COLUMN market_1m.obs_ts_fast IS
    'Observation instant of the fast-lane group. The fast-lane upsert guard compares on it.';
COMMENT ON COLUMN market_1m.obs_ts_slow IS
    'Observation instant of the slow-lane group. The slow-lane upsert guard compares on it.';
COMMENT ON COLUMN market_1m.premium IS
    'Hyperliquid only, permanently NULL on Binance (03 §5, 2026-09-20 ruling). '
    'Never mark - index: HL''s premium is its own oracle-based construction.';
COMMENT ON COLUMN market_1m.funding_rate IS
    'RAW rate over the venue''s native interval. The 8-hour figure is computed on read.';
COMMENT ON COLUMN market_1m.oi_usd IS
    'DERIVED, not observed: oi_base * mark / mult, computed by hlens_oi_usd() inside each '
    'upsert statement. NULL whenever oi_base, mark or mult is unknown. See 004_oi_usd.sql.';
COMMENT ON COLUMN market_1m.grid_s IS
    'Spacing of the grid this row sits on, in whole seconds: 60 live, 300 backfilled OI, '
    '28800 backfilled funding. Part of F8''s "n 的来源永远可追溯", and the second lock that '
    'keeps a coarse backfill from overwriting a live minute.';

-- --------------------------------------------------------------------------
-- BRIN on ts (§5, 2026-09-19 校核): "health 每分钟的缺口查询与爆仓 5 分钟窗口
-- 查询否则是全分区顺序扫描，月末 5 GB；不用 btree，那是 +50 B/行".
-- Declared on the partitioned parent so that every existing and future
-- partition gets it — including the ones `health` creates at 03:00 with
-- nobody watching — and `pages_per_range = 32` propagates to each child index.
-- --------------------------------------------------------------------------
CREATE INDEX market_1m_ts_brin ON market_1m USING brin (ts) WITH (pages_per_range = 32);

-- --------------------------------------------------------------------------
-- Partitions. §5: DEFAULT as the backstop, three months pre-created here,
-- `health` keeps two future months alive from M1-E on (plus the host systemd
-- timer, so the check does not depend on the collector being alive).
--
-- `fillfactor = 80` (§5, 2026-09-19 校核): "market_1m 每逻辑行每分钟被写 3 次,
-- 默认 100 走不了 HOT，每次更新都写新索引条目". PostgreSQL refuses storage
-- parameters on a partitioned parent, so it goes on each partition;
-- hlens_ensure_month_partition() copies it from the DEFAULT partition created
-- here, which is why the DEFAULT one is created first.
--
-- NOTE for M2-1: a 30-day backfill can reach into a month that has no
-- partition yet. Call hlens_ensure_month_partition() for every month the job
-- touches before writing — the DEFAULT partition is a backstop and an alarm
-- (010_views.sql), not a destination.
-- --------------------------------------------------------------------------
CREATE TABLE market_1m_default PARTITION OF market_1m DEFAULT WITH (fillfactor = 80);

DO $$
DECLARE
    n integer;
BEGIN
    FOR n IN 0..2 LOOP
        PERFORM hlens_ensure_month_partition(
            'market_1m',
            (date_trunc('month', now() AT TIME ZONE 'UTC') + (n || ' months')::interval)::date
        );
    END LOOP;
END
$$;
