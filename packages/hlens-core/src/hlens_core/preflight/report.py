"""Rendering: the terminal report, and the ``status.json`` block.

Two audiences, one set of facts. The terminal version is what ``03`` §9 步骤 ④
is read from during a deployment; the JSON is what ``03`` §11 puts on the
status page ("preflight 的区域与能力矩阵" is a permanent block there). Neither
is allowed to say more than the checks did, so both are pure functions of a
:class:`~hlens_core.preflight.verdict.Report`.

The terminal form prints every check's evidence, not only its verdict. ``03``
§6.1 asks for "扣减过程与结果" and the deduction is the longest block in the
output on purpose: a table nobody can reconstruct is a table nobody will
question.
"""

from __future__ import annotations

from typing import Any

from .rows import render_rows
from .verdict import Report, Status

__all__ = ["render_report", "status_json"]

_WIDTH = 96


def render_report(report: Report, *, header: str = "") -> str:
    """The whole report as text. Deterministic: no timestamps, no colours.

    No ANSI colour anywhere — this output is pasted into pull requests and
    served inside a ``<pre>``, and the ``[FAIL]`` marker has to survive both.
    """
    out: list[str] = []
    out.append("=" * _WIDTH)
    out.append("hlens CryptoPlus · preflight" + (f" · {header}" if header else ""))
    out.append("=" * _WIDTH)

    for check in report.checks:
        out.append("")
        out.extend(check.render())

    out.append("")
    out.append("-" * _WIDTH)
    out.append("汇总")
    out.extend(f"  {line}" for line in report.summary())

    rows = render_rows(report.rows)
    if rows:
        out.append("")
        out.append("本次会写进库的行（source_health / ops_event / ingest_gap）——")
        out.append("这两张表要到 M1-C 才存在，所以现在打在标准输出上，不是调试信息：")
        out.extend(f"  {line}" for line in rows)

    out.append("")
    out.append("-" * _WIDTH)
    out.append(f"结论 {report.status.value.upper()}  退出码 {int(report.exit_code)}")
    out.append(_conclusion(report))
    return "\n".join(out)


def _conclusion(report: Report) -> str:
    if report.status is Status.GREEN:
        return "  全绿：03 §9 的这一步可以继续。"
    if report.status is Status.RED:
        failed = "、".join(check.title for check in report.of_status(Status.RED))
        return f"  判红：{failed}。拒绝放行 —— 红就是红，没有跳过开关。"
    unknown = "、".join(check.title for check in report.of_status(Status.UNKNOWN))
    warned = "、".join(check.title for check in report.of_status(Status.YELLOW))
    parts = ["  未放行："]
    if unknown:
        parts.append(f"未检出 [{unknown}]")
    if warned:
        parts.append(f"警告 [{warned}]")
    parts.append("—— 没有东西被判错，但也没有东西被判对。")
    return " ".join(parts)


def status_json(report: Report, *, generated_at_ms: int) -> dict[str, Any]:
    """The block ``03`` §11 keeps on the status page.

    Includes the provenance of every check, because the status page shows the
    capability matrix to a reader who has no other way to tell a measured row
    from an assumed one.
    """
    return {
        "generated_at_ms": generated_at_ms,
        "status": report.status.value,
        "exit_code": int(report.exit_code),
        "checks": [
            {
                "key": check.key,
                "title": check.title,
                "status": check.status.value,
                "headline": check.headline,
                "lines": list(check.lines),
                "provenance": (
                    None
                    if check.provenance is None
                    else {
                        "tag": check.provenance.tag.value,
                        "effective": check.provenance.effective.value,
                        "qualifiers": sorted(q.value for q in check.provenance.qualifiers),
                        "render": check.provenance.render(),
                    }
                ),
            }
            for check in report.checks
        ],
        "rows": render_rows(report.rows),
    }
