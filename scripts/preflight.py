#!/usr/bin/env python3
"""Composition root for the preflight CLI. **The only file that names both.**

Run it as ``uv run python scripts/preflight.py [options]``.

Why this file is here and not inside ``packages/``
--------------------------------------------------
Seam ③ (``03`` §2, enforced by ``tests/test_module_boundaries.py``) lets every
module import ``contracts`` and nothing else. Preflight has to *print* two
things it is not allowed to *import*:

* the shared-egress deduction, which ``hlens_core.ratelimit.config`` already
  computes — and ``03`` §6.1's rule is that there is exactly one implementation
  of that arithmetic, because two copies of it disagree eventually and the
  disagreement arrives as a 429 on an IP we share with someone else;
* the capability declarations, which belong to ``hlens_core.adapters``.

So ``hlens_core.preflight`` declares protocols shaped like what those modules
already are (``BudgetView``, ``CapabilitySetView``), and the concrete objects
are injected here. ``LedgerConfig.load`` satisfies ``BudgetLoader`` as written:
it takes two paths and returns a ``LedgerConfig``, which structurally *is* a
``BudgetView``. Nothing was added to ``ratelimit`` or ``adapters`` for this,
and ``tests/test_preflight_seam.py`` proves the match under mypy rather than by
comment.

A composition root is the standard place for the one line that knows both
sides of a seam — ``adapters/admission.py`` says the same thing about the
collector's wiring — and it lives outside ``packages/`` so that the boundary
test's answer stays "no module imports another module", with no edge added to
``ALLOWED_EDGES`` and no exception carved into it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hlens_core.adapters.binance.capabilities import BINANCE_CAPABILITIES
from hlens_core.adapters.hyperliquid.capabilities import HYPERLIQUID_CAPABILITIES
from hlens_core.preflight import BudgetLoader, CapabilitySetView, main
from hlens_core.ratelimit import LedgerConfig

#: The ledger's loader, used as preflight's budget source with no adapter in
#: between. If this assignment ever stops type-checking, the two shapes have
#: drifted and the fix is a protocol change, not a cast.
BUDGET_LOADER: BudgetLoader = LedgerConfig.load

CAPABILITY_SETS: tuple[CapabilitySetView, ...] = (
    BINANCE_CAPABILITIES,
    HYPERLIQUID_CAPABILITIES,
)


def run(argv: list[str] | None = None) -> int:
    return main(
        argv if argv is not None else sys.argv[1:],
        budget_loader=BUDGET_LOADER,
        capability_sets=CAPABILITY_SETS,
    )


if __name__ == "__main__":
    # Default to the repository's own config directory so the command works
    # from anywhere without a flag; `--config-dir` still overrides it.
    argv = sys.argv[1:]
    if not any(arg.startswith("--config-dir") for arg in argv):
        argv = [*argv, "--config-dir", str(Path(__file__).resolve().parents[1] / "config")]
    raise SystemExit(run(argv))
