"""The command itself: what it exits with, and what it refuses to do.

Two properties are asserted here that no other test can assert:

* **The default run sends nothing.** Every test in this file runs inside
  ``respx.mock`` with no routes registered, so any outbound request raises
  instead of leaving the machine. That is the structural version of the rule
  this whole task worked under — this machine's egress is production's egress,
  and it already carries a collector that is not ours.
* **The exit code is the verdict.** ``03`` §9 步骤 ④: 全过才继续. A red check
  exits 2 whatever else passed, and an unmeasured check never exits 0.
"""

from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path

import pytest
import respx

from conftest import VENUES_PATH, set_hl_reservation
from hlens_core.adapters.binance.capabilities import BINANCE_CAPABILITIES
from hlens_core.adapters.hyperliquid.capabilities import HYPERLIQUID_CAPABILITIES
from hlens_core.preflight import ExitCode, HostFacts, egress_hash, main
from hlens_core.ratelimit import LedgerConfig

CAPABILITY_SETS = (BINANCE_CAPABILITIES, HYPERLIQUID_CAPABILITIES)
NOW = "1789000000000"
ADDRESS = "198.51.100.23"  # RFC 5737 TEST-NET-2
SALT = "unit-test-salt"
GB = 1000**3


def _facts(tmp_path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "egress_ip": ADDRESS,
        "default_route_interface": "eth0",
        "tailscale_exit_node": None,
        "tailscale_present": True,
        "chrony_synchronized": True,
        "clock_offset_s": "0.01",
        "disk_free_bytes": 900 * GB,
        "disk_total_bytes": 1000 * GB,
        "stage_free_bytes": 900 * GB,
        "cpu_threads": 8,
        "memory_total_bytes": 32 * GB,
        "postgres_server_version_num": 180006,
    }
    payload.update(overrides)
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _egress_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGRESS_SALT", SALT)
    monkeypatch.setenv("EXPECTED_EGRESS_HASH", egress_hash(SALT, ADDRESS))


def _run(argv: list[str]) -> tuple[int, str]:
    stdout = io.StringIO()
    code = main(
        argv,
        budget_loader=LedgerConfig.load,
        capability_sets=CAPABILITY_SETS,
        stdout=stdout,
    )
    return code, stdout.getvalue()


@respx.mock
def test_the_default_run_sends_no_request(tmp_path: Path) -> None:
    code, output = _run(
        [
            "--config-dir",
            str(VENUES_PATH.parent),
            "--facts-json",
            str(_facts(tmp_path)),
            "--now-ms",
            NOW,
        ]
    )
    assert respx.calls.call_count == 0
    assert "未运行活体探测" in output
    # Nothing failed, but the region was never measured, so this is not a pass.
    assert code == ExitCode.NOT_CLEARED


@respx.mock
def test_a_red_clock_decides_the_exit_code(tmp_path: Path) -> None:
    """``03`` §8's own example: 1.35 s on a 1 s threshold, and no way around it."""
    code, output = _run(
        [
            "--config-dir",
            str(VENUES_PATH.parent),
            "--facts-json",
            str(_facts(tmp_path, clock_offset_s="1.35")),
            "--now-ms",
            NOW,
        ]
    )
    assert code == ExitCode.REFUSED
    assert "[FAIL] 时钟" in output
    assert "红就是红，没有跳过开关" in output
    assert respx.calls.call_count == 0


@respx.mock
def test_no_flag_or_variable_turns_that_red_green(tmp_path: Path) -> None:
    """There is no development mode. Asserted against the parser itself so
    that adding one is a visible change to this test, not a quiet feature."""
    from hlens_core.preflight import build_parser

    options = {
        action.option_strings[0]
        for action in build_parser()._actions
        if action.option_strings
    }
    for forbidden in ("--skip-clock", "--dev", "--clock-threshold", "--force", "--allow-red"):
        assert forbidden not in options


@respx.mock
def test_a_missing_egress_secret_is_unknown_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EGRESS_SALT")
    code, output = _run(
        [
            "--config-dir",
            str(VENUES_PATH.parent),
            "--facts-json",
            str(_facts(tmp_path)),
            "--now-ms",
            NOW,
        ]
    )
    assert code == ExitCode.NOT_CLEARED
    assert "EGRESS_SALT 未设置" in output


