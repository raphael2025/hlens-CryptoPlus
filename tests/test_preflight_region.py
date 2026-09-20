"""能力矩阵 / 覆盖矩阵 (决定 B8), on the two adapters' real declarations.

The matrix is built from what the adapters declare and what the probe found.
Both halves are injected, so every region can be tested from here — including
the US egress of ``04`` §6, which cannot be produced from this machine at all.
"""

from __future__ import annotations

import pytest

from conftest import VENUES_PATH
from hlens_core.adapters.binance.capabilities import BINANCE_CAPABILITIES
from hlens_core.adapters.hyperliquid.capabilities import HYPERLIQUID_CAPABILITIES
from hlens_core.preflight import (
    M1_REQUIRED_CAPABILITIES,
    ProbeOutcome,
    Status,
    Tag,
    check_capability_matrix,
    check_region,
    probe_plan,
)
from hlens_core.preflight.reachability import EndpointFamily

CAPABILITY_SETS = (BINANCE_CAPABILITIES, HYPERLIQUID_CAPABILITIES)


@pytest.fixture
def families() -> tuple[EndpointFamily, ...]:
    return probe_plan(VENUES_PATH)


def _outcome(family: EndpointFamily, status_code: int) -> ProbeOutcome:
    return ProbeOutcome(
        family=family,
        status_code=status_code,
        latency_ms=42,
        error_class=None if 200 <= status_code < 300 else f"http_{status_code}",
    )


def _all_ok(families: tuple[EndpointFamily, ...]) -> dict[str, ProbeOutcome]:
    return {family.name: _outcome(family, 200) for family in families}


# --------------------------------------------------------------------------- #
# Not probing is an answer, and the answer is 未检出.
# --------------------------------------------------------------------------- #
def test_without_a_probe_the_region_is_unknown(families: tuple[EndpointFamily, ...]) -> None:
    check = check_region(None, families, None)
    assert check.status is Status.UNKNOWN
    body = "\n".join(check.lines)
    # The plan and its cost are printed even when nothing is sent: that is what
    # lets a person decide whether to pass --live-probe (04 §12).
    assert "binance:fapi" in body
    assert "记 20" in body


def test_without_a_probe_every_supported_capability_is_unknown() -> None:
    check = check_capability_matrix(CAPABILITY_SETS, None)
    assert check.status is Status.UNKNOWN
    body = "\n".join(check.lines)
    assert "派生(未验证)·未实测" in body
    # 03 §9: 任一「M1 必需能力」判定不可用则拒绝启动采集 — and "not measured"
    # is not "available".
    assert "不是「可用」" in check.headline
    for capability in sorted(M1_REQUIRED_CAPABILITIES):
        assert f"binance:{capability}" in body


# --------------------------------------------------------------------------- #
# A healthy non-US egress.
# --------------------------------------------------------------------------- #
def test_a_reachable_region_makes_the_m1_capabilities_available(
    families: tuple[EndpointFamily, ...],
) -> None:
    check = check_capability_matrix(CAPABILITY_SETS, _all_ok(families), country="SG")
    body = "\n".join(check.lines)
    assert "M1 必需能力全部可用" in check.headline
    assert "F1   币种清单" in body
    assert "F2   分钟级采集" in body
    # The REST rows are now derived from a measurement...
    assert "派生(实测)" in body
    # ...while the two stream rows stay unknown: this step never opens a
    # WebSocket (03 §9 ⑫ — M1-G does that on the production host).
    assert check.status is Status.UNKNOWN
    assert "本步不连" in body


def test_an_unsupported_capability_reads_as_the_venue_not_publishing_it(
    families: tuple[EndpointFamily, ...],
) -> None:
    """Seam ②: 「该所不发布」 is a complete answer, not a gap — and it must not
    print as 可用 next to the word ``unsupported``."""
    check = check_capability_matrix(CAPABILITY_SETS, _all_ok(families), country="SG")
    rows = [line for line in check.lines if line.startswith("hyperliquid")]
    ls_ratio = next(line for line in rows if "long_short_ratio" in line)
    assert "unsupported" in ls_ratio
    assert "该所不发布" in ls_ratio
    assert "可用" not in ls_ratio.replace("不可用", "")

    liquidation = next(line for line in rows if "liquidation_stream" in line)
    assert "unsupported" in liquidation
    binance_liquidation = next(
        line
        for line in check.lines
        if line.startswith("binance") and "liquidation_stream" in line
    )
    # 01 §4.7: a venue-throttled feed is always a lower bound, and the region
    # check copies that answer rather than recomputing it.
    assert "lower_bound" in binance_liquidation


# --------------------------------------------------------------------------- #
# The US egress of 04 §6 — the case this table exists for.
# --------------------------------------------------------------------------- #
def test_a_geo_refused_primary_host_takes_the_binance_rows_down(
    families: tuple[EndpointFamily, ...],
) -> None:
    outcomes = _all_ok(families)
    outcomes["binance:fapi"] = _outcome(
        next(family for family in families if family.name == "binance:fapi"), 451
    )
    check = check_capability_matrix(CAPABILITY_SETS, outcomes, country="US")
    body = "\n".join(check.lines)
    assert check.status is Status.RED
    assert "不可用" in body
    assert "451" in body
    assert "改走镜像路径" in body
    # Hyperliquid is unaffected: the refusal is one venue's, in one region.
    hyperliquid_mark = next(
        line for line in check.lines if line.startswith("hyperliquid") and "mark_price " in line
    )
    assert "可用" in hyperliquid_mark


def test_the_rebate_entry_follows_the_region_not_a_capability(
    families: tuple[EndpointFamily, ...],
) -> None:
    """``01`` §4.9 / ``03`` §9's own example of a display rule."""
    hidden = check_capability_matrix(CAPABILITY_SETS, _all_ok(families), country="US")
    assert "F9   返佣入口" in "\n".join(hidden.lines)
    assert "隐藏（US" in "\n".join(hidden.lines)

    shown = check_capability_matrix(CAPABILITY_SETS, _all_ok(families), country="SG")
    line = next(item for item in shown.lines if item.strip().startswith("F9"))
    assert "显示（SG）" in line
    # We do not have the sanctioned-region list anywhere in the documents, and
    # a list invented here would be worse than an admission that it is missing.
    assert "受制裁地区清单本仓库未定义" in line
    assert "未验证" in line


def test_the_region_check_reports_the_geo_refusal_as_such(
    families: tuple[EndpointFamily, ...],
) -> None:
    outcomes = _all_ok(families)
    outcomes["binance:fapi"] = _outcome(
        next(family for family in families if family.name == "binance:fapi"), 451
    )
    check = check_region("US", families, outcomes)
    body = "\n".join(check.lines)
    assert "出口国家：US" in body
    assert "按地理位置拒绝" in body
    assert "必须改走镜像路径" in body
    assert check.status is Status.YELLOW
    assert check.provenance is not None
    assert check.provenance.effective is Tag.MEASURED
