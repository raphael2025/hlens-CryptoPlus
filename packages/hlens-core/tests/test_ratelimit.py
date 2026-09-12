"""Budget accounting: sliding window, reserve/settle, AIMD, the two HL 429s."""

from __future__ import annotations

import pytest

from hlens_core.ratelimit import (
    Budget,
    BudgetExhausted,
    RateLimitKind,
    classify_rate_limit,
    hl_weight_for,
    load_venues,
    venue,
)


@pytest.fixture
def hl(clock):
    return Budget("203.0.113.7", venue("hyperliquid"), clock=clock, sleep=clock.sleep)


@pytest.fixture
def binance(clock):
    return Budget("203.0.113.7", venue("binance"), clock=clock, sleep=clock.sleep)


# -- config ------------------------------------------------------------------


def test_venues_yaml_matches_documented_budgets():
    v = load_venues()
    assert (v["binance"].main.limit, v["binance"].main.budget) == (2400, 960)
    assert (v["hyperliquid"].main.limit, v["hyperliquid"].main.budget) == (1200, 1080)
    assert v["hyperliquid"].max_inflight == 10
    assert (v["gate"].main.budget, v["gate"].main.window_s) == (40, 10.0)
    assert (v["bitget"].main.budget, v["bitget"].main.window_s) == (8, 1.0)
    assert (v["bybit"].main.budget, v["bybit"].main.window_s) == (10, 5.0)  # 120/min
    assert {b.source for b in v.values() for b in [b.main]} <= {
        "official", "measured", "unverified"
    }


def test_every_venue_budget_is_under_its_limit():
    for spec in load_venues().values():
        assert spec.main.budget <= spec.main.limit, spec.name


def test_hl_weight_table():
    assert hl_weight_for("clearinghouseState") == 2
    assert hl_weight_for("metaAndAssetCtxs") == 20
    assert hl_weight_for("userRole") == 60
    assert hl_weight_for("somethingNew") == 20  # unknown types are over-charged, never under
    # history endpoints are billed per rows returned: +1 per 20, per 60 for candles
    assert hl_weight_for("userFillsByTime", rows=100) == 5
    assert hl_weight_for("candleSnapshot", rows=120) == 2
    assert hl_weight_for("userFillsByTime") == 120  # the reserve-time upper bound


# -- sliding window ----------------------------------------------------------


async def test_sliding_window_frees_after_window(binance, clock):
    for _ in range(96):
        await binance.reserve("/fapi/v1/ticker/24hr")  # 40 weight each = 3840 > 960
    assert binance.used() == pytest.approx(960)
    assert binance.available == 0
    clock.advance(59)
    assert binance.used() == pytest.approx(960)  # still inside the 60 s window
    clock.advance(2)
    assert binance.used() == 0


async def test_reserve_waits_instead_of_overspending(binance, clock):
    for _ in range(24):
        await binance.reserve("/fapi/v1/ticker/24hr")
    t0 = clock.t
    await binance.reserve("/fapi/v1/ticker/24hr")
    assert clock.t > t0, "should have slept until the window rolled"
    assert binance.stats.waits == 1


async def test_reserve_without_wait_raises(binance):
    for _ in range(24):
        await binance.reserve("/fapi/v1/ticker/24hr")
    with pytest.raises(BudgetExhausted):
        await binance.reserve("/fapi/v1/ticker/24hr", wait=False)


async def test_request_bigger_than_budget_is_refused_not_awaited(binance):
    with pytest.raises(BudgetExhausted, match="split the request"):
        await binance.reserve("/fapi/v1/klines", weight=2000)


# -- reserve / settle --------------------------------------------------------


async def test_settle_refunds_an_over_estimate(hl):
    res = await hl.reserve("userFillsByTime")  # upper bound 120
    assert hl.used() == 120
    delta = hl.settle(res, hl_weight_for("userFillsByTime", rows=40))  # 2 rows-weight
    assert delta == -118
    assert hl.used() == 2
    assert hl.stats.refunded == 118


