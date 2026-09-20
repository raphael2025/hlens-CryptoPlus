"""Doing §6 and §6.1's arithmetic again, out loud.

Every assertion in this file is a literal copied from a table in
``docs/03-ARCHITECTURE.md``, next to the line it comes from. That is the point:
**if the document changes, this file must go red.** A limiter whose constants
drift away from the budget table is a limiter nobody can check, and the numbers
in those tables were corrected once already by the A5 review (§20) — the
original "约 700 个币", the original ``futures_data`` reserve of 50, and the
original HL ceiling of 1200 were all wrong and all looked plausible.

Nothing here reaches the network, and nothing here is derived a second time:
the loader computes, the test compares against the document.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from conftest import reclaimed_consumers_yaml
from hlens_core.ratelimit import BucketKind, BurstShaper, LedgerConfig

BINANCE_WEIGHT = "binance:fapi_weight"
FUTURES_DATA = "binance:futures_data"
FUNDING_RATE = "binance:funding_rate"
HL_WEIGHT = "hyperliquid:info_weight"


# --------------------------------------------------------------------------- #
# §6.1 table 1 — the egress ceilings. "出口合计上限（天花板）"
# --------------------------------------------------------------------------- #
def test_binance_weight_ceiling_is_960(config: LedgerConfig) -> None:
    """§6.1 row 1: 2400/分 official, 40 % → **960/分**."""
    bucket = config.bucket(BINANCE_WEIGHT)
    assert bucket.official_limit_per_min == 2400
    assert bucket.share == Fraction(2, 5)
    assert bucket.egress_ceiling_per_min == 960


def test_futures_data_ceiling_is_80_requests(config: LedgerConfig) -> None:
    """§6.1 row 2: 200 次/分 official (1000/5min), 40 % → **80 次/分**."""
    bucket = config.bucket(FUTURES_DATA)
    assert bucket.official_limit_per_min == 200
    assert bucket.egress_ceiling_per_min == 80


def test_hyperliquid_ceiling_is_1080(config: LedgerConfig) -> None:
    """§6.1 row 3 + §20 review fix 1: 1200 × 90 % = **1080**, not 1200."""
    bucket = config.bucket(HL_WEIGHT)
    assert bucket.official_limit_per_min == 1200
    assert bucket.share == Fraction(9, 10)
    assert bucket.egress_ceiling_per_min == 1080


def test_the_two_bucket_kinds_are_accounted_separately(config: LedgerConfig) -> None:
    """§6: ``/futures/data/*`` 不吃权重、自己一个桶."""
    assert config.bucket(BINANCE_WEIGHT).kind is BucketKind.WEIGHT
    assert config.bucket(HL_WEIGHT).kind is BucketKind.WEIGHT
    assert config.bucket(FUTURES_DATA).kind is BucketKind.REQUEST
    assert config.bucket(FUNDING_RATE).kind is BucketKind.REQUEST


# --------------------------------------------------------------------------- #
# M1-A2b — the fourth bucket: Binance `fundingRate` / `fundingInfo`
# --------------------------------------------------------------------------- #
def test_funding_rate_ceiling_is_40_requests(config: LedgerConfig) -> None:
    """04 §4: 500 次/5min = 100 次/分 official, 40 % 红线 → **40 次/分**."""
    bucket = config.bucket(FUNDING_RATE)
    assert bucket.official_limit_per_min == 100
    assert bucket.share == Fraction(2, 5)
    assert bucket.egress_ceiling_per_min == 40


def test_funding_rate_resident_steady_is_1(config: LedgerConfig) -> None:
    """03 §6 车道表「1h/8h/冷启动」行: fundingRate 每 8h 对账 + fundingInfo 每
    1h + 冷启动一次，04 §4 记为「<1 次/分」；loader 只吃整数，向上取整为 1。
    """
    bucket = config.bucket(FUNDING_RATE)
    assert bucket.resident_steady_per_min == 1


def test_funding_rate_reserve_is_2(config: LedgerConfig) -> None:
    """套用 ``futures_data`` 的公式 reserve = resident + ceil(resident × 11%):
    ceil(1 × 0.11) = 1 → reserve = 1 + 1 = **2**."""
    bucket = config.bucket(FUNDING_RATE)
    assert bucket.reserve_per_min == 1 + 1 == 2


def test_funding_rate_opportunistic_formula_is_38_but_the_cap_is_pressed_to_5(
    config: LedgerConfig,
) -> None:
    """§6.1 公式：40 − max(2, 1) = **38 次/分** — still correct arithmetic.

    M1-A2c: the hard cap is pressed down to **5**, below the formula, because
    04 §11 第 10 项 leaves "``futures_data`` 与 ``fundingRate`` 是否同一个桶"
    `unverified`. Same move as ``hyperliquid:info_weight``'s transitional
    profile: the cap is deliberately lower than the algebra, not equal to it.
    """
    bucket = config.bucket(FUNDING_RATE)
    assert bucket.opportunistic_formula_per_min(1) == 38
    assert bucket.opportunistic_hard_cap_per_min == 5
    assert bucket.opportunistic_available(1) == 5


def test_funding_rate_has_no_fast_lane(config: LedgerConfig) -> None:
    """本桶只出现在 1h/8h/冷启动行，没有 ≤60s 快道，同 ``futures_data``。"""
    assert config.bucket(FUNDING_RATE).fast_lane_floor_per_min == 0


def test_funding_rate_and_futures_data_fit_together_even_if_they_are_secretly_one_bucket(
    config: LedgerConfig,
) -> None:
    """04 §11 第 10 项: `futures_data`(1000 次/5min) 与 `fundingRate`
    (500 次/5min) 是否共用同一个计数器，官方两页各自独立写限额、未说明关系
    → `unverified`（见 venues.yaml 里 `funding_rate` 桶顶部的注释）。本仓库把
    它们建模成两个独立的桶，M1-G 上机录 fixture 时才能实测确认。

    这条测试是"猜错了也不会咬人"的证明：即使实测发现两者其实共用同一个物理
    计数器，把两桶现在的稳态用量合并在一起看，仍然远低于 `futures_data` 自己
    40 % 红线下的 80 次/分天花板 —— 建模成独立桶这件事本身不会把生产账本推
    过线。
    """
    futures_data = config.bucket(FUTURES_DATA)
    funding_rate = config.bucket(FUNDING_RATE)
    combined_steady = futures_data.resident_steady_per_min + funding_rate.resident_steady_per_min
    assert combined_steady == 54 + 1 == 55
    assert combined_steady <= 80
    assert combined_steady <= futures_data.egress_ceiling_per_min


def test_full_opportunistic_on_both_buckets_still_fits_in_80_if_they_share_a_counter(
    config: LedgerConfig,
) -> None:
    """M1-A2c — the whole point of this task.

    The steady-state check above (55 <= 80) does not cover the case that
    motivated this task: BOTH buckets' opportunistic lanes filling up at the
    same time. Before M1-A2c the ``funding_rate`` hard cap was 38 (the
    formula's own answer, per M1-A2b), which gives

        (54 + 20) + (1 + 38) = 113   vs a futures_data ceiling of 80

    a 41 % overshoot if the two buckets turn out to share one physical
    counter (04 §11 第 10 项, `unverified`). On the shared production egress
    IP that overshoot is a 429 -> 418 that bans the whole machine, taking the
    still-running legacy collector down with it. This test is the ledger-side
    guardrail until M1-G's on-machine fixture recording settles the question.
    """
    futures_data = config.bucket(FUTURES_DATA)
    funding_rate = config.bucket(FUNDING_RATE)

    combined_full_opportunistic = (
        futures_data.resident_steady_per_min
        + futures_data.opportunistic_hard_cap_per_min
        + funding_rate.resident_steady_per_min
        + funding_rate.opportunistic_hard_cap_per_min
    )

    # Pin the inputs so this test cannot pass by two unrelated numbers
    # happening to still add up: it must be exercising 54, 20, 1 and 5.
    assert futures_data.resident_steady_per_min == 54
    assert futures_data.opportunistic_hard_cap_per_min == 20
    assert funding_rate.resident_steady_per_min == 1
    assert funding_rate.opportunistic_hard_cap_per_min == 5

    assert combined_full_opportunistic == 80
    assert combined_full_opportunistic <= futures_data.egress_ceiling_per_min
    assert combined_full_opportunistic <= 80


# --------------------------------------------------------------------------- #
# §6.1 — the deduction, and the invariant the review added
# --------------------------------------------------------------------------- #
def test_hyperliquid_reserved_plus_ours_equals_the_ceiling(config: LedgerConfig) -> None:
    """§6.1 row 3: **960 + 120 = 1080 = 天花板 ✓**.

    §20 review fix 1: the placeholder was 1080 and made 1080 + 120 = 1200 =
    100 % of the official limit, leaving the whole egress no backoff headroom.
    """
    bucket = config.bucket(HL_WEIGHT)
    assert bucket.reserved_per_min == 960
    assert bucket.our_ceiling_per_min == 120
    assert bucket.reserved_per_min + bucket.our_ceiling_per_min == 1080
    assert bucket.reserved_per_min + bucket.our_ceiling_per_min == bucket.egress_ceiling_per_min


@pytest.mark.parametrize("key", [BINANCE_WEIGHT, FUTURES_DATA, HL_WEIGHT])
def test_reservations_land_inside_the_egress_total_not_the_official_limit(
    config: LedgerConfig, key: str
) -> None:
    """§6.1 天花板口径: reserved + ours must land inside the EGRESS total."""
    bucket = config.bucket(key)
    assert bucket.reserved_per_min + bucket.our_ceiling_per_min == bucket.egress_ceiling_per_min
    assert bucket.egress_ceiling_per_min < bucket.official_limit_per_min


def test_the_legacy_collector_reserves_nothing_on_binance_and_says_so(
    config: LedgerConfig,
) -> None:
    """§6.1: 「旧采集器不碰 Binance」必须被断言，不能靠记忆.

    The zero is not an omission and not a default: it is an assertion carried
    in ``config/egress-consumers.yaml`` with the claim and its re-checker.
    """
    reservations = {
        reservation.consumer: reservation
        for reservation in config.consumers.for_bucket(
            config.bucket(BINANCE_WEIGHT).key
        )
    }
    legacy = reservations["hub_legacy"]
    assert legacy.reserved_per_min == 0
    assert legacy.assertion is not None and "never calls Binance" in legacy.assertion
    assert legacy.checked_by == "preflight_each_start"
    assert config.bucket(BINANCE_WEIGHT).our_ceiling_per_min == 960
    assert config.bucket(FUTURES_DATA).our_ceiling_per_min == 80


# --------------------------------------------------------------------------- #
# §6 / §6.1 — resident steady load
# --------------------------------------------------------------------------- #
def test_binance_resident_steady_is_241(config: LedgerConfig) -> None:
    """§6 lane table: 20 + 180 + 40 + 1 = **241/分 = 官方 2400 的 10%**."""
    assert 20 + 180 + 40 + 1 == 241
    bucket = config.bucket(BINANCE_WEIGHT)
    assert bucket.resident_steady_per_min == 241
    assert Fraction(241, bucket.official_limit_per_min) == Fraction(241, 2400)


def test_futures_data_resident_steady_is_54(config: LedgerConfig) -> None:
    """§6.1 row 2: 54 次/分 = 3 类 × 180 币 ÷ 10 分钟; §6: 54/80 = 67.5 %."""
    assert 3 * 180 // 10 == 54
    bucket = config.bucket(FUTURES_DATA)
    assert bucket.resident_steady_per_min == 54
    assert Fraction(54, bucket.egress_ceiling_per_min) == Fraction(27, 40)  # 67.5 %


def test_hyperliquid_resident_steady_is_44(config: LedgerConfig) -> None:
    """§6.1 row 3: M1+M2 实际用量 **44/分**, which is 37 % of our 120."""
    bucket = config.bucket(HL_WEIGHT)
    assert bucket.resident_steady_per_min == 44
    assert bucket.our_ceiling_per_min == 120


# --------------------------------------------------------------------------- #
# §6.1 table 2 — reserve, the opportunistic formula, the hard caps
# --------------------------------------------------------------------------- #
def test_binance_reserve_is_300_and_the_fast_lane_floor_is_120(
    config: LedgerConfig,
) -> None:
    """§6.1 row 1: reserve **300/分**; 其中快道地板 **120/分**."""
    bucket = config.bucket(BINANCE_WEIGHT)
    assert bucket.reserve_per_min == 300
    assert bucket.fast_lane_floor_per_min == 120
    assert bucket.opportunistic_hard_cap_per_min == 200


def test_futures_data_reserve_is_60(config: LedgerConfig) -> None:
    """§6.1 row 2: **60 次/分** = 54 + 6 (an 11 % retry margin, self-healing)."""
    bucket = config.bucket(FUTURES_DATA)
    assert bucket.reserve_per_min == 54 + 6 == 60


def test_hyperliquid_transitional_reserve_is_60(config: LedgerConfig) -> None:
    """§6.1 row 3: reserve **60/分** (快道 40 + 元数据与重试 20)."""
    bucket = config.bucket(HL_WEIGHT)
    assert bucket.profile.name == "transitional"
    assert bucket.reserve_per_min == 40 + 20 == 60


def test_binance_opportunistic_is_660_before_the_hard_cap_of_200(
    config: LedgerConfig,
) -> None:
    """§6.1 row 1: 960 − max(300, 241) = **660/分**, 硬顶 **200/分**."""
    bucket = config.bucket(BINANCE_WEIGHT)
    assert bucket.opportunistic_formula_per_min(241) == 660
    assert bucket.opportunistic_available(241) == 200


def test_futures_data_opportunistic_is_20_and_the_cap_equals_the_formula(
    config: LedgerConfig,
) -> None:
    """§6.1 row 2: 80 − max(60, 54) = **20 次/分**; §20: 硬顶 20 现在等于算式结果."""
    bucket = config.bucket(FUTURES_DATA)
    assert bucket.opportunistic_formula_per_min(54) == 20
    assert bucket.opportunistic_hard_cap_per_min == 20
    assert bucket.opportunistic_available(54) == 20


def test_hyperliquid_opportunistic_is_60_with_a_deliberately_lower_cap_of_20(
    config: LedgerConfig,
) -> None:
    """§6.1 row 3: 120 − max(60, 44) = **60/分**, 硬顶 **20 权重/分** 故意压得更低."""
    bucket = config.bucket(HL_WEIGHT)
    assert bucket.opportunistic_formula_per_min(44) == 60
    assert bucket.opportunistic_available(44) == 20


def test_the_formula_never_subtracts_resident_twice(config: LedgerConfig) -> None:
    """§6.1: the replaced formula was ``预算 − 保底 − 常驻实际用量``.

    On ``futures_data`` the old shape gives 80 − 60 − 54 = −34: a backfill
    quota that is permanently negative whenever resident is healthy. The
    corrected one gives 20 whether resident is idle or at its steady load,
    because ``reserve`` is a floor and not a second subtraction.
    """
    bucket = config.bucket(FUTURES_DATA)
    old_shape = bucket.our_ceiling_per_min - bucket.reserve_per_min - 54
    assert old_shape == -34
    assert bucket.opportunistic_formula_per_min(54) == 20
    assert bucket.opportunistic_formula_per_min(0) == 20
    # Above the reserve, the lane does shrink — that is the point of `max`.
    assert bucket.opportunistic_formula_per_min(70) == 10


def test_reserve_is_a_floor_and_is_never_below_the_steady_load(
    config: LedgerConfig,
) -> None:
    """§6.1 保底的定义: reserve 必须 ≥ resident 的稳态用量 (决定 A7).

    This is the invariant whose breach the review found: ``futures_data`` had
    reserve 50 against a steady load of 54.
    """
    for bucket in config.buckets.values():
        assert bucket.reserve_per_min >= bucket.resident_steady_per_min, bucket.key


# --------------------------------------------------------------------------- #
# §6.1 row 4 — the post-retirement profile, reached by editing one number
# --------------------------------------------------------------------------- #
def test_retiring_the_legacy_collector_yields_1080_and_880(
    venues_path: Path, tmp_path: Path
) -> None:
    """§6.1 回收流程 step ③ + row 4: 可用预算变成 **1080/分**, opportunistic **880**.

    The procedure is three steps and changes no code: stop the legacy
    collector, set its reservation to 0, re-run preflight. This test performs
    step ② on a copy of the real file and asserts what step ③ must print.
    """
    path = tmp_path / "egress-consumers.yaml"
    path.write_text(reclaimed_consumers_yaml(), encoding="utf-8")
    reclaimed = LedgerConfig.load(venues_path, path)

    bucket = reclaimed.bucket(HL_WEIGHT)
    assert bucket.reserved_per_min == 0
    assert bucket.our_ceiling_per_min == 1080
    assert bucket.profile.name == "reclaimed"
    assert bucket.reserve_per_min == 200
    assert bucket.opportunistic_formula_per_min(44) == 880
    assert bucket.opportunistic_hard_cap_per_min == 400
    assert bucket.opportunistic_available(44) == 400


# --------------------------------------------------------------------------- #
# §6 — how many coins fit. 266 is the wall, 240 is where backoff still works.
# --------------------------------------------------------------------------- #
def test_the_wall_is_266_coins_and_the_bottleneck_is_futures_data(
    config: LedgerConfig,
) -> None:
    """§6 + §20 review fix 4: 权重桶 (960 − 61) ÷ 1 = **899**;
    ``futures_data`` 80 ÷ 0.3 = **266**; min(899, 266) = **266**.

    The original "约 700 个币" counted the weight bucket only.
    """
    weight = config.coin_headroom(BINANCE_WEIGHT)
    requests = config.coin_headroom(FUTURES_DATA)
    assert weight.wall_coins == 899
    assert requests.wall_coins == 266
    assert min(weight.wall_coins, requests.wall_coins) == 266
    assert config.coin_headroom_overall().key == config.bucket(FUTURES_DATA).key


def test_the_safe_limit_is_240_coins(config: LedgerConfig) -> None:
    """§6 / §20: 80 ÷ 0.3 ÷ 1.11 ≈ **240 个币** —— 266 是撞墙点，240 是还能安全退避的点.

    The 1.11 is this bucket's own retry margin, ``reserve ÷ steady = 60 ÷ 54``.
    """
    requests = config.coin_headroom(FUTURES_DATA)
    assert requests.retry_margin == Fraction(60, 54)
    assert requests.safe_coins == 240
    assert config.coin_headroom_overall().safe_coins == 240


def test_today_180_coins_uses_two_thirds_of_the_tightest_bucket(
    config: LedgerConfig,
) -> None:
    """§6: 当前 180 币用掉该桶 54/80 = 67.5%，是所有桶里最紧的一个."""
    usage = {
        key: Fraction(bucket.resident_steady_per_min, bucket.our_ceiling_per_min)
        for key, bucket in config.buckets.items()
    }
    tightest = max(usage, key=lambda key: usage[key])
    assert str(tightest) == FUTURES_DATA
    assert usage[tightest] == Fraction(27, 40)


# --------------------------------------------------------------------------- #
# §6 — the 10-minute lane's shaped rate
# --------------------------------------------------------------------------- #
def test_the_ten_minute_lane_is_54_requests_per_minute() -> None:
    """§6: 3 类 × 180 币 = 540 次 / 10 min = **54 次/分**."""
    shaper = BurstShaper(total=3 * 180, window_ms=10 * 60_000)
    assert shaper.total == 540
    assert shaper.per_min == 54
