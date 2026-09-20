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

from conftest import reclaimed_consumers_yaml, set_hl_reservation
from hlens_core.ratelimit import BucketKind, BurstShaper, ConfigError, LedgerConfig

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

    M1-B (2026-09-20) made it bind for real, and found a hole in it while
    doing so: the sum below used to leave out the OTHER consumers on this
    egress entirely, so it would have gone on passing while the egress went
    over. It now counts them. The development machine's Binance collector was
    measured at 4 requests/min of this bucket, and adding a fifth term to a
    sum that already came to exactly 80 would have made it 84.

    It does not, and the reason is worth pinning: ``opportunistic_available``
    is ``min(formula, hard cap)``, and the formula shrinks when someone
    else's reservation shrinks our ceiling. Our opportunistic lane gave up
    exactly the 4 the other consumer took (20 -> 16), which is what §6.1's
    formula is for. The sum is still 80 — with no slack left at all, and with
    the hard cap of 20 no longer the thing that binds.
    """
    futures_data = config.bucket(FUTURES_DATA)
    funding_rate = config.bucket(FUNDING_RATE)
    others = config.consumers.reserved_per_min(futures_data.key)

    combined_full_opportunistic = (
        futures_data.resident_steady_per_min
        + futures_data.opportunistic_available(futures_data.resident_steady_per_min)
        + funding_rate.resident_steady_per_min
        + funding_rate.opportunistic_available(funding_rate.resident_steady_per_min)
        + others
    )

    # Pin the inputs so this test cannot pass by unrelated numbers happening
    # to still add up: it must be exercising 54, 16, 1, 5 and the measured 4.
    assert futures_data.resident_steady_per_min == 54
    assert futures_data.opportunistic_hard_cap_per_min == 20  # no longer binding
    assert futures_data.opportunistic_available(54) == 16  # the formula binds
    assert funding_rate.resident_steady_per_min == 1
    assert funding_rate.opportunistic_available(1) == 5
    assert others == 4  # M1-B measured; this term used to be missing entirely

    assert combined_full_opportunistic == 80
    assert combined_full_opportunistic <= futures_data.egress_ceiling_per_min
    assert combined_full_opportunistic <= 80


# --------------------------------------------------------------------------- #
# §6.1 — the deduction, and the invariant the review added
# --------------------------------------------------------------------------- #
def test_hyperliquid_reserved_plus_ours_equals_the_ceiling(config: LedgerConfig) -> None:
    """§6.1 row 3 guessed **960 + 120**. M1-B measured **950 + 130**.

    §20 review fix 1: the placeholder was 1080 and made 1080 + 120 = 1200 =
    100 % of the official limit, leaving the whole egress no backoff headroom.
    The invariant is the sum, and it still holds.

    The 960 was flagged in §6.1 as a guess resting on a circular reference.
    Measured on 2026-09-20 from the legacy collector's own per-egress-IP
    weight counter, the guess was good — and good on the OPTIMISTIC side:
    see ``test_the_legacy_collectors_own_cap_exceeds_our_whole_ceiling`` just
    below for the number that actually matters.
    """
    bucket = config.bucket(HL_WEIGHT)
    assert bucket.reserved_per_min == 953
    assert bucket.our_ceiling_per_min == 127
    assert bucket.reserved_per_min + bucket.our_ceiling_per_min == 1080
    assert bucket.reserved_per_min + bucket.our_ceiling_per_min == bucket.egress_ceiling_per_min


def test_the_legacy_collectors_own_cap_exceeds_our_whole_ceiling(
    config: LedgerConfig, venues_path: Path, tmp_path: Path
) -> None:
    """M1-B's most load-bearing finding, pinned so it cannot be forgotten.

    The 950 above is what that collector SPENDS. What it is ALLOWED to spend,
    by its own configuration, is 1200 weight/min per egress IP — the full
    official Hyperliquid limit — and it has no idea this project exists.

    1200 > 1080. One consumer's self-granted cap is larger than our entire
    egress ceiling, so there is no split of this bucket that is safe by
    arithmetic alone; what keeps us inside the line today is only that it
    happens not to be running flat out. The loader refuses that configuration
    rather than resolving it to a negative budget, which is the correct
    behaviour and also the reason this cannot be fixed on our side: capping
    the other collector is raphael's call (report §A, §G).
    """
    assert config.bucket(HL_WEIGHT).egress_ceiling_per_min == 1080 < 1200

    path = tmp_path / "egress-consumers.yaml"
    path.write_text(
        set_hl_reservation(None, "reserved_per_min: 1200\nsource: measured"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="over the line"):
        LedgerConfig.load(venues_path, path)


@pytest.mark.parametrize("key", [BINANCE_WEIGHT, FUTURES_DATA, HL_WEIGHT])
def test_reservations_land_inside_the_egress_total_not_the_official_limit(
    config: LedgerConfig, key: str
) -> None:
    """§6.1 天花板口径: reserved + ours must land inside the EGRESS total."""
    bucket = config.bucket(key)
    assert bucket.reserved_per_min + bucket.our_ceiling_per_min == bucket.egress_ceiling_per_min
    assert bucket.egress_ceiling_per_min < bucket.official_limit_per_min


def test_the_binance_traffic_on_this_egress_is_the_dev_machines_not_the_legacy_collectors(
    config: LedgerConfig,
) -> None:
    """F-12, closed by M1-B on 2026-09-20. Both frozen documents were half right.

    §6.1 asserted "旧采集器只采 Hyperliquid，**不碰 Binance**" and reserved 0
    on that basis. `04 §4` called that `未验证` and warned that `01 §6`'s hub
    `funding`/`kline` tables are marked 「多所」, which "提示 hub 很可能也打
    Binance". Measured:

    * §6.1 is right **about the Hyperliquid collector**. It issues no Binance
      request at all — the Binance klines `01 §6` lists were downloaded from
      the static data-dump host by a hand-run tool, which charges no bucket.
      That is what reconciles the two documents.
    * §6.1 is wrong **about the egress**, because it only counted one other
      consumer. The development machine leaves through the same public IP and
      runs a resident Binance futures collector around the clock. §6.1's
      "dev 的 live 预算为 0" was read as covering that machine; it covers our
      own live tests, which are deselected by default, and nothing else.

    So the Binance zero was false — just not on the row anybody was watching.
    """
    weight_by_consumer = {
        reservation.consumer: reservation
        for reservation in config.consumers.for_bucket(config.bucket(BINANCE_WEIGHT).key)
    }
    request_by_consumer = {
        reservation.consumer: reservation
        for reservation in config.consumers.for_bucket(config.bucket(FUTURES_DATA).key)
    }

    # The Hyperliquid collector: still zero, now measured rather than recalled.
    legacy = weight_by_consumer["hub_legacy"]
    assert legacy.reserved_per_min == 0
    assert legacy.source.value == "measured"
    assert legacy.assertion is not None and "no Binance API request" in legacy.assertion
    assert legacy.checked_by == "preflight_each_start"
    assert request_by_consumer["hub_legacy"].reserved_per_min == 0

    # The development machine: not zero, and the reason F-12 existed.
    dev = weight_by_consumer["dev_machine"]
    assert dev.reserved_per_min == 42
    assert dev.source.value == "measured"
    assert request_by_consumer["dev_machine"].reserved_per_min == 4

    # What the deduction costs us, as preflight must print it.
    assert config.bucket(BINANCE_WEIGHT).our_ceiling_per_min == 960 - 42 == 918
    assert config.bucket(FUTURES_DATA).our_ceiling_per_min == 80 - 4 == 76
    assert config.bucket(FUNDING_RATE).our_ceiling_per_min == 40


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
    """§6.1 row 3: M1+M2 实际用量 **44/分**.

    §6.1 called that 37 % of the 120 its placeholder split left us. Measured,
    our share is 130, so the same 44 is 34 % of it — the transitional picture
    §6.1 drew survives the measurement almost unchanged.
    """
    bucket = config.bucket(HL_WEIGHT)
    assert bucket.resident_steady_per_min == 44
    assert bucket.our_ceiling_per_min == 127


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


def test_binance_opportunistic_is_618_before_the_hard_cap_of_200(
    config: LedgerConfig,
) -> None:
    """§6.1 row 1 gave 960 − max(300, 241) = 660/分. M1-B's measured 42 for the
    development machine makes it 918 − max(300, 241) = **618/分**.

    The hard cap of 200 binds either way, so the K-line backfill lane does not
    actually change: the deduction comes out of headroom nobody was spending.
    """
    bucket = config.bucket(BINANCE_WEIGHT)
    assert bucket.opportunistic_formula_per_min(241) == 618
    assert bucket.opportunistic_hard_cap_per_min == 200
    assert bucket.opportunistic_available(241) == 200


def test_futures_data_opportunistic_drops_to_16_and_the_cap_stops_binding(
    config: LedgerConfig,
) -> None:
    """§6.1 row 2 gave 80 − max(60, 54) = 20 次/分, equal to the hard cap.

    M1-B's measured 4 for the development machine takes our ceiling to 76, so
    the formula gives 76 − max(60, 54) = **16**, and ``min(16, 20)`` means the
    hard cap of 20 has stopped binding. This is the bucket §6 already named as
    the tightest one we have ("想加币先看这一路"), and it got tighter.
    """
    bucket = config.bucket(FUTURES_DATA)
    assert bucket.opportunistic_formula_per_min(54) == 16
    assert bucket.opportunistic_hard_cap_per_min == 20
    assert bucket.opportunistic_available(54) == 16


def test_hyperliquid_opportunistic_is_67_with_a_deliberately_lower_cap_of_20(
    config: LedgerConfig,
) -> None:
    """§6.1 row 3 gave 120 − max(60, 44) = 60/分. Measured: 127 − 60 = **67/分**.

    The hard cap of **20 权重/分** is unchanged and still binds, and §6.1's
    reason for pressing it below the algebra now has measurements behind it:
    "HL 那 120 是从共享出口里切出来的，把它吃满等于把整个出口顶到 1080 的天花板".
    The other consumer on this egress was measured spending ~953/min at p95
    that same 1080, so the headroom the cap protects is real and thin.
    """
    bucket = config.bucket(HL_WEIGHT)
    assert bucket.profile.name == "transitional"
    assert bucket.opportunistic_formula_per_min(44) == 67
    assert bucket.opportunistic_hard_cap_per_min == 20
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
    assert old_shape == 76 - 60 - 54 == -38
    assert bucket.opportunistic_formula_per_min(54) == 16
    assert bucket.opportunistic_formula_per_min(0) == 16
    # Above the reserve, the lane does shrink — that is the point of `max`.
    assert bucket.opportunistic_formula_per_min(70) == 6


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
    """§6 + §20 review fix 4 computed 权重桶 (960 − 61) ÷ 1 = 899 and
    ``futures_data`` 80 ÷ 0.3 = 266, min = 266.

    M1-B's measured deduction moves both: (918 − 61) ÷ 1 = **857** and
    76 ÷ 0.3 = **253**. ``min(857, 253) = 253`` — the bottleneck is still
    ``futures_data``, so that conclusion survives; the number does not.

    The original "约 700 个币" counted the weight bucket only.
    """
    weight = config.coin_headroom(BINANCE_WEIGHT)
    requests = config.coin_headroom(FUTURES_DATA)
    assert weight.wall_coins == 857
    assert requests.wall_coins == 253
    assert min(weight.wall_coins, requests.wall_coins) == 253
    assert config.coin_headroom_overall().key == config.bucket(FUTURES_DATA).key


def test_the_safe_limit_is_228_coins(config: LedgerConfig) -> None:
    """§6 / §20 gave 80 ÷ 0.3 ÷ 1.11 ≈ 240 个币 —— 266 是撞墙点，240 是还能安全
    退避的点. After M1-B's deduction it is 76 ÷ 0.3 ÷ 1.11 ≈ **228**, wall 253.

    The 1.11 is this bucket's own retry margin, ``reserve ÷ steady = 60 ÷ 54``,
    and it does not move: it is a property of the lane, not of the ceiling.
    """
    requests = config.coin_headroom(FUTURES_DATA)
    assert requests.retry_margin == Fraction(60, 54) == Fraction(10, 9)
    assert requests.safe_coins == 228
    assert config.coin_headroom_overall().safe_coins == 228


def test_today_180_coins_uses_seven_tenths_of_the_tightest_bucket(
    config: LedgerConfig,
) -> None:
    """§6 said 当前 180 币用掉该桶 54/80 = 67.5%，是所有桶里最紧的一个.

    The ratio is against OUR ceiling, which the measured deduction moved to
    76, so the same 180 coins now use 54/76 ≈ **71.1 %**. Still the tightest
    bucket, and now tighter.
    """
    usage = {
        key: Fraction(bucket.resident_steady_per_min, bucket.our_ceiling_per_min)
        for key, bucket in config.buckets.items()
    }
    tightest = max(usage, key=lambda key: usage[key])
    assert str(tightest) == FUTURES_DATA
    assert usage[tightest] == Fraction(54, 76) == Fraction(27, 38)


# --------------------------------------------------------------------------- #
# §6 — the 10-minute lane's shaped rate
# --------------------------------------------------------------------------- #
def test_the_ten_minute_lane_is_54_requests_per_minute() -> None:
    """§6: 3 类 × 180 币 = 540 次 / 10 min = **54 次/分**."""
    shaper = BurstShaper(total=3 * 180, window_ms=10 * 60_000)
    assert shaper.total == 540
    assert shaper.per_min == 54
