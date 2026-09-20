"""What this machine says about itself, gathered once, without a socket.

Everything preflight decides about the host is decided from one immutable
:class:`HostFacts` value. Two reasons it is a value and not a set of calls
scattered through the checks:

* **Tests must be able to say "a machine like this".** ``03`` §8 lists what
  this development machine cannot do — the clock is 1.35 s off, there is no
  hub egress, no PostgreSQL in reach — and a check whose verdict depends on
  whichever machine happens to run pytest is not a check. Every preflight test
  builds a :class:`HostFacts` literal, so the red cases are exercised on a
  green machine and the green cases on a red one.
* **Nothing here touches the network.** :meth:`HostFacts.from_system` reads
  ``/proc``, ``shutil.disk_usage`` and three local commands. The public egress
  IP is *given* to it (``HLENS_EGRESS_IP``, produced by the one command ``03``
  §9 runs on both ends), never fetched, so gathering facts cannot become an
  outbound request by accident. The country code and the venue reachability
  probes are the only parts of preflight that leave the machine, they live in
  :mod:`hlens_core.preflight.reachability`, and they run only when explicitly
  asked for.

Unknown is ``None`` everywhere, never a zero and never a plausible default —
``05``'s rule for the database, applied to the host: a disk of unknown size
must not read as a disk of size 0, and a clock we could not measure must not
read as a clock that is exactly right.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

__all__ = [
    "CommandRunner",
    "HostFacts",
    "run_command",
]

#: A command and its output, or ``None`` if it could not be run. Injected so
#: that the parsers below are tested against captured output rather than
#: against whatever is installed on the machine running pytest.
CommandRunner = Callable[[list[str]], str | None]

#: ``tailscale0`` — the interface name the three assertions of ``03`` §9 step
#: ④ are about. It is Tailscale's fixed device name on Linux, not a host or a
#: secret, so it belongs here rather than in ``config/venues.yaml``.
TAILSCALE_INTERFACE: Final = "tailscale0"


def run_command(argv: list[str]) -> str | None:
    """Run a local command, returning its stdout or ``None``.

    A missing binary, a non-zero exit and a timeout are all the same answer:
    *we did not learn this*. They must not raise, because a machine without
    ``chronyc`` still needs the other seven checks to run and print.
    """
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


@dataclass(frozen=True, slots=True)
class HostFacts:
    """One machine, as preflight sees it. Every field may be ``None``."""

    egress_ip: str | None = None
    """The public egress IP. **Never printed, never written to a file, never
    logged** — only hashed (``03`` §8: 报告与状态页只打印「一致 / 不一致」与前
    6 位，永不打印 IP). It is here because the hash needs it, and it leaves
    this process only as a hash."""

    egress_country: str | None = None
    """ISO country code of the egress, from the live probe (``03`` §9 决定
    B8). ``None`` means nobody asked, which is what the region matrix then
    says."""

    default_route_interface: str | None = None
    """The interface carrying the default route. Assertion 1 of ``03`` §9 step
    ④ is that it is **not** ``tailscale0``: exchange traffic must leave through
    the ordinary egress, or the rate-limit ledger is accounting for an IP we
    are not using."""

    tailscale_exit_node: str | None = None
    """The exit node in use, or ``None`` for none. Assertion 2: there must be
    none, for the same reason."""

    tailscale_present: bool | None = None
    """Whether Tailscale answered at all. Distinguishes "no exit node" from
    "no Tailscale", which are different answers to assertion 2."""

    chrony_synchronized: bool | None = None
    """``03`` §9 step ③: 确认 chrony 在同步. Separate from the offset — a clock
    can be close and drifting free, and that is the state that gets worse."""

    clock_offset_s: Decimal | None = None
    """Offset from true time in seconds, signed. ``04`` §7: 偏差需 <1 秒."""

    disk_free_bytes: int | None = None
    disk_total_bytes: int | None = None
    """``D_free`` (``03`` §12), the denominator of all three disk lines."""

    stage_free_bytes: int | None = None
    """Free space on the backup staging path (``03`` §9 step ⑤: ≥ 35 GB)."""

    cpu_threads: int | None = None
    memory_total_bytes: int | None = None
    """``03`` §9/§12 ask preflight to print these and 回填第 12 节."""

    postgres_server_version_num: int | None = None
    """``SHOW server_version_num`` as an integer, if the operator supplied it.
    ``None`` is 未检出 and prints as such: this step builds no connection and
    no tables (那是 M1-C)."""

    hostname_hint: str | None = None
    """A free-form label for the report header — ``dev`` / ``hub`` / a task id.
    Never a real hostname: AGENTS §2.3 keeps machine identities out of this
    repository, and a hint is enough to tell two runs apart."""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> HostFacts:
        """Build from ``--facts-json``. Unknown keys are refused.

        Injected facts are how every red case in the test suite is produced and
        how the CLI is demonstrated without a hub, so a typo in a key must fail
        loudly: silently ignoring ``clock_offset`` (no ``_s``) would print a
        perfect clock on a machine that is 1.35 s out.
        """
        known = {field.name for field in fields(cls)}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise ValueError(f"unknown host-fact key(s): {unknown}; known: {sorted(known)}")
        values = dict(payload)
        offset = values.get("clock_offset_s")
        if offset is not None:
            try:
                values["clock_offset_s"] = Decimal(str(offset))
            except InvalidOperation as error:
                raise ValueError(f"clock_offset_s: {offset!r} is not a number") from error
        return cls(**values)

    @classmethod
    def from_json_file(cls, path: Path) -> HostFacts:
        return cls.from_mapping(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def from_system(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        runner: CommandRunner = run_command,
        data_path: Path | None = None,
        stage_path: Path | None = None,
        server_version_num: int | None = None,
        hostname_hint: str | None = None,
    ) -> HostFacts:
        """Gather what this machine will admit to. No network, ever.

        The egress IP is read from ``HLENS_EGRESS_IP``; ``03`` §9 requires the
        same command on both ends to produce it, and keeping that command
        outside this process is what makes "preflight sends nothing" checkable
        by reading the imports rather than by trusting a flag.
        """
        environ = os.environ if env is None else env
        disk = _disk_usage(data_path)
        stage = _disk_usage(stage_path)
        offset, synchronized = _parse_chrony(runner(["chronyc", "tracking"]))
        exit_node, tailscale_present = _parse_tailscale(runner(["tailscale", "status", "--json"]))
        return cls(
            egress_ip=environ.get("HLENS_EGRESS_IP") or None,
            egress_country=None,
            default_route_interface=_parse_default_route(runner(["ip", "-4", "route", "show",
                                                                 "default"])),
            tailscale_exit_node=exit_node,
            tailscale_present=tailscale_present,
            chrony_synchronized=synchronized,
            clock_offset_s=offset,
            disk_free_bytes=None if disk is None else disk[0],
            disk_total_bytes=None if disk is None else disk[1],
            stage_free_bytes=None if stage is None else stage[0],
            cpu_threads=os.cpu_count(),
            memory_total_bytes=_parse_meminfo(_read_text(Path("/proc/meminfo"))),
            postgres_server_version_num=server_version_num,
            hostname_hint=hostname_hint,
        )


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _disk_usage(path: Path | None) -> tuple[int, int] | None:
    if path is None:
        return None
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return usage.free, usage.total


def _parse_default_route(output: str | None) -> str | None:
    """``default via 10.0.0.1 dev eth0 proto ...`` -> ``eth0``.

    Only the interface is kept. The gateway address is a fact about the local
    network that this repository has no reason to carry (AGENTS §2.3).
    """
    if not output:
        return None
    for line in output.splitlines():
        parts = line.split()
        if parts[:1] == ["default"] and "dev" in parts:
            index = parts.index("dev")
            if index + 1 < len(parts):
                return parts[index + 1]
    return None


def _parse_chrony(output: str | None) -> tuple[Decimal | None, bool | None]:
    """``chronyc tracking`` -> (offset in seconds, synchronizing?).

    Two facts, not one. "System time : 0.000000123 seconds fast of NTP time"
    gives the offset; "Leap status : Normal" is chrony saying it is actually
    disciplining the clock. ``Not synchronised`` means the offset it printed is
    stale, which is exactly the case a single number would hide.
    """
    if not output:
        return None, None
    offset: Decimal | None = None
    synchronized: bool | None = None
    for line in output.splitlines():
        name, _, value = line.partition(":")
        key = name.strip().lower()
        value = value.strip()
        if key == "system time":
            words = value.split()
            if len(words) >= 3:
                try:
                    magnitude = Decimal(words[0])
                except InvalidOperation:
                    magnitude = Decimal(0)
                offset = -magnitude if words[2] == "slow" else magnitude
        elif key == "leap status":
            synchronized = value.strip().lower() in {"normal", "insert second", "delete second"}
    return offset, synchronized


def _parse_tailscale(output: str | None) -> tuple[str | None, bool | None]:
    """``tailscale status --json`` -> (exit node id or None, is it running?).

    Tailscale reports the exit node as the peer whose ``ExitNode`` is true.
    The id, not the hostname, is kept and it is truncated by the caller: a
    tailnet hostname is one of the machine identities AGENTS §2.3 keeps out of
    reports.
    """
    if not output:
        return None, None
    try:
        document = json.loads(output)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(document, dict):
        return None, None
    peers = document.get("Peer")
    if isinstance(peers, dict):
        for peer in peers.values():
            if isinstance(peer, dict) and peer.get("ExitNode") is True:
                node_id = peer.get("ID")
                return (str(node_id) if node_id is not None else "unnamed"), True
    return None, True


def _parse_meminfo(output: str | None) -> int | None:
    if not output:
        return None
    for line in output.splitlines():
        name, _, value = line.partition(":")
        if name.strip() == "MemTotal":
            words = value.split()
            if words and words[0].isdigit():
                return int(words[0]) * 1024
    return None
