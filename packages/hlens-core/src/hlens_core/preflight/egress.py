"""The egress identity check, and the three assertions of ``03`` §9 step ④.

Why a hash and not the address
------------------------------
``03`` §8: 两端同一条命令取出口 IP，算 ``SHA256(EGRESS_SALT || ip)`` 取前 12
hex；报告与状态页只打印「一致 / 不一致」与前 6 位，**永不打印 IP**。Two
separate reasons, both binding here:

* **This repository is public.** AGENTS §3 forbids a real IP, hostname, email
  or absolute path of any live machine from entering it, and production is one
  of raphael's own machines. A hash in a committed report leaks nothing.
* **A bare hash of an IPv4 address is not a hash.** The space is 2^32; anyone
  can enumerate it in seconds. The salt is what makes the digest opaque, which
  is why ``EGRESS_SALT`` is a secret in the machine's ``.env`` and why ``03``
  §9 calls it 唯一一把「改一半就让生产起不来」的键: rotate the salt without
  recomputing ``EXPECTED_EGRESS_HASH`` in the same commit and this check goes
  red on the next start.

What the comparison means changed, and this module is where it shows
--------------------------------------------------------------------
``04`` §11 第 11 项 retired the old rule ("出口 IP 与 hub 相同即拒启"):
production *is* the hub, so the egress being the hub's is the expected state.
What survives is ``03`` §13's use — the hash changes when the ISP hands out a
different public address, and that has to be noticed, because every reservation
in ``config/egress-consumers.yaml`` is a statement about *that* address's
budget. A mismatch is therefore an ingest gap with cause ``egress_change``
(§5), not merely a warning.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from hlens_core.contracts import Venue

from .facts import TAILSCALE_INTERFACE, HostFacts
from .provenance import Provenance, Qualifier, Tag
from .rows import EGRESS_CHANGE, IngestGapRow
from .verdict import Check, Status

__all__ = [
    "HASH_LENGTH",
    "PRINTED_PREFIX",
    "EgressIdentity",
    "check_egress",
    "egress_hash",
]

#: ``03`` §8: 取前 12 hex.
HASH_LENGTH: Final = 12

#: ``03`` §8: 只打印前 6 位. Six hex digits is enough for a human to compare
#: two runs by eye and far too little to attack the salt with.
PRINTED_PREFIX: Final = 6

#: The metric name used for the ``ingest_gap`` row. One name, so that the
#: health module's later queries and this one agree without a lookup table.
EGRESS_METRIC: Final = "egress"


def egress_hash(salt: str, ip: str) -> str:
    """``SHA256(EGRESS_SALT || ip)``, first 12 hex characters.

    Salt first, address second, no separator, UTF-8 — spelled out because the
    hub's ``.env`` and this function have to agree on the byte string forever,
    and the day they disagree is the day production refuses to start with a
    message that looks exactly like an ISP change.
    """
    digest = hashlib.sha256(f"{salt}{ip}".encode()).hexdigest()
    return digest[:HASH_LENGTH]


@dataclass(frozen=True, slots=True)
class EgressIdentity:
    """The two secrets this check needs, from the machine's ``.env``.

    Never defaulted. A missing salt is "we cannot verify", which prints as
    unknown; inventing a default salt would make every machine agree with
    itself and nothing else.
    """

    salt: str | None
    expected_hash: str | None

    @property
    def configured(self) -> bool:
        return bool(self.salt) and bool(self.expected_hash)


def _short(value: str) -> str:
    return value[:PRINTED_PREFIX]


def check_egress(
    facts: HostFacts, identity: EgressIdentity, *, now_ms: int
) -> tuple[tuple[Check, ...], tuple[IngestGapRow, ...]]:
    """The three assertions, in ``03`` §9 step ④'s own order.

    Returns the checks and any rows they produced. The rows come back rather
    than being written because ``ingest_gap`` does not exist until ``M1-C``
    and because a check that writes as a side effect cannot be run twice in a
    test.
    """
    checks: list[Check] = []
    rows: list[IngestGapRow] = []

    checks.append(_check_default_route(facts))
    checks.append(_check_exit_node(facts))
    hash_check, gap = _check_hash(facts, identity, now_ms=now_ms)
    checks.append(hash_check)
    if gap is not None:
        rows.append(gap)
    return tuple(checks), tuple(rows)


def _check_default_route(facts: HostFacts) -> Check:
    """Assertion 1: the default route is not ``tailscale0``.

    If it were, our requests would leave through the tailnet's exit and be
    metered by the exchanges against an address that appears nowhere in
    ``config/egress-consumers.yaml`` — the ledger would be exactly right about
    a budget we are not spending, and exactly wrong about the one we are.
    """
    interface = facts.default_route_interface
    if interface is None:
        return Check(
            key="egress_default_route",
            title="默认路由不是 tailscale0",
            status=Status.UNKNOWN,
            headline="未检出：读不到默认路由",
            lines=(
                "`ip -4 route show default` 没有输出或不可用；这一条无法判定，不作绿。",
            ),
            provenance=Provenance.of(Tag.MEASURED, qualifiers=(Qualifier.NOT_MEASURED,)),
        )
    if interface == TAILSCALE_INTERFACE:
        return Check(
            key="egress_default_route",
            title="默认路由不是 tailscale0",
            status=Status.RED,
            headline=f"默认路由在 {TAILSCALE_INTERFACE} 上",
            lines=(
                "出网会走 tailnet 出口，交易所按那个地址记账，",
                "而 config/egress-consumers.yaml 描述的是本机出口 ——",
                "账本会对着一份不是我们在花的预算。",
            ),
            provenance=Provenance.of(Tag.MEASURED),
        )
    return Check(
        key="egress_default_route",
        title="默认路由不是 tailscale0",
        status=Status.GREEN,
        headline=f"默认路由在 {interface} 上",
        provenance=Provenance.of(Tag.MEASURED),
    )


def _check_exit_node(facts: HostFacts) -> Check:
    """Assertion 2: no exit node. Same failure as assertion 1, different knob."""
    if facts.tailscale_present is None:
        return Check(
            key="egress_exit_node",
            title="无 exit node",
            status=Status.UNKNOWN,
            headline="未检出：Tailscale 没有应答",
            lines=("`tailscale status --json` 不可用；无法断言没有 exit node。",),
            provenance=Provenance.of(Tag.MEASURED, qualifiers=(Qualifier.NOT_MEASURED,)),
        )
    if facts.tailscale_exit_node:
        return Check(
            key="egress_exit_node",
            title="无 exit node",
            status=Status.RED,
            headline=f"正在使用 exit node（id 前 6 位 {_short(facts.tailscale_exit_node)}）",
            lines=(
                "出网走了别人的出口：限速账本记的是本机出口，实际花的是那台机器的额度。",
            ),
            provenance=Provenance.of(Tag.MEASURED),
        )
    return Check(
        key="egress_exit_node",
        title="无 exit node",
        status=Status.GREEN,
        headline="没有 exit node",
        provenance=Provenance.of(Tag.MEASURED),
    )


def _check_hash(
    facts: HostFacts, identity: EgressIdentity, *, now_ms: int
) -> tuple[Check, IngestGapRow | None]:
    """Assertion 3: the salted egress hash equals ``EXPECTED_EGRESS_HASH``."""
    if not identity.configured:
        missing = [
            name
            for name, value in (("EGRESS_SALT", identity.salt),
                                ("EXPECTED_EGRESS_HASH", identity.expected_hash))
            if not value
        ]
        return (
            Check(
                key="egress_hash",
                title="出口哈希 = EXPECTED_EGRESS_HASH",
                status=Status.UNKNOWN,
                headline=f"未检出：{'、'.join(missing)} 未设置",
                lines=(
                    "两者都在机器的 .env 里，仓库内不存（AGENTS §2.3）。",
                    "没有盐就无法比对，也不能假装比对过了 —— 这一条判「未检出」而不是绿。",
                ),
                provenance=Provenance.of(Tag.MEASURED, qualifiers=(Qualifier.NOT_MEASURED,)),
            ),
            None,
        )
    if not facts.egress_ip:
        return (
            Check(
                key="egress_hash",
                title="出口哈希 = EXPECTED_EGRESS_HASH",
                status=Status.UNKNOWN,
                headline="未检出：没有拿到公网出口地址",
                lines=(
                    "出口地址由 03 §9 两端共用的那条命令取得，经 HLENS_EGRESS_IP 传进来；",
                    "preflight 自己不发请求去问（本步一次网络调用都没有）。",
                ),
                provenance=Provenance.of(Tag.MEASURED, qualifiers=(Qualifier.NOT_MEASURED,)),
            ),
            None,
        )

    assert identity.salt is not None  # narrowed by `configured`
    assert identity.expected_hash is not None
    actual = egress_hash(identity.salt, facts.egress_ip)
    expected = identity.expected_hash.strip().lower()[:HASH_LENGTH]
    if actual == expected:
        return (
            Check(
                key="egress_hash",
                title="出口哈希 = EXPECTED_EGRESS_HASH",
                status=Status.GREEN,
                headline=f"一致（前 {PRINTED_PREFIX} 位 {_short(actual)}）",
                lines=(
                    "生产就在 hub 上，所以「与 hub 相同」是预期状态而不是冲突"
                    "（04 §11 第 11 项作废了旧的判红规则）；",
                    "这一条现在管的是「ISP 有没有换公网 IP」（03 §13）。",
                ),
                provenance=Provenance.of(Tag.MEASURED),
            ),
            None,
        )
    return (
        Check(
            key="egress_hash",
            title="出口哈希 = EXPECTED_EGRESS_HASH",
            status=Status.RED,
            headline=(
                f"不一致（实测前 {PRINTED_PREFIX} 位 {_short(actual)}，"
                f"期望 {_short(expected)}）"
            ),
            lines=(
                "两种可能，都要人看一眼：① ISP 换了公网 IP（03 §13 最后一行，"
                "重算并回填 EXPECTED_EGRESS_HASH 并写 ops_event）；",
                "② EGRESS_SALT 换了一半 —— 03 §9：换盐必须与重算期望值在同一次提交里完成。",
                "已产出 ingest_gap(cause=egress_change)：预留额是对「那个地址」的陈述，"
                "地址变了，扣减表描述的就不再是当前出口。",
            ),
            provenance=Provenance.of(Tag.MEASURED),
        ),
        IngestGapRow(
            # A change of public IP is not one venue's gap: every reservation
            # in `egress-consumers.yaml` was a statement about that address, so
            # both venues are affected at once. §5 defines exactly one value
            # for "this record is about more than one venue" — the contracts'
            # cross-venue marker — and this is the one import preflight is
            # allowed to make (seam ③: every module may import `contracts`).
            venue=Venue.CROSS.value,
            metric=EGRESS_METRIC,
            from_ts=now_ms,
            to_ts=None,
            minutes=None,
            cause=EGRESS_CHANGE,
            ingest_ts=now_ms,
        ),
    )
