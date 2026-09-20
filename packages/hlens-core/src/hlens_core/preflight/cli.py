"""The CLI: gather once, check eight things, print everything, exit honestly.

Two things about the shape of :func:`main` are deliberate.

**The budget loader and the capability declarations are required keyword
arguments with no default.** They are the two pieces preflight needs from
modules it may not import (seam ③), and giving either a default would mean
importing that module right here. Instead the composition root outside
``packages/`` — ``scripts/preflight.py``, the one file in the repository that
knows both names — passes them in. See :mod:`hlens_core.preflight.budget`.

**The live probe is off unless asked for.** Everything else runs from local
facts, so the default invocation of this command sends no packet at all. That
is not a courtesy: this machine and the production host leave through the same
public egress IP, which already carries a collector that is not ours, and
``04`` §12 is explicit that a probe on this egress spends the budget that
collector is using.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Any

from .budget import BudgetLoader, check_budget, read_observations
from .egress import EgressIdentity, check_egress
from .facts import HostFacts
from .host import check_clock, check_disk, check_machine, check_postgres
from .provenance import unverified
from .reachability import (
    EndpointFamily,
    HttpReachabilityProbe,
    ProbeOutcome,
    ReachabilityProbe,
    plan_cost,
    probe_plan,
)
from .region import CapabilitySetView, check_capability_matrix, check_region
from .report import render_report, status_json
from .rows import OpsEventRow
from .verdict import Check, Report, Status

__all__ = ["build_parser", "main", "run_preflight"]

DEFAULT_CONFIG_DIR = Path("config")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hlens-preflight",
        description=(
            "上机自检：出口哈希与三条断言 · 共享出口预算扣减 · 区域与能力矩阵 · "
            "时钟 · 磁盘 · PG >= 18.6 · 两所可达"
        ),
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help="venues.yaml 与 egress-consumers.yaml 所在目录（默认 ./config）",
    )
    parser.add_argument(
        "--facts-json",
        type=Path,
        help="用一组注入的主机事实代替真实采集（测试与演示用；键名见 HostFacts）",
    )
    parser.add_argument(
        "--data-path", type=Path, help="量 D_free 的路径（默认不量）"
    )
    parser.add_argument(
        "--stage-path", type=Path, help="备份暂存路径（03 §9 步骤 ⑤：>= 35 GB）"
    )
    parser.add_argument(
        "--server-version-num",
        type=int,
        help="`SHOW server_version_num` 的值；不给就判「未检出」，本命令不自己连库",
    )
    parser.add_argument(
        "--live-probe",
        action="store_true",
        help=(
            "真的去打两所的最便宜探针。默认不打：本出口与生产共用，"
            "上面还跑着别人的采集器（04 §12）"
        ),
    )
    parser.add_argument("--status-json", type=Path, help="把 status.json 写到这里")
    parser.add_argument("--now-ms", type=int, help="固定时间戳（让输出可复现）")
    parser.add_argument("--hint", default="", help="报告抬头上的一句话标记（不写主机名）")
    return parser


def run_preflight(
    *,
    facts: HostFacts,
    venues_path: Path,
    consumers_path: Path,
    budget_loader: BudgetLoader,
    capability_sets: Sequence[CapabilitySetView],
    probe: ReachabilityProbe | None = None,
    country: str | None = None,
    now_ms: int,
) -> Report:
    """Every check, in the order ``03`` §9 步骤 ④ reads them.

    ``probe is None`` means the live probe was not run; the region and
    capability checks then answer 未检出 rather than assuming anything.
    """
    checks: list[Check] = []
    rows: list[object] = []

    egress_checks, egress_rows = check_egress(
        facts,
        EgressIdentity(
            salt=os.environ.get("EGRESS_SALT"),
            expected_hash=os.environ.get("EXPECTED_EGRESS_HASH"),
        ),
        now_ms=now_ms,
    )
    checks.extend(egress_checks)
    rows.extend(egress_rows)

    checks.append(_budget_check(venues_path, consumers_path, budget_loader))

    families = probe_plan(venues_path)
    outcomes: dict[str, ProbeOutcome] | None = None
    if probe is not None:
        outcomes = {family.name: probe.probe(family) for family in families}
    checks.append(check_region(country, families, outcomes))
    checks.append(check_capability_matrix(capability_sets, outcomes, country=country))

    checks.append(check_clock(facts))
    checks.append(check_disk(facts))
    checks.append(check_machine(facts))
    checks.append(check_postgres(facts))

    report = Report(checks=tuple(checks), rows=tuple(rows))
    rows.append(
        OpsEventRow(
            kind="preflight",
            ts=now_ms,
            ok=report.status is Status.GREEN,
            detail={
                "status": report.status.value,
                "red": [check.key for check in report.of_status(Status.RED)],
                "unknown": [check.key for check in report.of_status(Status.UNKNOWN)],
                "yellow": [check.key for check in report.of_status(Status.YELLOW)],
            },
            ingest_ts=now_ms,
        )
    )
    return Report(checks=tuple(checks), rows=tuple(rows))


def _budget_check(venues_path: Path, consumers_path: Path, loader: BudgetLoader) -> Check:
    """Load the two config files through the ledger's own loader, then print.

    The loader raises ``ConfigError`` — a ``ValueError`` subclass — when the
    files disagree, when a reservation has no assertion behind it, or when the
    reservations alone overrun the egress ceiling. Catching ``ValueError``
    rather than the class itself is what keeps even the failure path free of an
    import across the seam, and the message is printed verbatim: it already
    says which file, which bucket and which rule, and rewording it here would
    only make the two disagree.
    """
    try:
        budget = loader(venues_path, consumers_path)
    except (ValueError, OSError) as error:
        return Check(
            key="budget_deduction",
            title="共享出口预算扣减",
            status=Status.RED,
            headline="拒绝放行：预算配置加载失败",
            lines=(
                f"{type(error).__name__}: {error}",
                "",
                "03 §6.1：`reserved` 段缺失、或与运行中的旧采集器对不上，即拒绝启动。",
                "加载器拒载的每一种情形都是对的：预留额撑爆天花板，说的正是"
                "「这个桶没有安全的分法」；",
                "裸的 0 没有断言与复核人，说的是「旧采集器不碰 X」在靠记忆而不是靠核对。",
                "preflight 不在这里另算一遍，也不降级放行。",
            ),
            provenance=unverified("加载失败，本次没有可用的扣减结果"),
        )
    return check_budget(budget, read_observations(consumers_path))


def _probe_country(client_url: str | None) -> str | None:
    """Cloudflare-style trace: ``loc=XX`` on one line (``03`` §9 决定 B8).

    The URL comes from the environment, never from this file: AGENTS §3 keeps
    hosts out of the code, and the only host list in this repository is
    ``config/venues.yaml``, which is for venues.
    """
    if not client_url:
        return None
    import httpx

    from .reachability import TIMEOUT

    try:
        response = httpx.get(client_url, timeout=TIMEOUT)
    except httpx.HTTPError:
        return None
    for line in response.text.splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "loc":
            return value.strip().upper() or None
    return None


def main(
    argv: Sequence[str] | None = None,
    *,
    budget_loader: BudgetLoader,
    capability_sets: Sequence[CapabilitySetView],
    stdout: IO[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run the checks and return the exit code. Prints the whole report."""
    args = build_parser().parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    environ = os.environ if env is None else env
    now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)

    if args.facts_json is not None:
        facts = HostFacts.from_json_file(args.facts_json)
    else:
        facts = HostFacts.from_system(
            env=environ,
            data_path=args.data_path,
            stage_path=args.stage_path,
            server_version_num=args.server_version_num,
            hostname_hint=args.hint or None,
        )
    if args.server_version_num is not None and facts.postgres_server_version_num is None:
        facts = _with_version(facts, args.server_version_num)

    probe: ReachabilityProbe | None = None
    country: str | None = facts.egress_country
    http_probe: HttpReachabilityProbe | None = None
    if args.live_probe:
        families: Sequence[EndpointFamily] = probe_plan(args.config_dir / "venues.yaml")
        print(
            "--live-probe：本次会真的发出请求，代价 "
            + "、".join(f"{bucket} {cost}" for bucket, cost in plan_cost(families).items()),
            file=out,
        )
        http_probe = HttpReachabilityProbe()
        probe = http_probe
        if country is None:
            country = _probe_country(environ.get("HLENS_EGRESS_TRACE_URL"))

    try:
        report = run_preflight(
            facts=facts,
            venues_path=args.config_dir / "venues.yaml",
            consumers_path=args.config_dir / "egress-consumers.yaml",
            budget_loader=budget_loader,
            capability_sets=capability_sets,
            probe=probe,
            country=country,
            now_ms=now_ms,
        )
    finally:
        if http_probe is not None:
            http_probe.close()

    print(render_report(report, header=args.hint), file=out)
    if args.status_json is not None:
        payload: dict[str, Any] = status_json(report, generated_at_ms=now_ms)
        args.status_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return int(report.exit_code)


def _with_version(facts: HostFacts, version: int) -> HostFacts:
    from dataclasses import replace

    return replace(facts, postgres_server_version_num=version)
