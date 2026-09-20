"""Where a printed number came from, and how that survives arithmetic.

AGENTS §2, in one sentence: every constant carries ``official | measured |
unverified | derived``, and ``derived`` **inherits the weakest tag among its
inputs**. This module is that sentence as a type, because preflight is the
first place in the system where the rule has teeth: nearly every figure it
prints — "our Hyperliquid ceiling is 127/min", "this capability is available
here" — is computed from several tagged inputs, and rendering it without its
inheritance would quietly upgrade ``M1-B``'s sample-limited measurement into a
hard number.

``docs/reports/2026-09-20-m1b-hub-inventory.md`` is the concrete case. Its
Hyperliquid figure is ``measured``, and the report tags it ``样本不足`` and
calls it a lower bound: 28 samples over 29 minutes, no high-volatility window.
Our ``1080 - 953 = 127/min`` is arithmetic on that, so it is not merely
``derived`` — it is ``derived`` that may never print harder than ``measured ·
样本不足 · 下界``. :meth:`Provenance.derive` is what makes "may never" a
property of the code rather than of whoever writes the next table.

Qualifiers, unlike tags, are not ordered: they are a set, and a derivation
carries the **union** of its inputs'. A number derived from one sample-limited
input and one exact input is still sample-limited; there is no averaging here
either.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "Provenance",
    "Qualifier",
    "Tag",
    "measured",
    "official",
    "unverified",
]


class Tag(StrEnum):
    """AGENTS §2's four source tags."""

    OFFICIAL = "official"
    MEASURED = "measured"
    UNVERIFIED = "unverified"
    DERIVED = "derived"


#: Strength order for the three tags that describe an *origin*. ``derived`` is
#: not on this scale on purpose: it describes how a number was produced, not
#: how well it is known, and its strength is exactly its weakest input's.
_STRENGTH: Final[dict[Tag, int]] = {
    Tag.OFFICIAL: 3,
    Tag.MEASURED: 2,
    Tag.UNVERIFIED: 1,
}


class Qualifier(StrEnum):
    """A caveat that travels with a measurement and never falls off."""

    SAMPLE_LIMITED = "sample_limited"
    """``01`` §4.3's 样本不足. M1-B's Hyperliquid p95: 28 samples, one window,
    no high-volatility period."""

    LOWER_BOUND = "lower_bound"
    """The true value is at least this. M1-B's own words for the same figure —
    and the reason a ledger that balances against it is not proof of anything
    about the egress."""

    NOT_MEASURED = "not_measured"
    """Carried by a verdict computed from an input nobody has measured yet, so
    that an ``unverified`` placeholder cannot be laundered into a conclusion
    by passing through two additions."""


#: How each qualifier prints, in the documents' own vocabulary.
_QUALIFIER_TEXT: Final[dict[Qualifier, str]] = {
    Qualifier.SAMPLE_LIMITED: "样本不足",
    Qualifier.LOWER_BOUND: "下界",
    Qualifier.NOT_MEASURED: "未实测",
}

#: How each tag prints.
_TAG_TEXT: Final[dict[Tag, str]] = {
    Tag.OFFICIAL: "官方",
    Tag.MEASURED: "实测",
    Tag.UNVERIFIED: "未验证",
    Tag.DERIVED: "派生",
}


@dataclass(frozen=True, slots=True)
class Provenance:
    """One figure's origin: a tag, its caveats, and what it was derived from.

    ``tag`` is what the figure *is*; ``effective`` is how hard it may be
    presented as. For an origin they are equal. For a derivation ``tag`` is
    ``derived`` and ``effective`` is the weakest input's — the display rule
    AGENTS §2 asks for, kept as a separate field so that "this is a computed
    number" and "this is only as good as a sample-limited measurement" remain
    two different facts.
    """

    tag: Tag
    effective: Tag
    qualifiers: frozenset[Qualifier] = frozenset()
    inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.tag is Tag.DERIVED:
            if self.effective is Tag.DERIVED:
                raise ValueError(
                    "a derived figure must name the weakest tag it inherits; "
                    "'derived' is not itself a strength (AGENTS §2)"
                )
            return
        if self.effective is not self.tag:
            raise ValueError("an origin's effective tag is its own tag")

    @classmethod
    def of(
        cls,
        tag: Tag,
        *,
        qualifiers: Iterable[Qualifier] = (),
        source: str | None = None,
    ) -> Provenance:
        """A figure that was read somewhere, not computed."""
        if tag is Tag.DERIVED:
            raise ValueError("use Provenance.derive() for a computed figure")
        return cls(
            tag=tag,
            effective=tag,
            qualifiers=frozenset(qualifiers),
            inputs=(source,) if source else (),
        )

    @classmethod
    def derive(cls, *parts: Provenance, note: str | None = None) -> Provenance:
        """A figure computed from other tagged figures.

        The weakest input decides, and every input's qualifiers come along.
        Deriving from nothing is refused: a number with no inputs was not
        derived, it was chosen, and ``unverified`` is the honest tag for that.
        """
        if not parts:
            raise ValueError(
                "a derivation needs inputs; a figure with none is 'unverified', not 'derived'"
            )
        effective = min((part.effective for part in parts), key=lambda tag: _STRENGTH[tag])
        qualifiers: set[Qualifier] = set()
        inputs: list[str] = []
        for part in parts:
            qualifiers |= part.qualifiers
            inputs.extend(part.inputs)
        if note:
            inputs.append(note)
        return cls(
            tag=Tag.DERIVED,
            effective=effective,
            qualifiers=frozenset(qualifiers),
            inputs=tuple(dict.fromkeys(inputs)),
        )

    @property
    def is_hard(self) -> bool:
        """``True`` only for an official figure with no caveat on it."""
        return self.effective is Tag.OFFICIAL and not self.qualifiers

    def render(self) -> str:
        """``派生(实测)·样本不足·下界`` — the whole inheritance on one line."""
        head = _TAG_TEXT[self.tag]
        if self.tag is Tag.DERIVED:
            head = f"{head}({_TAG_TEXT[self.effective]})"
        parts = [head]
        parts.extend(
            _QUALIFIER_TEXT[qualifier]
            for qualifier in sorted(self.qualifiers, key=lambda q: q.value)
        )
        return "·".join(parts)


def official(source: str | None = None) -> Provenance:
    return Provenance.of(Tag.OFFICIAL, source=source)


def measured(
    source: str | None = None, *, qualifiers: Iterable[Qualifier] = ()
) -> Provenance:
    return Provenance.of(Tag.MEASURED, qualifiers=qualifiers, source=source)


def unverified(source: str | None = None) -> Provenance:
    return Provenance.of(
        Tag.UNVERIFIED, qualifiers=(Qualifier.NOT_MEASURED,), source=source
    )
