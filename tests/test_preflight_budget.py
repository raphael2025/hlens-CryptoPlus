"""The deduction table: the ledger's numbers, and the ⚠ the numbers do not say.

Every test here goes through the **real** ``LedgerConfig.load`` and the
repository's own ``config/*.yaml``. A fixture copy of those files would go
stale exactly when it mattered — the day someone changes a reservation — and
the point of this table is that it describes the files the collector will read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import CONSUMERS_PATH, VENUES_PATH, set_hl_reservation
from hlens_core.preflight import Status, Tag, check_budget, read_observations
from hlens_core.preflight.budget import ConsumerObservation
from hlens_core.ratelimit import ConfigError, LedgerConfig

HL = "hyperliquid:info_weight"
FUTURES_DATA = "binance:futures_data"
FAPI = "binance:fapi_weight"


@pytest.fixture
def budget() -> LedgerConfig:
    return LedgerConfig.load(VENUES_PATH, CONSUMERS_PATH)


def _block(check_lines: tuple[str, ...], key: str) -> list[str]:
    """The lines of one bucket's block, from its header to the blank line."""
    start = next(index for index, line in enumerate(check_lines) if line.startswith(key))
    out: list[str] = []
    for line in check_lines[start:]:
        if not line.strip() and out:
            break
        out.append(line)
    return out


# --------------------------------------------------------------------------- #
# The annotations, and the promise that they cost the ledger nothing.
# --------------------------------------------------------------------------- #
def test_the_ledger_still_loads_the_annotated_file() -> None:
    """The three preflight-only keys are invisible to ``ratelimit``.

    This is the test that keeps the arrangement honest: preflight put
    ``observed_max_per_min`` / ``self_cap_per_min`` / ``qualifiers`` into
    ``config/egress-consumers.yaml`` without touching the loader, so if the
    loader ever starts rejecting unknown keys this fails here rather than on
    the hub.
    """
    config = LedgerConfig.load(VENUES_PATH, CONSUMERS_PATH)
    assert config.bucket(HL).reserved_per_min == 953
    assert config.bucket(HL).our_ceiling_per_min == 127


def test_observations_are_read_for_the_consumers_that_have_them() -> None:
    observations = read_observations(CONSUMERS_PATH)
    hub = observations[("hub_legacy", HL)]
    assert hub.observed_max_per_min == 1147
    assert hub.self_cap_per_min == 1200
    assert hub.worst_known_per_min == 1200
    assert {qualifier.value for qualifier in hub.qualifiers} == {
        "sample_limited",
        "lower_bound",
    }
    dev = observations[("dev_machine", FAPI)]
    assert dev.observed_max_per_min == 122
    # No self-limit is declared anywhere by that collector, so the key is
    # absent rather than guessed at.
    assert dev.self_cap_per_min is None


def test_a_consumer_with_no_observation_is_unknown_not_fine(budget: LedgerConfig) -> None:
    """§6.1: 错的方式是「我们以为有 X 权重其实没有」."""
    stripped = {
        key: ConsumerObservation(
            consumer=value.consumer,
            bucket=value.bucket,
            observed_max_per_min=None,
            self_cap_per_min=None,
            qualifiers=frozenset(),
        )
        for key, value in read_observations(CONSUMERS_PATH).items()
    }
    check = check_budget(budget, stripped)
    assert check.status is Status.UNKNOWN
    assert "没有观测峰值" in "\n".join(check.lines)


# --------------------------------------------------------------------------- #
# The point of the whole table.
# --------------------------------------------------------------------------- #
def test_hyperliquid_can_never_print_a_tick(budget: LedgerConfig) -> None:
    """``03`` §6.1's ⚠, as a property of the renderer rather than a comment.

    The ledger's identity holds — the loader guarantees it — and the egress
    does not. Both have to be visible, and the verdict has to follow the
    second one.
    """
    check = check_budget(budget, read_observations(CONSUMERS_PATH))
    block = "\n".join(_block(check.lines, HL))

    # The ledger's side, printed in full: 1200 x 90% = 1080, −953, = 127.
    assert "官方 1200/min x 90% = 出口合计上限 1080/min" in block
    assert "Σ 预留 953/min" in block
    assert "我们可用 127/min" in block

    # The egress's side: both crossings, each with its own sentence.
    assert "观测最大 1147" in block
    assert "自配上限 1200" in block
    assert "账面平，现实没平" in block
    assert "恒等式" in block

    # And the verdict is not green, however neatly 953 + 127 adds up.
    assert check.status is Status.YELLOW


