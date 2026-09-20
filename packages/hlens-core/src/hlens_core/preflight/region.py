"""区域判定、能力矩阵、覆盖矩阵 —— 决定 B8 的那一块。

``03`` §9 (决定 B8) asks preflight for something beyond red/green: a table of
**what this egress can and cannot do**, written down so the status page can
show it and so "为什么这个功能没有数字" has an answer that is not a guess.
Three tables, in the order a reader needs them:

* **区域**: the egress country, plus which endpoint families answered and how.
  ``04`` §6 is the reason it matters — ``fapi.binance.com`` returns 451 from a
  US egress while the same paths under the mirror return 200, so the region
  decides which host the adapters are allowed to use.
* **能力矩阵 / 覆盖矩阵**: one row per venue per capability, carrying the
  adapter's three separate answers (``supported`` / ``mode`` /
  ``completeness``, seam ②), the endpoint family it needs, whether that family
  answered here, and — the column that makes it a *coverage* matrix — the
  provenance of the resulting verdict, inherited from the weakest input
  (AGENTS §2). A row whose reachability was never probed can only conclude
  ``未检出``; it cannot become "available" by being wanted.
* **功能可用 / 不可用清单**: the features of ``02`` that follow from the rows
  above, which is what ``03`` §9 actually asks to be printed and what §9's
  "任一「M1 必需能力」判定不可用则拒绝启动采集" acts on.

Seam ③ again: the capability declarations belong to ``adapters``, which this
module may not import. :class:`CapabilitySetView` and
:class:`CapabilityDeclarationView` are protocols matching what
``hlens_core.adapters.capabilities.CapabilitySet`` already is; the composition
root passes the real ones in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from .provenance import Provenance, Tag, official, unverified
from .reachability import EndpointFamily, ProbeOutcome
from .textwidth import pad
from .verdict import Check, Status, worst

__all__ = [
    "M1_REQUIRED_CAPABILITIES",
    "CapabilityDeclarationView",
    "CapabilitySetView",
    "FeatureRule",
    "check_capability_matrix",
    "check_region",
]


@runtime_checkable
class CapabilityDeclarationView(Protocol):
    """Seam ②'s three answers, read-only. Never merged into one."""

    @property
    def capability(self) -> str: ...

    @property
    def supported(self) -> str: ...

    @property
    def mode(self) -> str: ...

    @property
    def completeness(self) -> str: ...


@runtime_checkable
class CapabilitySetView(Protocol):
    @property
    def venue(self) -> str: ...

    @property
    def declarations(self) -> Sequence[CapabilityDeclarationView]: ...


#: Which endpoint family each capability needs, per venue. A capability whose
#: family did not answer is not available here whatever the venue publishes —
#: that is the entire point of 决定 B8.
#:
#: The two WebSocket families are named but never probed by this step: opening
#: a stream from this egress is ``M1-G``'s job, on the production host, where
#: the fixture is recorded at the same time (``03`` §9 步骤 ⑫). Naming them
#: here keeps their rows honest (未检出) instead of absent.
CAPABILITY_FAMILY: Final[Mapping[str, Mapping[str, str]]] = {
    "binance": {
        "instruments": "binance:fapi",
        "mark_price": "binance:fapi",
        "funding_rate": "binance:fapi",
        "open_interest": "binance:fapi",
        "long_short_ratio": "binance:fapi",
        "taker_ratio": "binance:fapi",
        "klines": "binance:fapi",
        "ticker_24h": "binance:fapi",
        "mark_price_stream": "binance:ws_market",
        "liquidation_stream": "binance:ws_market",
    },
    "hyperliquid": {
        "instruments": "hyperliquid:info",
        "mark_price": "hyperliquid:info",
        "funding_rate": "hyperliquid:info",
        "open_interest": "hyperliquid:info",
        "klines": "hyperliquid:info",
        "ticker_24h": "hyperliquid:info",
        "mark_price_stream": "hyperliquid:ws",
    },
}

#: Families this step deliberately does not open. Their rows read 未检出.
UNPROBED_FAMILIES: Final[frozenset[str]] = frozenset({"binance:ws_market", "hyperliquid:ws"})

#: ``03`` §9: 任一「M1 必需能力」判定不可用则拒绝启动采集. M1 is F1/F2/F3 —
#: the universe, the minute series and the unit alignment. Streams are M2's
#: (F12) and the M4 capabilities are declared unsupported until M4, so neither
#: can block an M1 start.
M1_REQUIRED_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"instruments", "mark_price", "funding_rate", "open_interest"}
)

