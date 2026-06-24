from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTAINERAPP_PATH = REPO_ROOT / "src" / "containerapp"
sys.path.insert(0, str(CONTAINERAPP_PATH))

from profiling import DEFAULT_DATASET, DEFAULT_TIERS, run_cost_profile  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ARGUS cost profiling across extraction tiers.")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="Dataset under demo\\ to profile.")
    parser.add_argument(
        "--files",
        default="",
        help="Comma-separated demo file names. Defaults to all supported documents in the dataset.",
    )
    parser.add_argument(
        "--tiers",
        default=",".join(DEFAULT_TIERS),
        help="Comma-separated tiers to profile, for example economy,standard,premium.",
    )
    parser.add_argument("--no-persist", action="store_true", help="Return the report without writing it to Cosmos.")
    return parser.parse_args()


def print_summary_table(report: dict[str, Any]) -> None:
    print("\nTier summary:")
    print(f"{'tier':<10} {'avg_usd_page':>14} {'avg_tokens':>12} {'success_rate':>13}")
    for tier, metrics in report.get("per_tier", {}).items():
        print(
            f"{tier:<10} "
            f"{metrics.get('avg_usd_per_page', 0):>14.6f} "
            f"{metrics.get('avg_tokens', 0):>12.2f} "
            f"{metrics.get('success_rate', 0):>13.2%}"
        )


def main() -> int:
    args = parse_args()
    files = [value.strip() for value in args.files.split(",") if value.strip()] or None
    tiers = [value.strip() for value in args.tiers.split(",") if value.strip()]
    report = run_cost_profile(args.dataset, files, tiers, persist=not args.no_persist)
    print(json.dumps(report, indent=2, default=str))
    print_summary_table(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
