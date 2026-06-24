"""PaddleOCR pre-gate experiment harness.

Runs a corpus of page images through the PaddleOCR ``/assess`` endpoint, applies the
pre-gate verdict (the same thresholds the backend uses), and reports how well Paddle
separates genuinely illegible scans from legitimate sparse/structured forms — plus an
estimate of the Document Intelligence / Content Understanding calls avoided and the $
saved.

The PaddleOCR service has internal-only ingress in Azure, so point ``--url`` at a local
container running the *same image* for calibration::

    docker run -d -p 8011:8000 cr53eugbsj5xbfy.azurecr.io/argus/paddleocr-dev:<tag>
    python scripts/paddle_pregate_experiment.py --url http://localhost:8011 \
        --samples demo/cms1500-claims/samples \
        --truth   demo/cms1500-claims/ground-truth \
        --out     files/paddle_pregate_experiment.json

Ground truth for this corpus: ``bad.png`` (== ``WO7U9NJQprod.tiff``) is the known-bad
target; the 22 rows in the truth CSVs are the labeled-good claims (must NOT be blocked).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import httpx

# Default thresholds — mirror the backend env (PADDLE_CONFIDENCE_*).
DEFAULT_MEAN_MIN = float(os.getenv("PADDLE_CONFIDENCE_MEAN_MIN", "0.80"))
DEFAULT_LOW_FRAC_MAX = float(os.getenv("PADDLE_CONFIDENCE_LOW_FRAC_MAX", "0.25"))
DEFAULT_WORD_MIN = float(os.getenv("PADDLE_CONFIDENCE_WORD_MIN", "0.70"))

IMAGE_EXTS = (".png", ".tif", ".tiff", ".jpg", ".jpeg", ".pdf")

# CU page price used by the backend cost model (fallback meter).
CU_PAGE_PRICE = float(os.getenv("CONTENT_UNDERSTANDING_PAGE_PRICE_USD", "0.01"))


def _norm(name: str) -> str:
    """Normalise a filename for case-/extension-insensitive matching."""
    base = os.path.basename(name).lower()
    for ext in (".tiff", ".tif", ".png", ".jpg", ".jpeg"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    return base.replace(".00000", "")


def load_truth_names(truth_dir: str) -> set[str]:
    names: set[str] = set()
    if not truth_dir or not os.path.isdir(truth_dir):
        return names
    for fname in os.listdir(truth_dir):
        if not fname.lower().endswith(".csv"):
            continue
        path = os.path.join(truth_dir, fname)
        with open(path, encoding="latin-1", newline="") as fh:
            reader = csv.DictReader(fh)
            field = next((c for c in (reader.fieldnames or []) if c and "image" in c.lower()), None)
            if not field:
                continue
            for row in reader:
                value = (row.get(field) or "").strip()
                if value:
                    names.add(_norm(value))
    return names


def assess(client: httpx.Client, url: str, path: str, word_min: float) -> dict | None:
    try:
        with open(path, "rb") as fh:
            files = {"file": (os.path.basename(path), fh, "application/octet-stream")}
            resp = client.post(f"{url.rstrip('/')}/assess", params={"word_min": word_min}, files=files)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  ! assess failed for {os.path.basename(path)}: {exc}", file=sys.stderr)
        return None


def verdict(stats: dict, mean_min: float, low_frac_max: float) -> list[str]:
    reasons: list[str] = []
    if stats["mean"] < mean_min:
        reasons.append(f"low_mean({stats['mean']:.2f}<{mean_min:.2f})")
    if stats["frac_low"] > low_frac_max:
        reasons.append(f"high_frac_low({stats['frac_low']:.2f}>{low_frac_max:.2f})")
    return reasons


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://localhost:8011")
    ap.add_argument("--samples", default="demo/cms1500-claims/samples")
    ap.add_argument("--truth", default="demo/cms1500-claims/ground-truth")
    ap.add_argument("--known-bad", nargs="*", default=["bad.png"], help="filenames that SHOULD be blocked")
    ap.add_argument("--mean-min", type=float, default=DEFAULT_MEAN_MIN)
    ap.add_argument("--low-frac-max", type=float, default=DEFAULT_LOW_FRAC_MAX)
    ap.add_argument("--word-min", type=float, default=DEFAULT_WORD_MIN)
    ap.add_argument("--cu-page-price", type=float, default=CU_PAGE_PRICE)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    sample_dir = args.samples
    files = sorted(
        os.path.join(sample_dir, f)
        for f in os.listdir(sample_dir)
        if f.lower().endswith(IMAGE_EXTS)
    )
    if not files:
        print(f"No sample images found in {sample_dir}", file=sys.stderr)
        return 1

    truth_good = load_truth_names(args.truth)
    known_bad = {_norm(b) for b in args.known_bad}

    results: list[dict] = []
    print(f"Assessing {len(files)} samples via {args.url} ...\n")
    print(f"{'file':40s} {'mean':>6s} {'frac_low':>8s} {'lines':>5s}  verdict")
    print("-" * 78)
    with httpx.Client(timeout=180.0) as client:
        for path in files:
            stats = assess(client, args.url, path, args.word_min)
            if stats is None:
                continue
            reasons = verdict(stats, args.mean_min, args.low_frac_max)
            blocked = bool(reasons)
            norm = _norm(path)
            results.append(
                {
                    "file": os.path.basename(path),
                    "mean": stats["mean"],
                    "frac_low": stats["frac_low"],
                    "n_lines": stats["n_lines"],
                    "blocked": blocked,
                    "reasons": reasons,
                    "is_known_bad": norm in known_bad,
                    "is_labeled_good": norm in truth_good,
                }
            )
            tag = "BLOCK" if blocked else "pass"
            print(f"{os.path.basename(path):40s} {stats['mean']:6.2f} {stats['frac_low']:8.2f} "
                  f"{stats['n_lines']:5d}  {tag} {','.join(reasons)}")

    # Metrics.
    known_bad_rows = [r for r in results if r["is_known_bad"]]
    good_rows = [r for r in results if r["is_labeled_good"]]
    recall = (sum(1 for r in known_bad_rows if r["blocked"]) / len(known_bad_rows)) if known_bad_rows else None
    fp = sum(1 for r in good_rows if r["blocked"])
    fp_rate = (fp / len(good_rows)) if good_rows else None
    n_blocked = sum(1 for r in results if r["blocked"])

    summary = {
        "thresholds": {"mean_min": args.mean_min, "low_frac_max": args.low_frac_max, "word_min": args.word_min},
        "n_samples": len(results),
        "n_blocked": n_blocked,
        "n_passed": len(results) - n_blocked,
        "known_bad": [r["file"] for r in known_bad_rows],
        "recall_on_known_bad": recall,
        "labeled_good_count": len(good_rows),
        "false_positives_on_labeled_good": fp,
        "false_positive_rate": fp_rate,
        "di_cu_calls_avoided": n_blocked,
        "est_usd_saved": round(n_blocked * args.cu_page_price, 4),
        "results": results,
    }

    print("\n" + "=" * 78)
    print(f"Samples assessed     : {summary['n_samples']}")
    print(f"Blocked (bad)        : {n_blocked}  -> DI/CU calls avoided, est ${summary['est_usd_saved']:.2f} saved")
    print(f"Recall on known-bad  : {recall}  ({[r['file'] for r in known_bad_rows if r['blocked']]})")
    print(f"False positives      : {fp}/{len(good_rows)} labeled-good claims blocked (rate {fp_rate})")
    if fp:
        print(f"  FP files: {[r['file'] for r in good_rows if r['blocked']]}")

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