#: ``04`` §6 measured it on a US egress (GitHub Actions) on 2026-09-12: the
#: primary host answers 451, the mirror answers 200. The country list is only
#: the one we have evidence for; a 451 observed from anywhere is treated the
#: same way, which is why this is a hint and the probe is the authority.
GEO_BLOCKED_COUNTRIES: Final[frozenset[str]] = frozenset({"US"})


@dataclass(frozen=True, slots=True)
class FeatureRule:
    """One row of the 功能可用 / 不可用清单."""

    feature: str
    title: str
    requires: tuple[tuple[str, str], ...]
    """``(venue, capability)`` pairs that must all be available here."""

    hidden_in_blocked_region: bool = False
    """``01`` §4.9 / ``03`` §9's example: the rebate entry is hidden in the US
    and in sanctioned regions. It is a display rule, not a capability."""


FEATURE_RULES: Final[tuple[FeatureRule, ...]] = (
    FeatureRule(
        "F1",
        "币种清单（两所都在交易的永续）",
        (("binance", "instruments"), ("hyperliquid", "instruments")),
    ),
    FeatureRule(
        "F2",
        "分钟级采集（标记价 · 费率 · 持仓量）",
        (
            ("binance", "mark_price"),
            ("binance", "funding_rate"),
            ("binance", "open_interest"),
            ("hyperliquid", "mark_price"),
            ("hyperliquid", "funding_rate"),
            ("hyperliquid", "open_interest"),
        ),
    ),
    FeatureRule(
        "F3",
        "两所口径对齐（原值与折算值同时可取）",
        (("binance", "instruments"), ("hyperliquid", "funding_rate")),
    ),
    FeatureRule(
        "F12",
        "爆仓事件流（M2；只有 Binance，永远是下界）",
        (("binance", "liquidation_stream"),),
    ),
    FeatureRule(
        "F9",
        "返佣入口",
        (),
        hidden_in_blocked_region=True,
    ),
)


def check_region(
    country: str | None,
    families: Sequence[EndpointFamily],
    outcomes: Mapping[str, ProbeOutcome] | None,
) -> Check:
    """出口国家与两所可达：the probe's own report.

    ``outcomes is None`` means the live probe was not run — the default, and
    the only state this task ever produced. It is ``未检出``: neither a pass
    nor a failure, and the collector does not start on it (``04`` §6: 三件事
    没做完之前 limiter 的 live 预算按 0 处理).
    """
    lines: list[str] = ["探测计划（每一条的代价都写在后面，跑之前就能看见）："]
    lines.extend("  " + family.describe() for family in families)
    for family in families:
        if family.note:
            lines.append(f"    {family.name}: {family.note}")

    if outcomes is None:
        lines.extend(
            (
                "",
                "本次没有探测：--live-probe 未给。这台开发机与生产主机共用一个公网出口，",
                "出口上还跑着别人的采集器（03 §8 / M1-B），04 §12：在这个出口上随手探一次，",
                "花的是生产正在用的预算，Binance 侧一次 429 升级成 418 会封掉整台机器的 IP。",
                "出口国家同样未知，因此下面的能力矩阵按「未检出」判，不按「可用」判。",
            )
        )
        return Check(
            key="region_reachability",
            title="区域判定与两所可达",
            status=Status.UNKNOWN,
            headline="未检出：未运行活体探测（默认不探）",
            lines=tuple(lines),
            provenance=unverified("04 §6 表：hub 出口整列仍是 未验证"),
        )

    lines.append("")
    statuses: list[Status] = []
    for family in families:
        outcome = outcomes.get(family.name)
        if outcome is None:
            lines.append(f"  {family.name:<24} 未检出")
            statuses.append(Status.UNKNOWN)
            continue
        if outcome.geo_refused:
            lines.append(
                f"  {family.name:<24} {outcome.status_code}  按地理位置拒绝"
                f"（应用层，不是网络不可达；04 §6）"
            )
            statuses.append(Status.YELLOW)
            continue
        if outcome.ok:
            lines.append(
                f"  {family.name:<24} {outcome.status_code}  {outcome.latency_ms} ms"
            )
            statuses.append(Status.GREEN)
            continue
        lines.append(
            f"  {family.name:<24} {outcome.status_code or '-'}  {outcome.error_class}"
        )
        statuses.append(Status.RED)

    country_text = country or "未检出"
    lines.insert(0, f"出口国家：{country_text}")
    if country and country in GEO_BLOCKED_COUNTRIES:
        lines.append(
            f"  {country} 出口：fapi 主机按 04 §6 应判 451，全部 Binance 请求必须改走镜像路径。"
        )
    return Check(
        key="region_reachability",
        title="区域判定与两所可达",
        status=worst(statuses),
        headline=f"出口国家 {country_text}，{len(outcomes)} 个端点族已探",
        lines=tuple(lines),
        provenance=Provenance.of(Tag.MEASURED),
    )