async def test_settle_charges_an_under_estimate(hl):
    res = await hl.reserve("clearinghouseState")
    hl.settle(res, 30)
    assert hl.used() == 30
    assert hl.stats.overdrawn == 28


async def test_double_settle_is_an_error(hl):
    res = await hl.reserve("clearinghouseState")
    hl.settle(res, 2)
    with pytest.raises(RuntimeError, match="already settled"):
        hl.settle(res, 2)


async def test_call_context_settles_at_the_reserved_amount(hl):
    async with hl.call("clearinghouseState") as res:
        assert not res.settled
    assert res.settled and hl.used() == 2


async def test_call_context_holds_an_inflight_slot(hl):
    assert hl.max_inflight == 10
    async with hl.call("clearinghouseState"):
        assert hl._inflight == 1
    assert hl._inflight == 0


# -- per-endpoint buckets ----------------------------------------------------


async def test_binance_funding_endpoints_share_their_own_500_per_5min_bucket(binance, clock):
    for _ in range(200):
        await binance.reserve("/fapi/v1/fundingRate")
    with pytest.raises(BudgetExhausted):
        await binance.reserve("/fapi/v1/fundingInfo", wait=False)
    # the weight ledger is untouched by the sub-bucket being full
    await binance.reserve("/fapi/v1/premiumIndex", wait=False)
    clock.advance(301)
    await binance.reserve("/fapi/v1/fundingInfo", wait=False)


async def test_okx_limits_are_per_endpoint(clock):
    okx = Budget("203.0.113.7", venue("okx"), clock=clock, sleep=clock.sleep)
    for _ in range(2):
        await okx.reserve("/rubik/stat/taker-volume-contract")
    with pytest.raises(BudgetExhausted):
        await okx.reserve("/rubik/stat/taker-volume-contract", wait=False)
    await okx.reserve("/market/tickers", wait=False)  # a different endpoint, own bucket


# -- AIMD --------------------------------------------------------------------


async def test_aimd_cuts_to_75_percent_and_freezes_for_an_hour(binance, clock):
    assert binance.capacity == 960
    binance.on_rate_limited(RateLimitKind.weight)
    assert binance.factor == 0.75
    assert binance.capacity == pytest.approx(720)
    clock.advance(1800)
    assert binance.factor == 0.75, "still frozen half an hour in"
    clock.advance(1801)
    assert binance.factor == 0.75, "freeze just lifted; recovery starts one window later"
    clock.advance(60)
    assert binance.factor == pytest.approx(0.80)  # additive increase, one step per window
    clock.advance(60 * 4)
    assert binance.factor == pytest.approx(1.0)


async def test_aimd_compounds_on_repeated_429(binance):
    binance.on_rate_limited(RateLimitKind.weight)
    binance.on_rate_limited(RateLimitKind.weight)
    assert binance.factor == pytest.approx(0.5625)


async def test_weight_429_stops_sending_immediately(binance):
    binance.on_rate_limited(RateLimitKind.weight)
    assert binance.available == 0


async def test_connection_429_shrinks_concurrency_not_the_budget(hl):
    before = hl.capacity
    hl.on_rate_limited(RateLimitKind.connection)
    assert hl.max_inflight == 9
    assert hl.capacity == before
    assert hl.available == before
    assert hl.stats.rate_limited_by_kind == {"connection": 1}


# -- 429 classification ------------------------------------------------------


def test_hl_weight_429_is_a_bare_json_null():
    assert classify_rate_limit(429, "null", venue="hyperliquid") is RateLimitKind.weight


def test_hl_connection_429_is_an_nginx_html_page():
    body = "<html>\r\n<head><title>429 Too Many Requests</title></head>\r\n<body>\r\n"
    assert classify_rate_limit(429, body, venue="hyperliquid") is RateLimitKind.connection
    assert (
        classify_rate_limit(429, b"<html><body><hr><center>nginx</center>", venue="hyperliquid")
        is RateLimitKind.connection
    )


def test_binance_418_is_a_ban_not_a_rate_limit():
    assert classify_rate_limit(418, "", venue="binance") is RateLimitKind.ban


