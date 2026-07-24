"""Calibrate the PaddleOCR pre-gate against the canonical Conduent corpus.

The corpus does not contain an independent labeled-bad scan: the archived
``bad.png`` is pixel-identical to ``WO7U9NJQprod.tiff``. This harness therefore
reports false blocks across golden documents and only calculates bad-scan recall
when explicit ``--known-bad`` filenames are supplied.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "containerapp"))

from evaluation.corpus import ConduentCorpus  # noqa: E402

DEFAULT_MEAN_MIN = float(os.getenv("PADDLE_CONFIDENCE_MEAN_MIN", "0.80"))
DEFAULT_LOW_FRAC_MAX = float(os.getenv("PADDLE_CONFIDENCE_LOW_FRAC_MAX", "0.25"))
DEFAULT_WORD_MIN = float(os.getenv("PADDLE_CONFIDENCE_WORD_MIN", "0.70"))
CU_PAGE_PRICE = float(os.getenv("CONTENT_UNDERSTANDING_PAGE_PRICE_USD", "0.005"))
DEFAULT_DATASETS = "cms1500,commercial-documents,enrollments,unlabeled"


def _norm(name: str) -> str:
    base = Path(name).name.lower()
    for extension in (".tiff", ".tif", ".png", ".jpg", ".jpeg", ".pdf"):
        if base.endswith(extension):
            base = base[: -len(extension)]
            break
    return base.replace(".00000", "")


def assess(client: httpx.Client, url: str, path: Path, word_min: float) -> dict | None:
    try:
        with path.open("rb") as handle:
            files = {"file": (path.name, handle, "application/octet-stream")}
            response = client.post(
                f"{url.rstrip('/')}/assess",
                params={"word_min": word_min},
                files=files,
            )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001 - retain other corpus results when one probe fails
        print(f"  ! assess failed for {path.name}: {exc}", file=sys.stderr)
        return None


def verdict(stats: dict, mean_min: float, low_frac_max: float) -> list[str]:
    reasons: list[str] = []
    if stats["mean"] < mean_min:
        reasons.append(f"low_mean({stats['mean']:.2f}<{mean_min:.2f})")
    if stats["frac_low"] > low_frac_max:
        reasons.append(f"high_frac_low({stats['frac_low']:.2f}>{low_frac_max:.2f})")
    return reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8011")
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=REPO_ROOT / "demo" / "conduent-datasets",
    )
    parser.add_argument("--datasets", default=DEFAULT_DATASETS)
    parser.add_argument("--splits", default="tuning,calibration,development")
    parser.add_argument(
        "--known-bad",
        nargs="*",
        default=[],
        help="Independent filenames that should be blocked. Do not use the duplicate bad.png alias.",
    )
    parser.add_argument("--mean-min", type=float, default=DEFAULT_MEAN_MIN)
    parser.add_argument("--low-frac-max", type=float, default=DEFAULT_LOW_FRAC_MAX)
    parser.add_argument("--word-min", type=float, default=DEFAULT_WORD_MIN)
    parser.add_argument("--cu-page-price", type=float, default=CU_PAGE_PRICE)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    corpus = ConduentCorpus(args.corpus_root)
    datasets = [item.strip() for item in args.datasets.split(",") if item.strip()]
    splits = {item.strip() for item in args.splits.split(",") if item.strip()}
    if "holdout" in splits:
        raise ValueError("Holdout documents are not available to the pre-gate calibration harness")
    cases = corpus.iter_cases(datasets, splits, golden_only=False)
    if not cases:
        print("No corpus documents matched the selected datasets.", file=sys.stderr)
        return 1

    known_bad = {_norm(name) for name in args.known_bad}
    results: list[dict] = []
    print(f"Assessing {len(cases)} samples via {args.url} ...\n")
    print(f"{'file':40s} {'mean':>6s} {'frac_low':>8s} {'lines':>5s}  verdict")
    print("-" * 78)
    with httpx.Client(timeout=180.0) as client:
        for case in cases:
            stats = assess(client, args.url, case.document_path, args.word_min)
            if stats is None:
                continue
            reasons = verdict(stats, args.mean_min, args.low_frac_max)
            blocked = bool(reasons)
            results.append(
                {
                    "document_id": case.document_id,
                    "dataset": case.dataset,
                    "split": case.split,
                    "file": case.original_filename,
                    "mean": stats["mean"],
                    "frac_low": stats["frac_low"],
                    "n_lines": stats["n_lines"],
                    "blocked": blocked,
                    "reasons": reasons,
                    "is_known_bad": _norm(case.original_filename) in known_bad,
                    "is_golden": case.manifest.get("truth_status") == "golden",
                }
            )
            tag = "BLOCK" if blocked else "pass"
            print(
                f"{case.original_filename:40s} {stats['mean']:6.2f} "
                f"{stats['frac_low']:8.2f} {stats['n_lines']:5d}  {tag} {','.join(reasons)}"
            )

    known_bad_rows = [result for result in results if result["is_known_bad"]]
    golden_rows = [result for result in results if result["is_golden"]]
    recall = sum(result["blocked"] for result in known_bad_rows) / len(known_bad_rows) if known_bad_rows else None
    false_positives = sum(result["blocked"] for result in golden_rows if not result["is_known_bad"])
    golden_denominator = sum(not result["is_known_bad"] for result in golden_rows)
    false_positive_rate = false_positives / golden_denominator if golden_denominator else None
    blocked_count = sum(result["blocked"] for result in results)

    summary = {
        "corpus_hash": corpus.corpus_hash(),
        "thresholds": {
            "mean_min": args.mean_min,
            "low_frac_max": args.low_frac_max,
            "word_min": args.word_min,
        },
        "n_samples": len(results),
        "n_blocked": blocked_count,
        "n_passed": len(results) - blocked_count,
        "known_bad": [result["file"] for result in known_bad_rows],
        "recall_on_known_bad": recall,
        "golden_document_count": len(golden_rows),
        "false_blocks_on_golden_documents": false_positives,
        "false_block_rate": false_positive_rate,
        "di_cu_calls_avoided": blocked_count,
        "estimated_usd_saved": round(blocked_count * args.cu_page_price, 4),
        "results": results,
    }

    print("\n" + "=" * 78)
    print(f"Samples assessed     : {summary['n_samples']}")
    print(f"Blocked              : {blocked_count}")
    print(f"Known-bad recall     : {recall if recall is not None else 'not available'}")
    print(f"Golden false blocks  : {false_positives}/{golden_denominator} (rate {false_positive_rate})")

    if args.out:
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nWrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