@respx.mock
def test_an_ip_change_prints_the_ingest_gap_row_without_a_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """坑 2: ``source_health`` / ``ops_event`` are M1-C's, so the rows go to
    stdout and the run still finishes."""
    monkeypatch.setenv("EXPECTED_EGRESS_HASH", egress_hash(SALT, "203.0.113.9"))
    code, output = _run(
        [
            "--config-dir",
            str(VENUES_PATH.parent),
            "--facts-json",
            str(_facts(tmp_path)),
            "--now-ms",
            NOW,
        ]
    )
    assert code == ExitCode.REFUSED
    assert "ingest_gap     venue=x metric=egress cause=egress_change" in output
    assert "ops_event      kind=preflight ok=False" in output
    assert ADDRESS not in output


@respx.mock
def test_no_database_leaves_the_postgres_assertion_unmeasured(tmp_path: Path) -> None:
    code, output = _run(
        [
            "--config-dir",
            str(VENUES_PATH.parent),
            "--facts-json",
            str(_facts(tmp_path, postgres_server_version_num=None)),
            "--now-ms",
            NOW,
        ]
    )
    assert code == ExitCode.NOT_CLEARED
    assert "[ ?? ] PostgreSQL >= 18.6" in output


@respx.mock
def test_an_old_postgres_refuses_the_start(tmp_path: Path) -> None:
    code, output = _run(
        [
            "--config-dir",
            str(VENUES_PATH.parent),
            "--facts-json",
            str(_facts(tmp_path, postgres_server_version_num=180005)),
            "--now-ms",
            NOW,
        ]
    )
    assert code == ExitCode.REFUSED
    assert "CVE-2026-19385" in output


@respx.mock
def test_a_budget_file_that_cannot_be_loaded_refuses_the_start(tmp_path: Path) -> None:
    """§6.1: the loader's refusal is preflight's refusal, in the loader's words."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "venues.yaml").write_text(
        VENUES_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (config_dir / "egress-consumers.yaml").write_text(
        set_hl_reservation(
            None,
            "reserved_per_min: 1147\nsource: measured\nnote: the observed maximum",
        ),
        encoding="utf-8",
    )
    code, output = _run(
        [
            "--config-dir",
            str(config_dir),
            "--facts-json",
            str(_facts(tmp_path)),
            "--now-ms",
            NOW,
        ]
    )
    assert code == ExitCode.REFUSED
    assert "拒绝放行：预算配置加载失败" in output
    assert "1147" in output and "1080" in output
    assert "没有安全的分法" in output


@respx.mock
def test_status_json_carries_the_matrix_and_the_provenance(tmp_path: Path) -> None:
    """``03`` §11: the status page shows preflight's 区域与能力矩阵 permanently."""
    target = tmp_path / "status.json"
    code, _ = _run(
        [
            "--config-dir",
            str(VENUES_PATH.parent),
            "--facts-json",
            str(_facts(tmp_path)),
            "--now-ms",
            NOW,
            "--status-json",
            str(target),
        ]
    )
    assert code == ExitCode.NOT_CLEARED
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["generated_at_ms"] == int(NOW)
    assert payload["exit_code"] == int(ExitCode.NOT_CLEARED)
    keys = {check["key"] for check in payload["checks"]}
    assert keys == {
        "egress_default_route",
        "egress_exit_node",
        "egress_hash",
        "budget_deduction",
        "region_reachability",
        "capability_matrix",
        "clock",
        "disk",
        "machine",
        "postgres_version",
    }
    budget = next(check for check in payload["checks"] if check["key"] == "budget_deduction")
    assert budget["provenance"]["effective"] == "measured"
    assert "sample_limited" in budget["provenance"]["qualifiers"]
    assert ADDRESS not in json.dumps(payload)


def test_injected_facts_reject_a_typo(tmp_path: Path) -> None:
    """A silently ignored key would print a perfect clock on a broken host."""
    path = tmp_path / "facts.json"
    path.write_text(json.dumps({"clock_offset": "1.35"}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown host-fact key"):
        HostFacts.from_json_file(path)
    assert HostFacts.from_mapping({"clock_offset_s": "1.35"}).clock_offset_s == Decimal("1.35")