def test_bybit_403_rate_limit_vs_geo_block():
    assert classify_rate_limit(403, "access too frequent", venue="bybit") is RateLimitKind.weight
    assert classify_rate_limit(403, "Access denied", venue="bybit") is RateLimitKind.unknown


# -- header sync -------------------------------------------------------------


async def test_used_weight_header_tops_up_the_ledger(binance):
    await binance.reserve("/fapi/v1/premiumIndex")  # our ledger says 10
    corrected = binance.sync_used(240)  # the venue says 240: someone else shares this IP
    assert corrected == 230
    assert binance.used() == 240


async def test_header_sync_never_refunds(binance):
    await binance.reserve("/fapi/v1/ticker/24hr")
    assert binance.sync_used(5) == 0
    assert binance.used() == 40


async def test_gate_remaining_header_sync(clock):
    gate = Budget("203.0.113.7", venue("gate"), clock=clock, sleep=clock.sleep)
    gate.sync_remaining(remaining=175, limit=200)
    assert gate.used() == 25


# -- registry ----------------------------------------------------------------


def test_budget_is_shared_per_egress_ip_and_venue():
    a = Budget.for_venue("198.51.100.4", "binance")
    b = Budget.for_venue("198.51.100.4", "binance")
    c = Budget.for_venue("198.51.100.5", "binance")
    assert a is b, "one egress IP is one ledger, whatever the process does"
    assert a is not c


def test_budget_requires_an_egress_ip():
    with pytest.raises(ValueError, match="egress IP"):
        Budget("", venue("binance"))


# -- 429 body only disambiguates Hyperliquid ---------------------------------


@pytest.mark.parametrize("name", ["binance", "bybit", "okx", "gate", "bitget"])
def test_html_429_from_a_non_hl_venue_is_a_plain_rate_limit(name):
    # Only HL is known to run two limiters behind one status code. An HTML error page
    # from anyone else is just their proxy talking, and reading it as "too many sockets"
    # would leave the weight budget untouched -- the road to a 418.
    body = "<html><head><title>429 Too Many Requests</title></head><body>nginx</body></html>"
    assert classify_rate_limit(429, body, venue=name) is RateLimitKind.weight


def test_html_429_still_means_connection_for_hyperliquid():
    body = "<html><head><title>429 Too Many Requests</title></head></html>"
    assert classify_rate_limit(429, body, venue="hyperliquid") is RateLimitKind.connection


# -- hard block (Retry-After / ban) ------------------------------------------


async def test_retry_after_sets_a_hard_block_not_a_ledger_entry(binance, clock):
    binance.on_rate_limited(RateLimitKind.weight, retry_after_s=120)
    assert binance.blocked_for == 120
    assert binance.available == 0
    with pytest.raises(BudgetExhausted, match="hard-blocked"):
        await binance.reserve("/fapi/v1/premiumIndex", wait=False)
    clock.advance(121)
    assert binance.blocked_for == 0
    await binance.reserve("/fapi/v1/premiumIndex", wait=False)


async def test_hard_block_is_waited_out_when_wait_is_true(binance, clock):
    binance.on_rate_limited(RateLimitKind.weight, retry_after_s=90)
    t0 = clock.t
    await binance.reserve("/fapi/v1/premiumIndex")
    assert clock.t >= t0 + 90


async def test_a_three_day_ban_outlives_aimd_recovery(binance, clock):
    # Binance 418: Retry-After grows to 3 days. The weight factor recovers on its own
    # schedule; the ban must not be recoverable by it.
    three_days = 3 * 24 * 3600
    binance.on_rate_limited(RateLimitKind.ban, retry_after_s=three_days)
    clock.advance(65 * 60)  # past the 1 h AIMD freeze plus 5 windows of recovery
    assert binance.factor > 0.75, "the weight factor has started recovering"
    assert binance.blocked_for == pytest.approx(three_days - 65 * 60)
    assert binance.available == 0
    with pytest.raises(BudgetExhausted, match="hard-blocked"):
        await binance.reserve("/fapi/v1/premiumIndex", wait=False)
    clock.advance(three_days)
    assert binance.blocked_for == 0
    await binance.reserve("/fapi/v1/premiumIndex", wait=False)


