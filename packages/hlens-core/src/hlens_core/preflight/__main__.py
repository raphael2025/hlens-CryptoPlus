"""``python -m hlens_core.preflight --venues binance`` -- table, then JSON."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .runner import run_preflight


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hlens-preflight", description=__doc__)
    ap.add_argument(
        "--venues",
        default="",
        help="comma-separated venue names from config/venues.yaml (default: all)",
    )
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args(argv)

    venues = [v.strip() for v in args.venues.split(",") if v.strip()] or None
    report = asyncio.run(run_preflight(venues, timeout_s=args.timeout))
    if not args.json_only:
        print(report.to_table())
        print()
    print(json.dumps(report.model_dump(mode="json"), indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
