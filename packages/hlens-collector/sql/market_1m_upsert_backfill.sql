-- market_1m_upsert_backfill.sql — statement 3 of 3, and the one
-- docs/03-ARCHITECTURE.md §5 calls "本次冻结最要紧的一条".
--
-- Column group (§5): one of the two lane groups, plus `grid_s`, `semantic`
-- and `backfilled = true`.
-- Guard (§5's table, verbatim):
--     WHERE market_1m.backfilled AND excluded.grid_s <= market_1m.grid_s
--
-- Why it is its own statement, in §5's words: "M2-1 回补 30 天时 M1 已自采 ≥ 14
-- 天，重叠必然存在；共用一条 upsert 会把 5 分钟网格的回补值整片盖掉分钟级实时
-- 观测，F8 的『n 是怎么来的可追溯』当场破产." The guard's first half is the
-- lock: a backfill may only ever touch a row that is itself backfilled. A live
-- row — `backfilled = false`, written by either live statement — is invisible
-- to this statement, whatever its timestamps say.
--
-- The second half, `excluded.grid_s <= market_1m.grid_s`, is what orders two
-- backfills against each other: a finer grid may replace a coarser one, never
-- the other way round. It follows that `grid_s` on a backfilled row only ever
-- gets finer, and that the column describes the finest group on the row.
--
-- Payload columns are written with `coalesce(excluded.x, market_1m.x)` here,
-- and NOT hard-assigned the way the two live statements assign their own
-- group. A backfill job carries one group (Binance klines fill the fast
-- group, `openInterestHist` the slow one), so hard-assigning both groups would
-- wipe the other job's work every time the two jobs met on the same minute.
-- This is not the merge §5 forbids: it is still one statement, with one
-- guard, and it is the statement §5 itself describes as carrying "同上两组之
-- 一".
--
-- `ON CONFLICT` on a partitioned table needs the partition key in the conflict
-- target; the primary key contains `ts`, so it holds (§5).

INSERT INTO market_1m (
    venue, symbol, ts, ingest_ts, source, semantic,
    mark, index_px, premium, funding_rate, funding_interval_h, next_funding_ts,
    oi_base, oi_usd, vol24h_usd, chg24h_pct,
    obs_ts_fast, obs_ts_slow, grid_s, backfilled
)
SELECT DISTINCT ON (s.venue, s.symbol, date_trunc('minute', hlens_epoch_ms(s.ts_ms)))
    s.venue,
    s.symbol,
    date_trunc('minute', hlens_epoch_ms(s.ts_ms)),
    hlens_epoch_ms(s.ingest_ts_ms),
    s.source,
    s.semantic,
    s.mark,
    s.index_px,
    s.premium,
    s.funding_rate,
    s.funding_interval_h,
    hlens_epoch_ms(s.next_funding_ts_ms),
    s.oi_base,
    -- A backfill record can carry both factors at once (a kline close and an
    -- open-interest point for the same minute), so the insert path computes
    -- the derived column rather than writing NULL. Same function, same rule:
    -- unknown factor, unknown multiplier -> NULL (004_oi_usd.sql).
    hlens_oi_usd(
        s.venue,
        s.symbol,
        date_trunc('minute', hlens_epoch_ms(s.ts_ms)),
        s.oi_base,
        s.mark
    ),
    s.vol24h_usd,
    s.chg24h_pct,
    hlens_epoch_ms(s.obs_ts_fast_ms),
    hlens_epoch_ms(s.obs_ts_slow_ms),
    s.grid_s,
    true
FROM market_1m_stage s
WHERE s.backfilled
  -- The guard compares on `grid_s`; a backfilled row without one could never
  -- be overwritten by a finer grid, because the comparison would be NULL.
  AND s.grid_s IS NOT NULL
  AND (s.obs_ts_fast_ms IS NOT NULL OR s.obs_ts_slow_ms IS NOT NULL)
ORDER BY
    s.venue,
    s.symbol,
    date_trunc('minute', hlens_epoch_ms(s.ts_ms)),
    s.grid_s ASC,
    coalesce(s.obs_ts_fast_ms, s.obs_ts_slow_ms) DESC
ON CONFLICT (venue, symbol, ts) DO UPDATE SET
    mark               = coalesce(excluded.mark, market_1m.mark),
    index_px           = coalesce(excluded.index_px, market_1m.index_px),
    premium            = coalesce(excluded.premium, market_1m.premium),
    funding_rate       = coalesce(excluded.funding_rate, market_1m.funding_rate),
    funding_interval_h = coalesce(excluded.funding_interval_h, market_1m.funding_interval_h),
    next_funding_ts    = coalesce(excluded.next_funding_ts, market_1m.next_funding_ts),
    oi_base            = coalesce(excluded.oi_base, market_1m.oi_base),
    oi_usd             = hlens_oi_usd(
                             market_1m.venue,
                             market_1m.symbol,
                             market_1m.ts,
                             coalesce(excluded.oi_base, market_1m.oi_base),
                             coalesce(excluded.mark, market_1m.mark)
                         ),
    vol24h_usd         = coalesce(excluded.vol24h_usd, market_1m.vol24h_usd),
    chg24h_pct         = coalesce(excluded.chg24h_pct, market_1m.chg24h_pct),
    obs_ts_fast        = coalesce(excluded.obs_ts_fast, market_1m.obs_ts_fast),
    obs_ts_slow        = coalesce(excluded.obs_ts_slow, market_1m.obs_ts_slow),
    grid_s             = excluded.grid_s,
    semantic           = excluded.semantic,
    backfilled         = true,
    source             = excluded.source,
    ingest_ts          = excluded.ingest_ts
WHERE market_1m.backfilled
  AND excluded.grid_s <= market_1m.grid_s;
