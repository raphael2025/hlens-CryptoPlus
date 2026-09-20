"""上机自检 CLI —— the eight checks of ``03`` §16 步骤 ③.

``03`` §4 gives this module one job: answer, before anything else starts,
whether this machine is the machine we think it is and whether it may spend
what it is about to spend. The eight items, and where each lives:

============================  ========================================
出口哈希比对                  :mod:`~hlens_core.preflight.egress`
三条 Tailscale 断言           :mod:`~hlens_core.preflight.egress`
共享出口的预算扣减表          :mod:`~hlens_core.preflight.budget`
区域判定与能力矩阵            :mod:`~hlens_core.preflight.region`
时钟                          :mod:`~hlens_core.preflight.host`
磁盘 ``D_free``               :mod:`~hlens_core.preflight.host`
PG >= 18.6                    :mod:`~hlens_core.preflight.host`
两所可达 + 覆盖矩阵           :mod:`~hlens_core.preflight.reachability` · ``region``
============================  ========================================

Three properties of the whole command, each of which is a decision
-------------------------------------------------------------------

**It runs without a database, and says so.** ``source_health`` and
``ops_event`` are ``M1-C``'s tables and do not exist yet. Preflight builds its
rows as values and prints them (:mod:`~hlens_core.preflight.rows}`); writing
them is an optional sink somebody else supplies. The first run on a new
machine happens before ``docker compose up``, and the run that matters most is
the one where the database will not start.

**It sends nothing unless asked twice.** This development machine shares one
public egress IP with the production host, and that egress already carries a
collector that is not ours (``03`` §8, ``M1-B``). The venue probe is fully
implemented and runs only behind ``--live-probe``, which prints its exact
weight cost first. Every test drives it through ``respx`` against hand-built
fixtures tagged ``source: documented`` — ``M1-G`` records the real ones, from
the production host's egress, as AGENTS §3.3 requires.

**Red is red.** There is no development mode, no threshold that widens when
the machine is inconvenient, and no ``--skip``. ``03`` §8 already says this
machine cannot make ``M1-F``'s preflight all green — its clock is 1.35 s out
against a 1 s line — so this CLI prints that as a failure and exits non-zero.
``03`` §8 硬规则 ③ does allow dev to downgrade the clock to a yellow skip; that
downgrade is deliberately not implemented and is raised in the PR's "Doc
corrections", because a self-check with a documented way to pass is a
decoration, and the exit code is the only part of this command another program
reads.

Seam ③ (two protocols, no imports)
----------------------------------
``tests/test_module_boundaries.py`` allows ``preflight -> contracts`` and
nothing else, while the two things preflight has to print belong to
``ratelimit`` (the deduction) and ``adapters`` (the capability declarations).
Both cross the seam by dependency inversion, the pattern ``M1-A4`` established:
this package declares protocols matching what those modules already are
(:class:`~hlens_core.preflight.budget.BudgetView`,
:class:`~hlens_core.preflight.region.CapabilitySetView`), neither module
changes, and the single line that names both sides lives in the composition
root outside ``packages/`` — ``scripts/preflight.py``.
"""

from __future__ import annotations

from .budget import (
    BucketView,
    BudgetLoader,
    BudgetView,
    ConsumerObservation,
    ReservationView,
    check_budget,
    read_observations,
)
from .cli import build_parser, main, run_preflight
from .egress import EgressIdentity, check_egress, egress_hash
from .facts import HostFacts
from .host import (
    CLOCK_THRESHOLD_S,
    MIN_SERVER_VERSION_NUM,
    STAGE_MIN_BYTES,
    check_clock,
    check_disk,
    check_machine,
    check_postgres,
)
from .provenance import Provenance, Qualifier, Tag
from .reachability import (
    EndpointFamily,
    HttpReachabilityProbe,
    ProbeOutcome,
    ReachabilityProbe,
    probe_plan,
)
from .region import (
    M1_REQUIRED_CAPABILITIES,
    CapabilityDeclarationView,
    CapabilitySetView,
    check_capability_matrix,
    check_region,
)
from .report import render_report, status_json
from .rows import HealthSink, IngestGapRow, OpsEventRow, SourceHealthRow
from .verdict import Check, ExitCode, Report, Status, worst

__all__ = [
    "CLOCK_THRESHOLD_S",
    "M1_REQUIRED_CAPABILITIES",
    "MIN_SERVER_VERSION_NUM",
    "STAGE_MIN_BYTES",
    "BucketView",
    "BudgetLoader",
    "BudgetView",
    "CapabilityDeclarationView",
    "CapabilitySetView",
    "Check",
    "ConsumerObservation",
    "EgressIdentity",
    "EndpointFamily",
    "ExitCode",
    "HealthSink",
    "HostFacts",
    "HttpReachabilityProbe",
    "IngestGapRow",
    "OpsEventRow",
    "ProbeOutcome",
    "Provenance",
    "Qualifier",
    "ReachabilityProbe",
    "Report",
    "ReservationView",
    "SourceHealthRow",
    "Status",
    "Tag",
    "build_parser",
    "check_budget",
    "check_capability_matrix",
    "check_clock",
    "check_disk",
    "check_egress",
    "check_machine",
    "check_postgres",
    "check_region",
    "egress_hash",
    "main",
    "probe_plan",
    "read_observations",
    "render_report",
    "run_preflight",
    "status_json",
    "worst",
]