async def test_a_429_without_retry_after_blocks_for_one_window(binance, clock):
    binance.on_rate_limited(RateLimitKind.weight)
    assert binance.blocked_for == pytest.approx(60)
    clock.advance(61)
    await binance.reserve("/fapi/v1/premiumIndex", wait=False)


async def test_the_longest_hard_block_wins(binance):
    binance.on_rate_limited(RateLimitKind.weight, retry_after_s=600)
    binance.on_rate_limited(RateLimitKind.weight, retry_after_s=5)
    assert binance.blocked_for == pytest.approx(600), "a later short block cannot shorten a ban"


# -- charging happens when the request is sent, not when it is queued --------


async def test_queued_calls_are_charged_when_they_are_actually_sent(clock):
    import asyncio

    hl = Budget("203.0.113.7", venue("hyperliquid"), clock=clock, sleep=clock.sleep)
    hl.max_inflight = 1  # one socket: 19 callers queue behind the first

    async def on_the_wire(seconds: float) -> None:
        # A real request yields to the loop before it finishes, which is what lets the
        # other 19 coroutines pile into the queue. Without this yield the tasks run to
        # completion one at a time and the bug cannot show itself.
        await asyncio.sleep(0)
        clock.advance(seconds)

    async def one() -> None:
        async with hl.call("clearinghouseState"):  # weight 2
            await on_the_wire(5.0)

    await asyncio.gather(*(one() for _ in range(20)))

    # 20 serialized 5 s requests span 100 s, so only the last 60 s sit in the window:
    # sends at t0+45 .. t0+95 -> 11 entries x weight 2 = 22.
    # Reserving before taking the slot stamps all 20 at t0 while they queue, so by the
    # time the last one is actually sent the ledger has aged every entry out and reads
    # 0 -- a budget that believes nothing has been sent in the last minute, just as 20
    # requests go out.
    assert hl.used() == pytest.approx(22)


# -- concurrency AIMD --------------------------------------------------------


async def test_max_inflight_recovers_additively_after_the_freeze(hl, clock):
    assert hl.max_inflight == 10
    hl.on_rate_limited(RateLimitKind.connection)
    hl.on_rate_limited(RateLimitKind.connection)
    assert hl.max_inflight == 8
    clock.advance(3599)
    hl._recover(clock.t)
    assert hl.max_inflight == 8, "frozen for the same hour as the weight AIMD"
    clock.advance(2 + 60)
    hl._recover(clock.t)
    assert hl.max_inflight == 9, "+1 per clean window"
    clock.advance(60 * 5)
    hl._recover(clock.t)
    assert hl.max_inflight == 10, "never above the configured ceiling"


async def test_inflight_recovery_never_overrides_a_hand_set_limit(hl, clock):
    hl.max_inflight = 2  # an operator pinned it
    clock.advance(86400)
    hl._recover(clock.t)
    assert hl.max_inflight == 2


# -- AIMD-reduced capacity waits; only the configured budget refuses ---------


async def test_cost_over_the_reduced_capacity_waits_rather_than_raising(binance, clock):
    binance.on_rate_limited(RateLimitKind.weight)  # capacity 960 -> 720
    binance.on_rate_limited(RateLimitKind.weight)  # -> 540
    clock.advance(61)  # clear the hard block
    assert binance.capacity == pytest.approx(540)
    t0 = clock.t
    # 600 fits the configured 960 budget but not today's 540: early, not impossible.
    await binance.reserve("/fapi/v1/klines", weight=600)
    assert clock.t > t0, "it waited for the factor to recover instead of raising"
    assert binance.stats.waits >= 1


async def test_cost_over_the_configured_budget_still_raises_at_once(binance):
    with pytest.raises(BudgetExhausted, match="split the request"):
        await binance.reserve("/fapi/v1/klines", weight=2000)
