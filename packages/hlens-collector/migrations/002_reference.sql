-- 002_reference.sql — `instruments` (SCD-2) and `coin_universe` (§5).
--
-- Writer: the `universe` module (§4). Nobody else writes these two tables.
--
-- §5's general rules, applied here and in every later migration:
--   * time is `timestamptz`; seam ①'s UTC millisecond integers are converted
--     at the persistence boundary by `hlens_epoch_ms()`;
--   * prices and rates are `numeric`;
--   * unknown is NULL, never 0 (there is not one DEFAULT 0 in this schema);
--   * every table carries `source` and `ingest_ts`.
--
-- The `symbol` / `venue` / `source` CHECKs below are the SQL spelling of the
-- contract types in packages/hlens-core/src/hlens_core/contracts/base.py
-- (`Symbol`, `Venue`, `SourceTag`). Keeping them identical is what makes the
-- table shape and the contract shape hold each other up.

-- --------------------------------------------------------------------------
-- instruments — one row per venue per contract per version (SCD-2).
-- PK (venue, venue_symbol, valid_from). §5's first "必须由数据结构本身回答的事":
-- valid_from/valid_to ARE the version, and /v1/instruments.json is generated
-- from this table, which is what makes F3's alignment publishable.
--
-- The table deliberately does NOT store the 8-hour funding rate. §5: "只存
-- funding_rate 与 funding_interval_h，不存折算后的 8 小时费率" — the conversion
-- (`funding_rate × 8 ÷ funding_interval_h`) is done on read by
-- MarketRecord.funding_rate_8h, so a correction to the rule never leaves a
-- table full of numbers computed under the old one.
-- --------------------------------------------------------------------------
CREATE TABLE instruments (
    -- Identity.
    venue              text        NOT NULL,
    venue_symbol       text        NOT NULL,
    valid_from         timestamptz NOT NULL,
    valid_to           timestamptz,

    -- Payload.
    symbol             text        NOT NULL,
    mult               numeric,
    funding_interval_h smallint,
    tick               numeric,
    status             text,

    -- Provenance (§5's general rule).
    source             text        NOT NULL,
    ingest_ts          timestamptz NOT NULL,

    PRIMARY KEY (venue, venue_symbol, valid_from),

    -- An instrument belongs to exactly one exchange; `x` is the cross-venue
    -- marker of §5's scope syntax and is a type error here (mirrors
    -- contracts.universe.InstrumentRecord, which narrows `venue` to the two).
    CONSTRAINT instruments_venue_is_an_exchange
        CHECK (venue IN ('binance', 'hyperliquid')),
    CONSTRAINT instruments_symbol_is_unified
        CHECK (symbol ~ '^[A-Z0-9]{1,15}$'),
    CONSTRAINT instruments_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    -- Closed domain, same three values as contracts.universe.InstrumentStatus.
    -- NULL means "the venue's own status does not map onto these three" — a
    -- guess would be worse than the gap (AGENTS §3.4).
    CONSTRAINT instruments_status_is_closed
        CHECK (status IS NULL OR status IN ('trading', 'suspended', 'delisted')),
    -- §5 / contracts: an unknown multiplier is NULL, never 1. `hlens_oi_usd()`
    -- (003) and M2's notional_usd both return NULL when it is missing, and
    -- `> 0` is what lets that function divide by it without a zero check.
    CONSTRAINT instruments_mult_is_positive
        CHECK (mult IS NULL OR mult > 0),
    CONSTRAINT instruments_funding_interval_is_positive
        CHECK (funding_interval_h IS NULL OR (funding_interval_h > 0 AND funding_interval_h <= 168)),
    CONSTRAINT instruments_tick_is_not_negative
        CHECK (tick IS NULL OR tick >= 0),
    CONSTRAINT instruments_version_is_an_interval
        CHECK (valid_to IS NULL OR valid_to > valid_from)
);

-- The as-of lookup `hlens_oi_usd()` performs on every slow-lane row, and the
-- lookup /v1/instruments.json needs: "this venue's contract for this unified
-- coin, as it stood at an instant".
CREATE INDEX instruments_symbol_asof_idx ON instruments (venue, symbol, valid_from DESC);

-- At most one open version per contract. Without this, two open rows make the
-- as-of lookup ambiguous and the SCD-2 history silently stops being a history.
CREATE UNIQUE INDEX instruments_one_open_version_idx
    ON instruments (venue, venue_symbol)
    WHERE valid_to IS NULL;

COMMENT ON TABLE instruments IS
    'SCD-2 contract metadata, one row per version. Writer: `universe` (03 §4). '
    'Source of /v1/instruments.json (F3) and of `mult` for the derived oi_usd column.';
COMMENT ON COLUMN instruments.mult IS
    'Base units per contract unit (1000 for 1000PEPE and for kPEPE). '
    'Unknown is NULL, never 1 — 03 §5''s third "必须由数据结构本身回答的事".';
COMMENT ON COLUMN instruments.funding_interval_h IS
    'The venue''s native funding interval in hours (HL 1, Binance 8, some Binance symbols 4). '
    'The normalized 8-hour rate is computed on read and is deliberately not stored.';

-- --------------------------------------------------------------------------
-- coin_universe — one row per coin per spell on the collected list.
-- PK (symbol, in_from). F1: a coin is collected exactly while BOTH venues
-- list it.
--
-- §5, verbatim: "coin_universe 不设 venue 列 —— 在册与否是关于两所的同一个陈述,
-- 契约里它取跨所标记 x，存进表里是恒定值、是死重量。本节通用规则只要求每表带
-- source 与 ingest_ts，不含 venue，故不冲突". The column is absent on purpose;
-- tests/test_migrations.py asserts its absence so that a later "consistency"
-- cleanup cannot add it back.
-- --------------------------------------------------------------------------
CREATE TABLE coin_universe (
    symbol     text        NOT NULL,
    in_from    timestamptz NOT NULL,
    in_to      timestamptz,
    reason     text,

    source     text        NOT NULL,
    ingest_ts  timestamptz NOT NULL,

    PRIMARY KEY (symbol, in_from),

    CONSTRAINT coin_universe_symbol_is_unified
        CHECK (symbol ~ '^[A-Z0-9]{1,15}$'),
    CONSTRAINT coin_universe_source_is_a_tag
        CHECK (source ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT coin_universe_reason_is_a_tag
        CHECK (reason IS NULL OR reason ~ '^[a-z0-9][a-z0-9_.:-]{0,63}$'),
    CONSTRAINT coin_universe_segment_is_an_interval
        CHECK (in_to IS NULL OR in_to > in_from)
);

-- At most one open spell per coin, for the same reason as instruments above:
-- "is this coin currently collected" must have exactly one answer.
CREATE UNIQUE INDEX coin_universe_one_open_segment_idx
    ON coin_universe (symbol)
    WHERE in_to IS NULL;

COMMENT ON TABLE coin_universe IS
    'One row per coin per spell on the collected list (F1). Writer: `universe` (03 §4). '
    'No `venue` column by 03 §5''s explicit ruling: membership is a statement about both venues.';
