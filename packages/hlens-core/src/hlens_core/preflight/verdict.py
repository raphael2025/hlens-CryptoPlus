"""What a check answers, and what the process does about it.

Four verdicts, not three. ``unknown`` exists because the most common way for a
self-check to lie is to report green on something it never measured: the
PostgreSQL assertion with no database in reach, the region matrix with no probe
run, the disk line on a host whose mount was never read. ``03`` §8's list of
"本机做不了的" is a list of unknowns, and every one of them has to print as an
unknown rather than as a pass.

The exit code is the point of the CLI (``03`` §9 step ④: 全过才继续), so it is
a function of the worst verdict and of nothing else. There is no development
mode that turns a red into a pass; see this package's ``__init__``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Final

from .provenance import Provenance
from .textwidth import pad

__all__ = [
    "Check",
    "ExitCode",
    "Report",
    "Status",
    "worst",
]


class Status(StrEnum):
    """One check's verdict.

    ``YELLOW`` is for "measured, inside the letter of the rule, and still not
    safe" — §6.1's Hyperliquid row is the case that forced it to exist, where
    the ledger's arithmetic balances and the observed egress does not.
    ``UNKNOWN`` is for "not measured here", and is never rounded to green.
    """

    GREEN = "green"
    YELLOW = "yellow"
    UNKNOWN = "unknown"
    RED = "red"


#: Severity order. ``UNKNOWN`` outranks ``YELLOW``: a warning is a fact we
#: checked, an unknown is a fact we did not, and the second is the worse thing
#: to start a collector on (``04`` §6: 三件事没做完之前 live 预算按 0 处理).
_SEVERITY: Final[dict[Status, int]] = {
    Status.GREEN: 0,
    Status.YELLOW: 1,
    Status.UNKNOWN: 2,
    Status.RED: 3,
}

#: What each verdict prints as. ASCII only: this table is read over SSH at
#: 03:00 and inside a status page's ``<pre>``, and a terminal that cannot draw
#: a box character must not be able to change the meaning.
_MARK: Final[dict[Status, str]] = {
    Status.GREEN: "[ OK ]",
    Status.YELLOW: "[WARN]",
    Status.UNKNOWN: "[ ?? ]",
    Status.RED: "[FAIL]",
}


def worst(statuses: Iterable[Status]) -> Status:
    """The verdict of a set of checks: the worst one, with no averaging.

    An empty set is ``UNKNOWN``, not ``GREEN`` — a preflight that ran nothing
    has cleared nothing.
    """
    ordered = sorted(statuses, key=lambda status: _SEVERITY[status], reverse=True)
    return ordered[0] if ordered else Status.UNKNOWN


class ExitCode(IntEnum):
    """What the shell sees.

    Three codes, because the operator's next action differs:

    * ``0`` — every check green; the deployment step may continue.
    * ``1`` — nothing contradicted, but something was not measured or carries a
      warning. **Not cleared.** This is what a development machine gets when
      the only gaps are the ones ``03`` §8 says it cannot close.
    * ``2`` — a check failed. Refuse to start.
    """

    CLEARED = 0
    NOT_CLEARED = 1
    REFUSED = 2

    @classmethod
    def of(cls, status: Status) -> ExitCode:
        if status is Status.GREEN:
            return cls.CLEARED
        if status is Status.RED:
            return cls.REFUSED
        return cls.NOT_CLEARED


@dataclass(frozen=True, slots=True)
class Check:
    """One line of the summary table, plus the evidence printed under it.

    ``headline`` is the sentence, ``lines`` is the evidence, and both are
    always rendered: ``03`` §6.1 asks for "扣减过程与结果", and a check that
    prints only its verdict cannot be reviewed.
    """

    key: str
    """Stable ``snake_case`` identifier. It is the ``source_health.capability``
    value and the ``status.json`` key, so it does not change when the title
    does."""

    title: str
    status: Status
    headline: str
    lines: tuple[str, ...] = ()
    provenance: Provenance | None = None
    """Where this verdict's inputs came from. Present on every check whose
    answer is computed from tagged constants or measurements (AGENTS §2: a
    derived figure inherits the weakest tag among its inputs)."""

    def summary_line(self) -> str:
        tag = f"  ({self.provenance.render()})" if self.provenance is not None else ""
        return f"{_MARK[self.status]} {pad(self.title, 30)} {self.headline}{tag}"

    def render(self) -> list[str]:
        out = [self.summary_line()]
        out.extend(f"       {line}" for line in self.lines)
        return out


@dataclass(frozen=True, slots=True)
class Report:
    """Every check, plus the rows a database would have received.

    The rows travel *with* the report instead of being written as a side
    effect: ``source_health`` and ``ops_event`` do not exist until ``M1-C``,
    and a preflight that cannot run without its own output tables would be
    useless on the day it matters most — the first boot of a new machine.
    """

    checks: tuple[Check, ...]
    rows: tuple[object, ...] = field(default_factory=tuple)

    @property
    def status(self) -> Status:
        return worst(check.status for check in self.checks)

    @property
    def exit_code(self) -> ExitCode:
        return ExitCode.of(self.status)

    def by_key(self, key: str) -> Check:
        for check in self.checks:
            if check.key == key:
                return check
        raise KeyError(key)

    def of_status(self, status: Status) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status is status)

    def summary(self) -> Sequence[str]:
        return [check.summary_line() for check in self.checks]
