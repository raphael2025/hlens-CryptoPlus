"""Three demonstrations, written as tests so they cannot rot.

Run them with output shown::

    uv run pytest tests/test_preflight_demos.py -q -s

They assert, so they fail when the behaviour changes; they also print what a
person should be able to *see* rather than take on trust:

1. this development machine fails its own preflight, for the reason ``03`` §8
   already predicted, and the exit code says so;
2. the same command with ``--live-probe`` — driven entirely through ``respx``,
   because the real thing would spend production's budget — prints its price
   first and then resolves the capability matrix;
3. a reservation that overruns the egress ceiling is refused, and the refusal
   explains itself.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
import respx

from conftest import VENUES_PATH, preflight_fixture, set_hl_reservation
from hlens_core.adapters.binance.capabilities import BINANCE_CAPABILITIES
from hlens_core.adapters.hyperliquid.capabilities import HYPERLIQUID_CAPABILITIES
from hlens_core.preflight import ExitCode, egress_hash, main, probe_plan
from hlens_core.ratelimit import LedgerConfig

CAPABILITY_SETS = (BINANCE_CAPABILITIES, HYPERLIQUID_CAPABILITIES)
NOW = "1789000000000"
ADDRESS = "198.51.100.23"  # RFC 5737 TEST-NET-2, never a real egress
SALT = "unit-test-salt"
GB = 1000**3

#: This development machine, as ``03`` §8 describes it: the clock is 1.35 s out
#: and there is no PostgreSQL in reach. The disk and CPU figures are M1-B's
#: measurements of the same machine (report §E).
DEV_MACHINE: dict[str, object] = {
    "egress_ip": ADDRESS,
    "default_route_interface": "eth0",
    "tailscale_exit_node": None,
    "tailscale_present": True,
    "chrony_synchronized": True,
    "clock_offset_s": "1.35",
    "disk_free_bytes": 919 * GB,
    "disk_total_bytes": 1007 * GB,
    "stage_free_bytes": 919 * GB,
    "cpu_threads": 26,
    "memory_total_bytes": 15 * GB,
    "postgres_server_version_num": None,
    "hostname_hint": "dev",
}


@pytest.fixture(autouse=True)
def _egress_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGRESS_SALT", SALT)
    monkeypatch.setenv("EXPECTED_EGRESS_HASH", egress_hash(SALT, ADDRESS))


def _facts_file(tmp_path: Path, **overrides: object) -> Path:
    payload = dict(DEV_MACHINE)
    payload.update(overrides)
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


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
def test_demo_one_this_machine_fails_its_own_preflight(tmp_path: Path) -> None:
    """DEMO 1 — 03 §8: 本机时钟偏差 1.35 s > 1 s 门槛，M1-F 的全绿本机做不了."""
    code, output = _run(
        [
            "--config-dir",
            str(VENUES_PATH.parent),
            "--facts-json",
            str(_facts_file(tmp_path)),
            "--now-ms",
            NOW,
            "--hint",
            "M1-A3 DEMO 1 · 注入的假值 · 本机口径",
        ]
    )
    print("\n=== DEMO 1: preflight on an injected copy of this machine ===")
    print(output)
    print(f"exit code = {code}")

    assert respx.calls.call_count == 0, "a preflight demo must not touch a venue"
    assert code == ExitCode.REFUSED
    assert "[FAIL] 时钟" in output
    assert "[WARN] 共享出口预算扣减" in output
    assert "账面平，现实没平" in output
    assert "[ ?? ] PostgreSQL >= 18.6" in output


@respx.mock
def test_demo_two_the_live_probe_path_offline(tmp_path: Path) -> None:
    """DEMO 2 — what ``--live-probe`` does, without doing it.

    Every response here is a hand-built fixture tagged ``source: documented``
    (``tests/fixtures/preflight/``). The point of the demo is the two things
    that happen around the requests: the cost is printed *before* they are
    sent, and the capability matrix stops saying 未检出 once they answer.
    """
    families = {family.name: family for family in probe_plan(VENUES_PATH)}
    status_code, payload = preflight_fixture("binance_ping_ok")
    respx.get(families["binance:fapi"].url).mock(
        return_value=httpx.Response(status_code, json=payload)
    )
    respx.get(families["binance:fapi_mirror"].url).mock(
        return_value=httpx.Response(status_code, json=payload)
    )
    meta_status, meta_payload = preflight_fixture("hyperliquid_meta_ok")
    respx.post(families["hyperliquid:info"].url).mock(
        return_value=httpx.Response(meta_status, json=meta_payload)
    )

    code, output = _run(
        [
            "--config-dir",
            str(VENUES_PATH.parent),
            "--facts-json",
            str(_facts_file(tmp_path, clock_offset_s="0.01", postgres_server_version_num=180006)),
            "--now-ms",
            NOW,
            "--live-probe",
            "--hint",
            "M1-A3 DEMO 2 · --live-probe，但全部走 respx 的离线 fixture",
        ]
    )
    print("\n=== DEMO 2: the --live-probe path, answered by documented fixtures ===")
    print(output)
    print(f"exit code = {code}")

    # The price is on the screen before anything is spent (04 §12).
    assert "--live-probe：本次会真的发出请求，代价" in output
    assert "binance:fapi_weight 2" in output
    assert "hyperliquid:info_weight 20" in output
    assert respx.calls.call_count == 3

    # And the matrix resolves: the M1 capabilities become available, while the
    # two stream rows stay unknown because this step never opens a WebSocket.
    assert "M1 必需能力全部可用" in output
    assert "本步不连" in output
    assert code == ExitCode.NOT_CLEARED


@respx.mock
def test_demo_three_a_reservation_over_the_ceiling_is_refused(tmp_path: Path) -> None:
    """DEMO 3 — §6.1: 预留额撑爆天花板时加载器拒载，「那个拒绝是对的」.

    The number used is not invented: ``1147`` is the single-minute maximum
    M1-B actually observed from ``hub_legacy``. Writing the measured peak into
    the reservation instead of its p95 makes the loader say out loud what the
    measurement means — there is no safe split of this bucket.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "venues.yaml").write_text(
        VENUES_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (config_dir / "egress-consumers.yaml").write_text(
        set_hl_reservation(
            None,
            "reserved_per_min: 1147\n"
            "source: measured\n"
            "note: M1-B 观测最大值，不是 p95",
        ),
        encoding="utf-8",
    )

    code, output = _run(
        [
            "--config-dir",
            str(config_dir),
            "--facts-json",
            str(_facts_file(tmp_path, clock_offset_s="0.01")),
            "--now-ms",
            NOW,
            "--hint",
            "M1-A3 DEMO 3 · HL 预留额 953 -> 1147（越过 1080 天花板）",
        ]
    )
    print("\n=== DEMO 3: the reservation table refuses to load ===")
    print(output)
    print(f"exit code = {code}")

    assert respx.calls.call_count == 0
    assert code == ExitCode.REFUSED
    assert "拒绝放行：预算配置加载失败" in output
    assert "1147" in output
    assert "1080" in output
    assert "preflight 不在这里另算一遍，也不降级放行。" in output
