-- market_1m_upsert_slow.sql — statement 2 of 3. NEVER merged with the other
-- two (docs/03-ARCHITECTURE.md §5).
--
-- Column group (§5's table, verbatim): oi_base, oi_usd, vol24h_usd,
-- chg24h_pct, obs_ts_slow.
-- Guard: §5's fast-lane guard "同上，换 obs_ts_slow", i.e.
--     WHERE excluded.obs_ts_slow > coalesce(market_1m.obs_ts_slow,'-infinity')
--        OR market_1m.backfilled
--
-- `oi_usd` is the one column §5 lists in this group that is not observed. The
-- venue publishes open interest in contract units only, so the USD figure is
-- computed here from this statement's `oi_base` and the row's existing `mark`
-- — which belongs to the fast lane and is therefore already in the table, not
-- in this batch. 004_oi_usd.sql carries the ruling, the arrival-order
-- semantics and the correction to §5's formula (divide by `mult`, do not
-- multiply). Read it before changing this line.
--
-- If the fast lane has not written this minute yet, `market_1m.mark` is NULL
-- and `oi_usd` is NULL — never 0 — and the fast lane fills it in when it
-- arrives. That is case (a) then (b) of 004_oi_usd.sql.
--
-- See market_1m_upsert_fast.sql for why `DISTINCT ON` is required, why the
-- SET list assigns the whole group including NULLs, and why a live statement
-- clears `backfilled`.

INSERT INTO market_1m (
    venue, symbol, ts, ingest_ts, source, semantic,
    oi_base, oi_usd, vol24h_usd, chg24h_pct,
    obs_ts_slow, grid_s, backfilled
)
SELECT DISTINCT ON (s.venue, s.symbol, date_trunc('minute', hlens_epoch_ms(s.ts_ms)))
    s.venue,
    s.symbol,
    date_trunc('minute', hlens_epoch_ms(s.ts_ms)),
    hlens_epoch_ms(s.ingest_ts_ms),
    s.source,
    s.semantic,
    s.oi_base,
    -- Insert path: this statement is creating the row, so there is no `mark`
    -- yet and the derived column is unknown. NULL, never 0.
    NULL::numeric,
    s.vol24h_usd,
    s.chg24h_pct,
    hlens_epoch_ms(s.obs_ts_slow_ms),
    coalesce(s.grid_s, 60),
    false
FROM market_1m_stage s
WHERE s.obs_ts_slow_ms IS NOT NULL
  AND NOT s.backfilled
ORDER BY
    s.venue,
    s.symbol,
    date_trunc('minute', hlens_epoch_ms(s.ts_ms)),
    s.obs_ts_slow_ms DESC
ON CONFLICT (venue, symbol, ts) DO UPDATE SET
    oi_base     = excluded.oi_base,
    oi_usd      = hlens_oi_usd(
                      market_1m.venue,
                      market_1m.symbol,
                      market_1m.ts,
                      excluded.oi_base,
                      market_1m.mark
                  ),
    vol24h_usd  = excluded.vol24h_usd,
    chg24h_pct  = excluded.chg24h_pct,
    obs_ts_slow = excluded.obs_ts_slow,
    semantic    = excluded.semantic,
    grid_s      = excluded.grid_s,
    backfilled  = false,
    source      = excluded.source,
    ingest_ts   = excluded.ingest_ts
WHERE excluded.obs_ts_slow > coalesce(market_1m.obs_ts_slow, '-infinity')
   OR market_1m.backfilled;
