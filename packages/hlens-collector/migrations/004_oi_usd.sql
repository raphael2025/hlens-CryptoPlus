-- 004_oi_usd.sql — where `market_1m.oi_usd` is computed, and what it means
-- when the two lanes arrive out of order.
--
-- §5 (2026-09-20 ruling, "premium 与 oi_usd 的裁决") states the problem and
-- hands it to M1-C: "oi_usd 是派生值，不是观测值…… 适配器一律对这两列写 None
-- …… oi_usd 在哪里算是 M1-C 必须回答的设计题：oi_usd 属慢道（60 s）、mark 属快
-- 道（30 s），而本节规定三条 upsert 语句永不合并，所以 M1-C 必须写明它是在入库
-- 边界算还是在 compute 层算，以及两道先到后到时的取值语义。没有这个答案不得开工
-- 建表."
--
-- ===========================================================================
-- 1. WHERE: at the persistence boundary, inside each upsert statement.
-- ===========================================================================
-- Not in the `compute` layer. §4's module table gives `market_1m` exactly two
-- writers — `collector` and `backfill` — and gives `compute` three other
-- tables. For `compute` to fill `oi_usd` it would have to become a third
-- writer of `market_1m`, which is precisely the ownership rule seam ③ exists
-- to hold ("表名就是接口…… 每张表只有一个写方"). The alternative — leaving the
-- column permanently NULL and recomputing the value on every read — would make
-- §5's column dead weight and would put the unit conversion in as many places
-- as there are readers. So the ingest boundary is not merely the better of two
-- options here; it is the only one that keeps the table's writer set intact.
--
-- ===========================================================================
-- 2. HOW: in SQL, inside the ON CONFLICT ... DO UPDATE, never in Python.
-- ===========================================================================
-- The two factors live in different column groups: `oi_base` is slow-lane,
-- `mark` is fast-lane. Whichever lane is writing, the other factor is already
-- in the table, not in the batch. Computing this in Python would mean a SELECT
-- of the current row before the upsert — an extra round trip per batch, and a
-- window between the SELECT and the INSERT in which the other lane can commit,
-- so the number written would be a product of a fresh factor and a factor read
-- a moment ago. Inside DO UPDATE, `market_1m.<col>` is the existing row's value
-- read under the row lock the update already holds. There is no window.
--
-- This does NOT merge the three statements (§5: "三条独立的写入语句，永远不合
-- 并"). What §5 forbids is one statement writing both lane payloads under one
-- guard, because the absent group is NULL, `NULL > x` is NULL, and the guard
-- is void. Each statement here still writes only its own payload columns,
-- still carries only its own guard, and still refuses the whole row when that
-- guard fails. `oi_usd` is not a payload column of either lane: it is a
-- function of the row's two factors, and every statement that changes a factor
-- recomputes it.
--
-- ===========================================================================
-- 3. THE ARRIVAL-ORDER SEMANTICS (the answer §5 asks for)
-- ===========================================================================
-- Invariant, one sentence: **`oi_usd` is always `oi_base * mark / mult`
-- computed from the values this row holds after the statement that is writing,
-- with `mult` taken from `instruments` as of this row's minute; if any of the
-- three is unknown it is NULL.** It is never a stale factor mixed with a fresh
-- one, and never carried over from an earlier minute.
--
-- The four cases:
--
--   a. Slow lane arrives first in a minute. `mark` is still NULL, so `oi_usd`
--      is NULL. Not 0 — §5's general rule, and F5 shows an unobserved number
--      grey rather than as a zero.
--   b. The fast lane then lands in the same minute. It writes its own group
--      AND recomputes `oi_usd` from the row's existing `oi_base` and its own
--      new `mark`. So yes: the late lane fills in the derived column. That is
--      the whole reason it is recomputed by both statements rather than only
--      by the slow one — otherwise every minute in which the slow lane won the
--      race would keep `oi_usd` NULL forever, and F7's cross-venue open
--      interest share (the one confirmed consumer of this column) would lose
--      roughly half its minutes to a write-ordering accident.
--   c. Fast lane first, slow lane second: the mirror image. The slow statement
--      computes from its own `oi_base` and the row's existing `mark`.
--   d. A stale observation on either lane: the guard fails, the DO UPDATE
--      writes nothing at all, and `oi_usd` does not move either. A late-
--      arriving old `mark` cannot revalue a fresh `oi_base`.
--
-- The cost, stated plainly: within one minute bucket the two factors can be up
-- to 30 seconds apart (fast lane 30 s, slow lane 60 s), so `oi_usd` is "this
-- minute's open interest at this minute's mark", not a simultaneous pair. The
-- minute bucket is the resolution of the whole table, and `obs_ts_fast` /
-- `obs_ts_slow` keep the exact instants of both factors on the row, so the
-- staleness is measurable rather than hidden.
--
-- ===========================================================================
-- 4. THE CROSS-TABLE READ, and the formula correction
-- ===========================================================================
-- `mult` comes from `instruments`, a different table. That read happens here,
-- as a scalar subquery inside this function, evaluated in the same statement
-- and the same transaction as the write. Unknown `mult` yields NULL, same
-- rule as `notional_usd` (§5's third "必须由数据结构本身回答的事").
--
-- **Doc correction — the formula is `oi_base * mark / mult`, not
-- `oi_base * mark * mult`.** §5 writes "它只能由 oi_base × mark × mult 算出".
-- That was true of the raw venue field, but it is not true of the column this
-- schema stores, because both adapters already applied the multiplier before
-- the value reached a contract:
--   * adapters/binance/normalize.py::normalize_open_interest —
--     "oi_base 是 the coin's own units …… the mapping's multiplier is applied
--     here", `oi_base = mapping.to_base_units(contracts)`;
--   * adapters/hyperliquid/normalize.py — the same, for `kPEPE`.
-- The venue quotes both open interest and the mark price in units of the
-- LISTED contract, so `contracts * mark` is already USD. With
-- `oi_base = contracts * mult`, USD is `oi_base * mark / mult`. Following §5
-- literally would multiply a 1000PEPE row by 10^6 — 37 USD would be recorded
-- as 37,000,000 USD — in a permanent table, on exactly the column F7 uses to
-- compare open interest across venues. AGENTS §12 ("Never silently follow the
-- doc into a wrong call") is why this file computes the corrected formula and
-- why the PR carries the correction. `mult = 1` coins, which are most of them,
-- are identical either way, which is why this would have survived review.
-- tests/test_market_1m_upserts.py::test_oi_usd_divides_by_the_multiplier pins
-- the 1000-multiplier case.

CREATE FUNCTION hlens_oi_usd(
    p_venue   text,
    p_symbol  text,
    p_ts      timestamptz,
    p_oi_base numeric,
    p_mark    numeric
)
RETURNS numeric
LANGUAGE sql
STABLE
AS $$
    SELECT p_oi_base * p_mark / (
        SELECT i.mult
          FROM instruments i
         WHERE i.venue = p_venue
           AND i.symbol = p_symbol
           AND i.mult IS NOT NULL
           AND i.mult > 0
         ORDER BY
           -- The SCD-2 version that covers this minute, when there is one.
           (i.valid_from <= p_ts AND (i.valid_to IS NULL OR i.valid_to > p_ts)) DESC,
           -- Otherwise the nearest recorded version. `mult` is a property of
           -- the listed contract, not an observation of market state, and
           -- `valid_from` records when WE first saw the contract, not when the
           -- venue defined it. Being strict here would NULL the multiplier for
           -- every minute older than our first universe reconciliation — which
           -- is every row M2-1 backfills, i.e. the whole 30-day window F8's
           -- open-interest percentile is built on. A genuine change of `mult`
           -- is what the covering test above protects; this fallback only ever
           -- fires outside the recorded span.
           abs(extract(epoch FROM (i.valid_from - p_ts))) ASC
         LIMIT 1
    )
$$;

COMMENT ON FUNCTION hlens_oi_usd(text, text, timestamptz, numeric, numeric) IS
    'market_1m.oi_usd = oi_base * mark / mult, with mult read from instruments as of the row''s '
    'minute. NULL if any factor is unknown (03 §5: 未知写 NULL，绝不写 0). Called from inside '
    'each of the three market_1m upsert statements — see this migration''s header.';
