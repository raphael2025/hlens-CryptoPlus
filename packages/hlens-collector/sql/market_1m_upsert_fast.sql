-- market_1m_upsert_fast.sql — statement 1 of 3. NEVER merged with the other
-- two (docs/03-ARCHITECTURE.md §5).
--
-- Column group (§5's table, verbatim): mark, index_px, premium, funding_rate,
-- funding_interval_h, next_funding_ts, obs_ts_fast.
-- Guard (§5's table, verbatim):
--     WHERE excluded.obs_ts_fast > coalesce(market_1m.obs_ts_fast,'-infinity')
--        OR market_1m.backfilled
-- The second half is "实时永远可以盖掉回补".
--
-- Why the three are never merged (§5): "合并会失效：另一列组是 NULL,
-- `NULL > x` 为 NULL（假），守卫整条作废."
--
-- Three things this statement does that are not in §5's column list, each
-- deliberate:
--
--   1. `ts = date_trunc('minute', <observation instant>)`. This is the
--      persistence boundary, and §5 says the bucketing happens here and
--      nowhere else. The observation instant is kept in `obs_ts_fast`.
--   2. `oi_usd` is recomputed from the row's existing `oi_base` and this
--      statement's new `mark`. See 004_oi_usd.sql for the full ruling: it is a
--      derived column, not a payload column of either lane, and it is NOT a
--      merge — this statement still writes only its own group, still carries
--      only its own guard, and writes nothing at all when that guard fails.
--   3. `backfilled = false` and `grid_s`. A live observation that beats a
--      backfilled row makes the row a live row; leaving `backfilled = true`
--      would let the backfill statement overwrite live minute data later,
--      which is the failure §5 calls "F8 的『n 是怎么来的可追溯』当场破产".
--      `grid_s` is the second lock on that: with a live row at 60, the
--      backfill guard's `excluded.grid_s <= market_1m.grid_s` refuses a
--      300-second grid outright.
--
-- Within-group semantics: a lane's SET list assigns its whole group, NULL
-- included, because the group means "these values as of obs_ts_fast". A writer
-- must therefore send the complete group in one record; splitting a group
-- across two writes erases the half that is missing from the second.
--
-- `DISTINCT ON` is required, not decorative: two observations of the same coin
-- inside one minute bucket would otherwise make ON CONFLICT DO UPDATE try to
-- touch one row twice in one statement, which PostgreSQL refuses outright
-- ("cannot affect row a second time") — the whole batch would fail. The newest
-- observation in the bucket wins, which is the same rule the guard applies
-- across batches.

INSERT INTO market_1m (
    venue, symbol, ts, ingest_ts, source, semantic,
    mark, index_px, premium, funding_rate, funding_interval_h, next_funding_ts,
    obs_ts_fast, oi_usd, grid_s, backfilled
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
    hlens_epoch_ms(s.obs_ts_fast_ms),
    -- Insert path: this statement is creating the row, so there is no
    -- `oi_base` yet and the derived column is unknown. NULL, never 0. The
    -- slow lane fills it in when it arrives (004_oi_usd.sql, case c).
    NULL::numeric,
    coalesce(s.grid_s, 60),
    false
FROM market_1m_stage s
WHERE s.obs_ts_fast_ms IS NOT NULL
  AND NOT s.backfilled
ORDER BY
    s.venue,
    s.symbol,
    date_trunc('minute', hlens_epoch_ms(s.ts_ms)),
    s.obs_ts_fast_ms DESC
ON CONFLICT (venue, symbol, ts) DO UPDATE SET
    mark               = excluded.mark,
    index_px           = excluded.index_px,
    premium            = excluded.premium,
    funding_rate       = excluded.funding_rate,
    funding_interval_h = excluded.funding_interval_h,
    next_funding_ts    = excluded.next_funding_ts,
    obs_ts_fast        = excluded.obs_ts_fast,
    oi_usd             = hlens_oi_usd(
                             market_1m.venue,
                             market_1m.symbol,
                             market_1m.ts,
                             market_1m.oi_base,
                             excluded.mark
                         ),
    semantic           = excluded.semantic,
    grid_s             = excluded.grid_s,
    backfilled         = false,
    source             = excluded.source,
    ingest_ts          = excluded.ingest_ts
WHERE excluded.obs_ts_fast > coalesce(market_1m.obs_ts_fast, '-infinity')
   OR market_1m.backfilled;
