"""Clock, disk, PostgreSQL — and the parsers that feed them.

Every case runs on injected facts. That is not convenience: the verdicts here
depend on the machine, and a test that asked the machine running pytest would
pass or fail for reasons that have nothing to do with the code. ``03`` §8 says
this development machine is 1.35 s out; :func:`test_the_dev_machine_clock_is_red`
pins what preflight does about that, and it does so on a host whose clock is
fine.
"""

from __future__ import annotations

from decimal import Decimal

from hlens_core.preflight import (
    MIN_SERVER_VERSION_NUM,
    HostFacts,
    Status,
    check_clock,
    check_disk,
    check_machine,
    check_postgres,
)
from hlens_core.preflight.facts import _parse_chrony, _parse_default_route, _parse_tailscale
from hlens_core.preflight.host import STAGE_MIN_BYTES

GB = 1000**3


# --------------------------------------------------------------------------- #
# Clock
# --------------------------------------------------------------------------- #
def test_a_disciplined_clock_inside_a_second_is_green() -> None:
    check = check_clock(
        HostFacts(clock_offset_s=Decimal("0.012"), chrony_synchronized=True)
    )
    assert check.status is Status.GREEN


def test_the_dev_machine_clock_is_red() -> None:
    """``03`` §8: 本机时钟偏差 1.35 s > 1 s 门槛，所以 M1-F 的全绿本机做不了.

    There is no flag, no environment variable and no fixture that turns this
    green. The threshold is the check.
    """
    check = check_clock(
        HostFacts(clock_offset_s=Decimal("1.35"), chrony_synchronized=True)
    )
    assert check.status is Status.RED
    assert "1.35" in check.headline


def test_a_clock_that_is_close_but_free_running_is_red() -> None:
    """Being right now and drifting is the state that gets worse unattended."""
    check = check_clock(
        HostFacts(clock_offset_s=Decimal("0.05"), chrony_synchronized=False)
    )
    assert check.status is Status.RED


def test_an_unmeasured_clock_is_unknown() -> None:
    assert check_clock(HostFacts()).status is Status.UNKNOWN


def test_chrony_output_is_parsed_for_two_separate_facts() -> None:
    output = (
        "Reference ID    : 0A000001 (ntp.example)\n"
        "System time     : 1.350000000 seconds slow of NTP time\n"
        "Frequency       : 12.345 ppm fast\n"
        "Leap status     : Normal\n"
    )
    offset, synchronized = _parse_chrony(output)
    assert offset == Decimal("-1.350000000")
    assert synchronized is True

    offset, synchronized = _parse_chrony(
        "System time     : 0.000012 seconds fast of NTP time\n"
        "Leap status     : Not synchronised\n"
    )
    assert offset == Decimal("0.000012")
    assert synchronized is False

    assert _parse_chrony(None) == (None, None)


def test_route_and_tailscale_parsers_keep_no_addresses() -> None:
    assert _parse_default_route("default via 10.0.0.1 dev eth0 proto dhcp\n") == "eth0"
    assert _parse_default_route("default dev tailscale0 scope link\n") == "tailscale0"
    assert _parse_default_route("") is None

    exit_node, present = _parse_tailscale(
        '{"Peer": {"key1": {"ID": "n123", "ExitNode": false},'
        ' "key2": {"ID": "n456", "ExitNode": true}}}'
    )
    assert exit_node == "n456"
    assert present is True
    assert _parse_tailscale('{"Peer": {}}') == (None, True)
    assert _parse_tailscale(None) == (None, None)


# --------------------------------------------------------------------------- #
# Disk
# --------------------------------------------------------------------------- #
def test_disk_prints_the_three_lines_that_use_d_free() -> None:
    check = check_disk(
        HostFacts(
            disk_free_bytes=900 * GB,
            disk_total_bytes=1000 * GB,
            stage_free_bytes=900 * GB,
        )
    )
    assert check.status is Status.GREEN
    body = "\n".join(check.lines)
    assert "M4 门控线" in body
    assert "压缩触发线" in body
    assert "告警线" in body
    assert "135.0 GB" in body  # 15 % of 900 GB


def test_a_disk_that_cannot_hold_the_staging_area_is_red() -> None:
    check = check_disk(
        HostFacts(disk_free_bytes=20 * GB, disk_total_bytes=500 * GB, stage_free_bytes=20 * GB)
    )
    assert check.status is Status.RED
    assert str(STAGE_MIN_BYTES // GB) in "\n".join(check.lines)


def test_a_disk_below_the_first_year_estimate_warns() -> None:
    check = check_disk(
        HostFacts(disk_free_bytes=80 * GB, disk_total_bytes=500 * GB, stage_free_bytes=80 * GB)
    )
    assert check.status is Status.YELLOW


def test_an_unmeasured_disk_is_unknown() -> None:
    assert check_disk(HostFacts()).status is Status.UNKNOWN


# --------------------------------------------------------------------------- #
# PostgreSQL
# --------------------------------------------------------------------------- #
def test_no_database_is_unknown_not_green() -> None:
    """坑 2 / ``03`` §9: 本步不建连接、不建表，读不到就报未检出."""
    check = check_postgres(HostFacts())
    assert check.status is Status.UNKNOWN
    assert "未检出" in check.headline


def test_the_exact_boundary_of_the_cve_fix() -> None:
    assert check_postgres(
        HostFacts(postgres_server_version_num=MIN_SERVER_VERSION_NUM)
    ).status is Status.GREEN
    below = check_postgres(HostFacts(postgres_server_version_num=MIN_SERVER_VERSION_NUM - 1))
    assert below.status is Status.RED
    assert "CVE-2026-19385" in "\n".join(below.lines)


def test_the_machine_line_is_informational_but_not_blank() -> None:
    check = check_machine(HostFacts(cpu_threads=26, memory_total_bytes=16 * GB))
    assert check.status is Status.GREEN
    assert "26" in check.headline
    assert check_machine(HostFacts()).status is Status.UNKNOWN
