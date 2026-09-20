"""Seam ③, proved: preflight uses the ledger's arithmetic without importing it.

This file is type-checked with the production code (``[tool.mypy] files``),
which is the whole point. ``tests/test_adapter_admission.py`` does the same job
for the adapters' ``SpendAuthority``; the reasoning is identical:

* ``isinstance`` against a ``runtime_checkable`` protocol compares member
  *names*. It passes with the wrong types and the wrong signatures, so on its
  own it proves almost nothing.
* A plain **assignment**, checked by mypy in strict mode, proves the shapes
  actually match — parameter types contravariantly, return types covariantly.

So the assignments below are the evidence, and the runtime assertions are a
second, weaker fence for anyone who runs pytest without mypy.

What is being proved, concretely: ``hlens_core.ratelimit`` was not modified by
this task, ``hlens_core.preflight`` imports nothing but its own modules, and
the two still fit together — because the protocols in
:mod:`hlens_core.preflight.budget` were written to match what the ledger
already was.
"""

from __future__ import annotations

from pathlib import Path

from conftest import CONSUMERS_PATH, VENUES_PATH
from hlens_core.adapters.binance.capabilities import BINANCE_CAPABILITIES
from hlens_core.adapters.hyperliquid.capabilities import HYPERLIQUID_CAPABILITIES
from hlens_core.preflight import (
    BucketView,
    BudgetLoader,
    BudgetView,
    CapabilityDeclarationView,
    CapabilitySetView,
    ReservationView,
)
from hlens_core.ratelimit import LedgerConfig

# --------------------------------------------------------------------------- #
# The three assignments. If any of them stops type-checking, two shapes have
# drifted apart and the fix is a protocol change — never a cast.
# --------------------------------------------------------------------------- #
BUDGET_LOADER: BudgetLoader = LedgerConfig.load
"""``LedgerConfig.load`` *is* a ``BudgetLoader``: it accepts ``Path | str``
(wider than the ``Path`` the protocol declares — parameters are checked
contravariantly) and returns a ``LedgerConfig``, which satisfies
:class:`BudgetView`."""

BINANCE_SET: CapabilitySetView = BINANCE_CAPABILITIES
HYPERLIQUID_SET: CapabilitySetView = HYPERLIQUID_CAPABILITIES


def test_ledger_config_is_a_budget_view() -> None:
    config: BudgetView = LedgerConfig.load(VENUES_PATH, CONSUMERS_PATH)
    assert config.venues() == ("binance", "hyperliquid")
    key = config.keys_of("hyperliquid")[0]
    bucket: BucketView = config.bucket(str(key))
    # The numbers themselves are the ledger's; preflight only reads them.
    assert bucket.egress_ceiling_per_min == 1080
    assert bucket.reserved_per_min == 953
    assert bucket.our_ceiling_per_min == 127
    assert bucket.reserved_per_min + bucket.our_ceiling_per_min == (
        bucket.egress_ceiling_per_min
    )


def test_reservations_are_reservation_views() -> None:
    config = LedgerConfig.load(VENUES_PATH, CONSUMERS_PATH)
    reservations: list[ReservationView] = list(
        config.bucket("hyperliquid:info_weight").reservations
    )
    assert {reservation.consumer for reservation in reservations} == {
        "hub_legacy",
        "dev_machine",
    }
    assert all(isinstance(reservation, ReservationView) for reservation in reservations)


def test_capability_sets_are_capability_set_views() -> None:
    for capability_set in (BINANCE_SET, HYPERLIQUID_SET):
        assert isinstance(capability_set, CapabilitySetView)
        declarations: list[CapabilityDeclarationView] = list(capability_set.declarations)
        assert declarations
        for declaration in declarations:
            assert isinstance(declaration, CapabilityDeclarationView)
            # Seam ②: three separate answers, and preflight reads all three.
            assert declaration.supported in {"supported", "unsupported"}


def test_the_budget_loader_signature_takes_two_paths(tmp_path: Path) -> None:
    """Called exactly the way the CLI calls it: two positional ``Path``s."""
    budget = BUDGET_LOADER(VENUES_PATH, CONSUMERS_PATH)
    assert budget.bucket("binance:fapi_weight").official_limit_per_min == 2400
    assert not (tmp_path / "nothing-was-written-here").exists()


def test_preflight_imports_no_other_module() -> None:
    """The AST authority is ``tests/test_module_boundaries.py``; this is the
    one-line version, so that a reviewer reading *this* file can see the claim
    being made rather than having to take it from a docstring."""
    import hlens_core.preflight as preflight_package

    directory = Path(preflight_package.__file__ or "").parent
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.py"))
    )
    for forbidden in ("hlens_core.ratelimit", "hlens_core.adapters", "hlens_collector"):
        offenders = [
            line
            for line in sources.splitlines()
            if forbidden in line and ("import " in line and not line.lstrip().startswith("#"))
        ]
        assert not offenders, offenders
