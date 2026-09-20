"""AGENTS §2's inheritance rule, exercised on the numbers it was written for.

"``derived`` 继承输入里最弱的标签" is one sentence in a document and a whole
class of quiet dishonesty in a report: ``1080 − 953 = 127`` is exact
arithmetic, and printing the 127 as a hard figure would present M1-B's
sample-limited lower bound as a measurement of the future.
"""

from __future__ import annotations

import pytest

from hlens_core.preflight import Provenance, Qualifier, Tag
from hlens_core.preflight.provenance import measured, official, unverified


def test_an_origin_is_as_strong_as_itself() -> None:
    assert official("04 §2").effective is Tag.OFFICIAL
    assert measured("M1-B").effective is Tag.MEASURED
    assert unverified().effective is Tag.UNVERIFIED


def test_derive_takes_the_weakest_tag() -> None:
    weak = Provenance.derive(official("1200 是官方"), measured("953 是实测"))
    assert weak.tag is Tag.DERIVED
    assert weak.effective is Tag.MEASURED
    assert not weak.is_hard

    weaker = Provenance.derive(weak, unverified("旧采集器的退役时间未知"))
    assert weaker.effective is Tag.UNVERIFIED


def test_qualifiers_are_a_union_and_never_drop_off() -> None:
    """The M1-B case, end to end: our 127/min may not print harder than 953."""
    ceiling = official("04 开头硬规则 ②：1200 x 90 % = 1080")
    reservation = measured(
        "M1-B §A：28 样本 / 29 分钟",
        qualifiers=(Qualifier.SAMPLE_LIMITED, Qualifier.LOWER_BOUND),
    )
    ours = Provenance.derive(ceiling, reservation)
    assert ours.effective is Tag.MEASURED
    assert ours.qualifiers == {Qualifier.SAMPLE_LIMITED, Qualifier.LOWER_BOUND}
    assert ours.render() == "派生(实测)·下界·样本不足"

    # And one more step of arithmetic does not launder it either.
    opportunistic = Provenance.derive(ours, official("03 §6.1 的硬顶 20"))
    assert opportunistic.effective is Tag.MEASURED
    assert Qualifier.SAMPLE_LIMITED in opportunistic.qualifiers


def test_a_figure_with_no_inputs_is_not_derived() -> None:
    with pytest.raises(ValueError, match="unverified"):
        Provenance.derive()


def test_derived_is_not_a_strength() -> None:
    """``derived`` says how a number was made, never how well it is known."""
    with pytest.raises(ValueError, match="weakest tag"):
        Provenance(tag=Tag.DERIVED, effective=Tag.DERIVED)
    with pytest.raises(ValueError, match="derive"):
        Provenance.of(Tag.DERIVED)
