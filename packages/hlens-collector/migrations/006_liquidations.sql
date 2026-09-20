-- 006_liquidations.sql — observed liquidations (§5, F12). Writer:
-- `liquidation` (M2). The table exists from M1-C because §16's M1-C check is
-- "`\dt` 与第 5 节一致"; no module writes it before M2.
--
-- ===========================================================================
-- THIS TABLE IS A LOWER BOUND, AND THE SCHEMA SAYS SO
-- ===========================================================================
-- F12 / §5's third "必须由数据结构本身回答的事": Binance pushes at most one
-- (largest) liquidation per symbol per second, so the feed is throttled by
-- construction. `completeness` therefore has a two-value domain here —
-- `lower_bound` and `partial_history` — and `full` is not spellable. That is
-- the CHECK doing what the contract rule ("throttled_source=true 时契约禁止写
-- full") does, one layer lower, where a future writer cannot argue with it.
--
-- `ingest_path` separates Binance's WebSocket from M5's Hyperliquid
-- wallet-derived sample, because the export layer must refuse to add the two
-- together. §5: 导出字段名 `observed_lower_bound_usd`, and the export layer has
-- no field that can express "whole market" at all.
CREATE TABLE liquidations (
    ts               timestamptz NOT NULL,
    ingest_ts        timestamptz NOT NULL,

    throttled_source boolean     NOT NULL,

    venue            text        NOT NULL,
    symbol           text        NOT NULL,
    event_id         text        NOT NULL,
    side             text        NOT NULL,
    completeness     text        NOT NULL,
    ingest_path      text        NOT NULL,
    source           text        NOT NULL,

    price            numeric,
    size             numeric,
    notional_usd     numeric,

    PRIMARY KEY (venue, symbol, ts, event_id),

    CONSTRAINT liquidations_venue_is_an_exchange
        CHECK (venue IN ('binance', 'hyperliquid')),
    CONSTRAINT liquidations_symbol_is_unified
        CHECK (symbol ~ '^[A-Z0-9]{1,15}$'),
    CONSTRAINT liquidations_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT liquidations_event_id_is_not_empty
        CHECK (event_id <> ''),

    -- The side of the POSITION that was liquidated, not the side of the order
    -- that closed it (Binance's forceOrder carries the latter; converting it
    -- is the adapter's job). F12 keeps long and short liquidations as two
    -- separate bars and never sums them into one.
    CONSTRAINT liquidations_side_is_closed
        CHECK (side IN ('long', 'short')),

    -- Closed domain: `full` is deliberately absent (see the header).
    CONSTRAINT liquidations_completeness_is_a_lower_bound
        CHECK (completeness IN ('lower_bound', 'partial_history')),

    -- Closed domain (§5, 2026-09-19 校核 / M5).
    CONSTRAINT liquidations_ingest_path_is_closed
        CHECK (ingest_path IN ('binance_ws', 'hl_wallet_derived')),

    CONSTRAINT liquidations_price_is_not_negative
        CHECK (price IS NULL OR price >= 0),
    CONSTRAINT liquidations_size_is_not_negative
        CHECK (size IS NULL OR size >= 0),
    -- NULL when `mult` is unknown (a newly listed coin that has not reached
    -- `instruments` yet) — "不是 0、不是默认 1，推送阈值跳过 NULL 行" (§5).
    CONSTRAINT liquidations_notional_is_not_negative
        CHECK (notional_usd IS NULL OR notional_usd >= 0)
)
PARTITION BY RANGE (ts);

COMMENT ON TABLE liquidations IS
    'Observed liquidations — a LOWER BOUND by construction (F12). Writer: `liquidation` (M2). '
    'Export writes observed_lower_bound_usd and can express no whole-market total.';
COMMENT ON COLUMN liquidations.completeness IS
    'lower_bound | partial_history. `full` is not in the domain: the venue throttles the feed.';
COMMENT ON COLUMN liquidations.ingest_path IS
    'binance_ws | hl_wallet_derived. The export layer refuses to add the two together.';
COMMENT ON COLUMN liquidations.notional_usd IS
    'NULL when the multiplier is unknown — never 0, never a default of 1 (03 §5).';

CREATE INDEX liquidations_ts_brin ON liquidations USING brin (ts) WITH (pages_per_range = 32);

CREATE TABLE liquidations_default PARTITION OF liquidations DEFAULT;

-- Three months pre-created (§5); `health` keeps two future months alive.
DO $$
DECLARE
    n integer;
BEGIN
    FOR n IN 0..2 LOOP
        PERFORM hlens_ensure_month_partition(
            'liquidations',
            (date_trunc('month', now() AT TIME ZONE 'UTC') + (n || ' months')::interval)::date
        );
    END LOOP;
END
$$;