@dataclass(frozen=True, slots=True)
class _Row:
    venue: str
    capability: str
    supported: str
    mode: str
    completeness: str
    family: str | None
    available: Status
    provenance: Provenance
    why: str


def check_capability_matrix(
    capability_sets: Sequence[CapabilitySetView],
    outcomes: Mapping[str, ProbeOutcome] | None,
    *,
    country: str | None = None,
) -> Check:
    """能力矩阵 + 覆盖矩阵 + 功能清单, in one printable block.

    The three answers are copied, never combined: a capability can be
    ``supported`` and still unavailable here (its family is refused), and one
    that is ``lower_bound`` stays ``lower_bound`` whatever the region says.
    """
    rows = [
        _row_for(capability_set.venue, declaration, outcomes)
        for capability_set in capability_sets
        for declaration in capability_set.declarations
    ]
    lines = [
        f"{'venue':<12}{'capability':<20}{'supported':<12}{'mode':<16}"
        f"{'completeness':<16}{pad('本区域', 12)}{'来源'}",
    ]
    for row in sorted(rows, key=lambda item: (item.venue, item.capability)):
        lines.append(
            f"{row.venue:<12}{row.capability:<20}{row.supported:<12}{row.mode:<16}"
            f"{row.completeness:<16}{pad(_availability_text(row), 12)}"
            f"{row.provenance.render()}"
        )
    unresolved = [row for row in rows if row.available is not Status.GREEN]
    if unresolved:
        lines.append("")
        lines.append("不是「可用」的每一行，各自的原因：")
        seen: set[str] = set()
        for row in sorted(unresolved, key=lambda item: (item.why, item.venue)):
            if row.why in seen:
                continue
            seen.add(row.why)
            names = ", ".join(
                f"{item.venue}:{item.capability}" for item in unresolved if item.why == row.why
            )
            lines.append(f"  {row.why}")
            lines.append(f"    -> {names}")
    lines.append("")
    lines.append("覆盖矩阵的最后一列是这一行结论的来源标签，按 AGENTS §2 继承输入里最弱的那个：")
    lines.append(
        "  一条「本区域可用」的结论 = 适配器声明（官方文档）+ 该端点族的实测可达性，"
    )
    lines.append(
        "  没探过就是 派生(未验证)·未实测 —— 它不会因为我们需要它而变硬。"
    )
    lines.append("")
    lines.append("功能可用 / 不可用（决定 B8 要的那一块）：")
    feature_statuses: list[Status] = []
    available = {
        (row.venue, row.capability): row for row in rows
    }
    for rule in FEATURE_RULES:
        status, text, provenance = _feature_verdict(rule, available, country)
        feature_statuses.append(status)
        lines.append(
            f"  {rule.feature:<5}{pad(rule.title, 48)}{pad(text, 30)}  {provenance.render()}"
        )

    blocking = [
        row
        for row in rows
        if row.capability in M1_REQUIRED_CAPABILITIES
        and row.supported == "supported"
        and row.available is not Status.GREEN
    ]
    if blocking:
        lines.append("")
        lines.append(
            "03 §9：任一「M1 必需能力」判定不可用则拒绝启动采集。以下几条还不是「可用」："
        )
        for row in blocking:
            lines.append(f"  {row.venue}:{row.capability} —— {row.why}")

    status = worst([row.available for row in rows] + feature_statuses)
    return Check(
        key="capability_matrix",
        title="能力矩阵与覆盖矩阵",
        status=status,
        headline=(
            f"{len(rows)} 行；M1 必需能力里还有 {len(blocking)} 条不是「可用」"
            if blocking
            else f"{len(rows)} 行；M1 必需能力全部可用"
        ),
        lines=tuple(lines),
        provenance=Provenance.derive(*[row.provenance for row in rows]) if rows else None,
    )