def test_our_hyperliquid_share_is_never_printed_harder_than_its_input(
    budget: LedgerConfig,
) -> None:
    """AGENTS §2, on the one figure it matters most for."""
    check = check_budget(budget, read_observations(CONSUMERS_PATH))
    block = "\n".join(_block(check.lines, HL))
    ours_line = next(line for line in block.splitlines() if "我们可用 127/min" in line)
    assert "派生(实测)" in ours_line
    assert "样本不足" in ours_line
    assert "下界" in ours_line
    assert check.provenance is not None
    assert check.provenance.effective is Tag.MEASURED


def test_the_binance_weight_bucket_is_green_with_room_to_spare(
    budget: LedgerConfig,
) -> None:
    check = check_budget(budget, read_observations(CONSUMERS_PATH))
    block = "\n".join(_block(check.lines, FAPI))
    assert "官方 2400/min x 40% = 出口合计上限 960/min" in block
    assert "我们可用 918/min" in block
    # 122 (their worst minute) + 300 (our reserve) + 200 (our opportunistic
    # cap) = 622, comfortably inside 960.
    assert "合计最坏 122 + 500 = 622/min" in block
    assert "账面与实测都在天花板内" in block


def test_futures_data_is_the_bucket_that_actually_tightens(budget: LedgerConfig) -> None:
    """The tightest bucket we have (§6: 想加币先看这一路), and now exactly full.

    ``config/egress-consumers.yaml``'s own note works this out as ``54 + 20 +
    4 = 78 of 80`` using resident's *steady* load and the pre-M1-A3b hard cap
    of 20. Neither number is what the ledger will actually admit: the reserve
    is 60, not 54 (the retry margin is part of what the ledger admits), and
    the hard cap is 16, not 20 — M1-A3b's fix, because 20 sat above what
    ``our_ceiling_per_min`` (76, after M1-B's ``dev_machine`` reservation of
    4) could ever pay out. With the corrected 16, our practical max is
    ``60 + 16 = 76`` — which is exactly ``our_ceiling_per_min``, not a looser
    bound above it — and the honest worst case is ``76 + 4 = 80``: precisely
    the ceiling, with zero room, not four over it. Nobody was wrong before;
    the stale hard cap made this block print a worse number than the true one,
    which happened to still be safe. It is now the true one.
    """
    check = check_budget(budget, read_observations(CONSUMERS_PATH))
    block = "\n".join(_block(check.lines, FUTURES_DATA))
    assert "机会硬顶 16 = 76/min" in block
    assert "合计最坏 4 + 76 = 80/min" in block
    assert "vs 天花板 80/min" in block
    assert "账面与实测都在天花板内（最坏 80 <= 80）" in block


# --------------------------------------------------------------------------- #
# The refusal. Demo 3 of the task, as a test.
# --------------------------------------------------------------------------- #
def test_a_reservation_over_the_ceiling_refuses_to_load(tmp_path: Path) -> None:
    """§6.1: 预留额 > 天花板 时加载器拒载 —— 「那个拒绝是对的」.

    1147 is not a hypothetical: it is the maximum M1-B actually observed from
    ``hub_legacy`` in its sampling window. Writing the measured *peak* into the
    reservation instead of its p95 is what the refusal is for — it says there
    is no safe split of this bucket, which is exactly true.
    """
    consumers = tmp_path / "egress-consumers.yaml"
    consumers.write_text(
        set_hl_reservation(
            None,
            "reserved_per_min: 1147\n"
            "source: measured\n"
            "note: the observed single-minute maximum, not the p95",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as error:
        LedgerConfig.load(VENUES_PATH, consumers)
    message = str(error.value)
    assert "1147" in message
    assert "1080" in message
