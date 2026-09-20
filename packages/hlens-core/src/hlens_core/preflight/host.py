"""The three checks about the machine: clock, disk, PostgreSQL version.

Each one exists because a specific, documented thing goes wrong without it.

**Clock** (``04`` §7 第 4 项: 偏差需 <1 秒). Every record carries ``ts`` and
``ingest_ts``, staleness is measured from them, and ``F5``'s "更新于 X 分钟前"
is computed against this clock. A host that is a second and a half out does not
report an error; it reports numbers that are quietly wrong, and ``03`` §8 says
this development machine is exactly that host — 1.35 s, over the 1 s line.

**There is no development-mode skip here, and no adjustable threshold.**
``03`` §8 硬规则 ③ allows dev to downgrade this one to a warning; this CLI does
not implement that downgrade, and the PR says so under "Doc corrections". The
reasoning: the threshold is what the check *is*. A self-check that can be told
to pass has stopped being a self-check, and the failure it hides — everything
timestamped slightly wrong, forever, with no error anywhere — is the failure a
preflight exists to catch. ``03`` §8 already concludes that 本机做不了 M1-F
的全绿; printing that honestly costs nothing, while relaxing the line would
make every future green meaningless.

**Disk** (``03`` §12). ``D_free`` is 未验证 for the production host and is the
denominator of all three gates there, so preflight measures it, prints the
three lines derived from it, and ``M1-F`` 回填第 12 节 with what it printed.

**PostgreSQL** (``03`` §3 / §10, AGENTS §9). ``server_version_num >= 180006``
is 红即拒启 rather than a warning because the daily full backup walks straight
into CVE-2026-19385, the ``pg_dump`` heap overflow fixed in 18.6 — an unfixed
server makes the restore drill test a dump that may not be trustworthy. With
no database in reach the answer is **未检出**, never green: this step opens no
connection and creates no table (那是 ``M1-C``), and the version is supplied by
the operator or the wrapper.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from .facts import HostFacts
from .provenance import Provenance, Qualifier, Tag, measured, official
from .verdict import Check, Status

__all__ = [
    "CLOCK_THRESHOLD_S",
    "MIN_SERVER_VERSION_NUM",
    "STAGE_MIN_BYTES",
    "check_clock",
    "check_disk",
    "check_machine",
    "check_postgres",
]

#: ``04`` §7: 偏差需 <1 秒. Not a tunable.
CLOCK_THRESHOLD_S: Final = Decimal("1")

#: AGENTS §9 / ``03`` §3: 18.6, the release that fixes CVE-2026-19385.
MIN_SERVER_VERSION_NUM: Final = 180006

#: ``03`` §9 步骤 ⑤ / §10 的算式：暂存分区给 35 GB.
STAGE_MIN_BYTES: Final = 35 * 1000**3

#: ``03`` §12 第一年增量 ≈ 74.8 GB + PG/OS 10 + WAL 10 + 暂存 30 ≈ 125 GB.
FIRST_YEAR_BYTES: Final = 125 * 1000**3

_GB: Final = 1000**3


def _gb(value: int) -> str:
    return f"{value / _GB:,.1f} GB"


def check_clock(facts: HostFacts) -> Check:
    """时钟：偏差 < 1 秒，且 chrony 真的在同步。"""
    offset = facts.clock_offset_s
    synchronized = facts.chrony_synchronized
    if offset is None and synchronized is None:
        return Check(
            key="clock",
            title="时钟（偏差 < 1 s）",
            status=Status.UNKNOWN,
            headline="未检出：chrony 没有应答",
            lines=("`chronyc tracking` 不可用；没有测到偏差就不作绿。",),
            provenance=measured(qualifiers=(Qualifier.NOT_MEASURED,)),
        )

    lines: list[str] = []
    status = Status.GREEN
    if offset is None:
        status = Status.UNKNOWN
        lines.append("读到了同步状态但没读到偏差值。")
    else:
        magnitude = abs(offset)
        lines.append(
            f"偏差 {offset:+} s，门槛 {CLOCK_THRESHOLD_S} s（04 §7 第 4 项）。"
        )
        if magnitude >= CLOCK_THRESHOLD_S:
            status = Status.RED
            lines.append(
                "超门槛。每条记录的 ts / ingest_ts 都从这只钟来，F5 的「更新于 X 分钟前」也是；"
            )
            lines.append(
                "差这么多不会报错，只会让所有时间戳一直偏着 —— 这正是自检存在的理由。"
            )
            lines.append(
                "03 §8 硬规则 ③ 允许 dev 把这一条打黄跳过；本 CLI 不实现那个降级"
                "（理由见模块文档与 PR 的 Doc corrections）。"
            )
    if synchronized is False:
        status = Status.RED
        lines.append("chrony 报告未在同步：偏差值是陈的，而且只会越来越大（03 §9 步骤 ③）。")
    elif synchronized is None:
        lines.append("没读到 chrony 的同步状态。")
        status = Status.UNKNOWN if status is Status.GREEN else status
    else:
        lines.append("chrony 在同步。")

    headline = "偏差未知" if offset is None else f"偏差 {offset:+} s"
    if status is Status.RED:
        headline += "（超门槛，判红）"
    return Check(
        key="clock",
        title="时钟（偏差 < 1 s）",
        status=status,
        headline=headline,
        lines=tuple(lines),
        provenance=measured("chronyc tracking"),
    )


def check_disk(facts: HostFacts) -> Check:
    """磁盘 ``D_free`` 与从它派生的三条线（03 §12）。"""
    free = facts.disk_free_bytes
    if free is None:
        return Check(
            key="disk",
            title="磁盘 D_free",
            status=Status.UNKNOWN,
            headline="未检出：没有读到数据盘",
            lines=(
                "03 §12 的三条门控线都以 D_free 为分母；读不到就一条也算不出来。",
            ),
            provenance=measured(qualifiers=(Qualifier.NOT_MEASURED,)),
        )

    total = facts.disk_total_bytes
    lines = [
        f"D_free = {_gb(free)}" + (f" / 总 {_gb(total)}" if total else ""),
        f"  M4 门控线   trade_tick 滚动 7 天稳态 <= D_free x 15% = {_gb(int(free * 0.15))}",
        f"  压缩触发线  我们这一套总占用 > D_free x 60% = {_gb(int(free * 0.60))}",
        f"  告警线      > D_free x 70% = {_gb(int(free * 0.70))}",
        f"第一年增量估算 {_gb(FIRST_YEAR_BYTES)}（03 §12，估算 ±20%）。",
    ]
    status = Status.GREEN
    if free < FIRST_YEAR_BYTES:
        status = Status.YELLOW
        lines.append("D_free 低于第一年的估算增量：够开工，撑不到十二个月。")
    if free < STAGE_MIN_BYTES:
        status = Status.RED
        lines.append(
            f"D_free 连暂存要的 {_gb(STAGE_MIN_BYTES)} 都不够（03 §9 步骤 ⑤ / §10）。"
        )

    stage = facts.stage_free_bytes
    if stage is None:
        lines.append("暂存分区：未检出（没有给 --stage-path）。")
        status = Status.UNKNOWN if status is Status.GREEN else status
    else:
        lines.append(f"暂存分区可用 {_gb(stage)}，要求 >= {_gb(STAGE_MIN_BYTES)}。")
        if stage < STAGE_MIN_BYTES:
            status = Status.RED
            lines.append(
                "暂存写满之后每小时增量会静默失败，而它是异地 RPO 的唯一来源（03 §10）。"
            )

    return Check(
        key="disk",
        title="磁盘 D_free",
        status=status,
        headline=f"D_free {_gb(free)}",
        lines=tuple(lines),
        provenance=Provenance.derive(
            measured("shutil.disk_usage"),
            official("03 §12 的三条比例"),
        ),
    )


def check_postgres(facts: HostFacts) -> Check:
    """``server_version_num >= 180006``，读不到就是未检出。"""
    version = facts.postgres_server_version_num
    if version is None:
        return Check(
            key="postgres_version",
            title="PostgreSQL >= 18.6",
            status=Status.UNKNOWN,
            headline="未检出：没有拿到 server_version_num",
            lines=(
                "本步不建连接、不建表（那是 M1-C）；版本由运维或包装脚本用",
                "`SHOW server_version_num` 取到后经 --server-version-num 传进来。",
                "没有数据库时这一条是「未检出」，不是绿 —— 断言没跑过就不能算通过。",
            ),
            provenance=measured(qualifiers=(Qualifier.NOT_MEASURED,)),
        )
    if version < MIN_SERVER_VERSION_NUM:
        return Check(
            key="postgres_version",
            title="PostgreSQL >= 18.6",
            status=Status.RED,
            headline=f"server_version_num = {version} < {MIN_SERVER_VERSION_NUM}",
            lines=(
                "CVE-2026-19385（pg_dump 堆缓冲区溢出，CVSS 8.8）在 18.6 修复；",
                "本项目每日全量直接踩这条，所以这里是红即拒启，不是警告（03 §10）。",
                "另：dev 与 prod 必须同一大版本，否则恢复演练是假的（03 §3 决定 C5）。",
            ),
            provenance=Provenance.derive(
                measured("SHOW server_version_num"),
                official("AGENTS §9 / 03 §3 的版本下限"),
            ),
        )
    return Check(
        key="postgres_version",
        title="PostgreSQL >= 18.6",
        status=Status.GREEN,
        headline=f"server_version_num = {version}",
        provenance=Provenance.derive(
            measured("SHOW server_version_num"),
            official("AGENTS §9 / 03 §3 的版本下限"),
        ),
    )


def check_machine(facts: HostFacts) -> Check:
    """CPU / 内存 —— ``03`` §9 与 §12 要 preflight 把这两个数打出来并回填第 12 节。

    Informational: it never fails, because ``03`` §12 sets no threshold on
    either. It is ``未检出`` when the numbers are missing, so that a report
    copied into the document cannot silently contain blanks.
    """
    lines = []
    known = 0
    if facts.cpu_threads is not None:
        lines.append(f"CPU {facts.cpu_threads} 线程")
        known += 1
    if facts.memory_total_bytes is not None:
        lines.append(f"内存 {_gb(facts.memory_total_bytes)}")
        known += 1
    if not lines:
        return Check(
            key="machine",
            title="机器（CPU / 内存）",
            status=Status.UNKNOWN,
            headline="未检出",
            lines=("03 §12 的这两格仍是 未验证，本次没能填上。",),
            provenance=measured(qualifiers=(Qualifier.NOT_MEASURED,)),
        )
    return Check(
        key="machine",
        title="机器（CPU / 内存）",
        status=Status.GREEN if known == 2 else Status.UNKNOWN,
        headline=" · ".join(lines),
        lines=(
            "03 §9/§12：由 preflight 打出来并回填第 12 节；"
            "hub 上的性能数字必须在 hub 上复测（03 §8）。",
        ),
        provenance=Provenance.of(Tag.MEASURED),
    )
