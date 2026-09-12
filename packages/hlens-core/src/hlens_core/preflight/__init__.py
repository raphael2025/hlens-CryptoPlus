"""Collector self-check: who am I, can I reach it, is my clock right, is there data."""

from .egress import OBSERVERS, Observer, normalize_asn, normalize_country, resolve_egress
from .health import CheckKind, EgressIdentity, PreflightReport, SourceHealth, Verdict
from .probes import (
    CoverageProbe,
    probe_clock,
    probe_coverage,
    probe_reachability,
    run_probes,
)
from .runner import run_preflight

__all__ = [
    "OBSERVERS",
    "CheckKind",
    "CoverageProbe",
    "EgressIdentity",
    "Observer",
    "PreflightReport",
    "SourceHealth",
    "Verdict",
    "normalize_asn",
    "normalize_country",
    "probe_clock",
    "probe_coverage",
    "probe_reachability",
    "resolve_egress",
    "run_preflight",
    "run_probes",
]