def _row_for(
    venue: str,
    declaration: CapabilityDeclarationView,
    outcomes: Mapping[str, ProbeOutcome] | None,
) -> _Row:
    capability = str(declaration.capability)
    family = CAPABILITY_FAMILY.get(venue, {}).get(capability)
    declared = official("适配器能力声明（04 §1/§2/§3）")

    if str(declaration.supported) != "supported":
        return _Row(
            venue=venue,
            capability=capability,
            supported=str(declaration.supported),
            mode=str(declaration.mode),
            completeness=str(declaration.completeness),
            family=family,
            available=Status.GREEN,
            provenance=declared,
            why="该所不发布，声明为 unsupported —— 这是一个确定的答案，不是缺口",
        )

    if family is None:
        return _Row(
            venue, capability, str(declaration.supported), str(declaration.mode),
            str(declaration.completeness), None, Status.UNKNOWN,
            Provenance.derive(declared, unverified("本模块未登记该能力的端点族")),
            "未登记端点族，无法判定本区域是否可用",
        )
    if family in UNPROBED_FAMILIES:
        return _Row(
            venue, capability, str(declaration.supported), str(declaration.mode),
            str(declaration.completeness), family, Status.UNKNOWN,
            Provenance.derive(declared, unverified(f"{family} 本步不连（M1-G 在 hub 上做）")),
            f"{family} 本步不连 WS（03 §9 ⑫：M1-G 在生产出口上连一次并录 fixture）",
        )
    outcome = outcomes.get(family) if outcomes is not None else None
    if outcome is None:
        return _Row(
            venue, capability, str(declaration.supported), str(declaration.mode),
            str(declaration.completeness), family, Status.UNKNOWN,
            Provenance.derive(declared, unverified(f"{family} 未探测")),
            f"{family} 未探测（--live-probe 未给）",
        )
    if outcome.ok:
        return _Row(
            venue, capability, str(declaration.supported), str(declaration.mode),
            str(declaration.completeness), family, Status.GREEN,
            Provenance.derive(declared, Provenance.of(Tag.MEASURED, source=family)),
            f"{family} 返回 {outcome.status_code}",
        )
    if outcome.geo_refused:
        return _Row(
            venue, capability, str(declaration.supported), str(declaration.mode),
            str(declaration.completeness), family, Status.RED,
            Provenance.derive(declared, Provenance.of(Tag.MEASURED, source=family)),
            f"{family} 返回 451：本区域按地理位置拒绝（04 §6：改走镜像路径）",
        )
    return _Row(
        venue, capability, str(declaration.supported), str(declaration.mode),
        str(declaration.completeness), family, Status.RED,
        Provenance.derive(declared, Provenance.of(Tag.MEASURED, source=family)),
        f"{family} 不可达：{outcome.error_class}",
    )


def _availability_text(row: _Row) -> str:
    """The 本区域 column.

    An ``unsupported`` capability gets its own word rather than 可用: the
    verdict on that row is green because the answer is complete and honest
    ("该所不发布"), and printing 可用 beside ``unsupported`` would read as the
    opposite of what seam ② declared.
    """
    if row.supported != "supported":
        return "该所不发布"
    return {
        Status.GREEN: "可用",
        Status.YELLOW: "受限",
        Status.UNKNOWN: "未检出",
        Status.RED: "不可用",
    }[row.available]


def _feature_verdict(
    rule: FeatureRule,
    rows: Mapping[tuple[str, str], _Row],
    country: str | None,
) -> tuple[Status, str, Provenance]:
    if rule.hidden_in_blocked_region:
        if country is None:
            return (
                Status.UNKNOWN,
                "未检出（出口国家未知）",
                unverified("出口国家未探测"),
            )
        if country in GEO_BLOCKED_COUNTRIES:
            return (
                Status.GREEN,
                f"隐藏（{country}；01 §4.9）",
                Provenance.of(Tag.MEASURED, source="出口国家"),
            )
        return (
            Status.GREEN,
            f"显示（{country}）；受制裁地区清单本仓库未定义",
            Provenance.derive(
                Provenance.of(Tag.MEASURED, source="出口国家"),
                unverified("受制裁地区清单未在文档中给出"),
            ),
        )

    needed = [rows[key] for key in rule.requires if key in rows]
    if len(needed) != len(rule.requires):
        return Status.UNKNOWN, "未检出（有能力行缺失）", unverified("能力行缺失")
    provenance = Provenance.derive(*[row.provenance for row in needed])
    statuses = [row.available for row in needed]
    verdict = worst(statuses)
    if verdict is Status.GREEN:
        unsupported = [row for row in needed if row.supported != "supported"]
        if unsupported:
            names = "、".join(f"{row.venue}:{row.capability}" for row in unsupported)
            return Status.GREEN, f"不可用（{names} 该所不发布）", provenance
        return Status.GREEN, "可用", provenance
    if verdict is Status.UNKNOWN:
        return Status.UNKNOWN, "未检出", Provenance.derive(
            provenance, unverified("端点族未探测")
        )
    return Status.RED, "不可用（本区域端点族被拒或不可达）", provenance
