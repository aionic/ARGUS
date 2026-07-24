"""CMS-1500 claims extraction scoring harness.

Runs the canonical Conduent CMS-1500 samples in ``demo/conduent-datasets`` through
the ARGUS pipeline and scores the extracted ``od_*`` fields against the
ground-truth CSVs, with normalized (format-insensitive) comparison.

Modes
-----
* ``--mode local``  Run each sample in-process through ``blob_processing.process_blob``
  for every requested tier (economy vs premium), pulling extracted fields, cost,
  and the preflight ``flag`` straight off the returned document. Requires Azure
  access (Content Understanding + Cosmos + OCR) and is the path used for the P4
  end-to-end run.
* ``--mode api``    Upload each sample to a running ARGUS deployment, poll until
  processing completes, fetch the document, and score. Tier comes from the
  dataset's processing_options. Requires ``--base-url`` and ``--api-key``.
* ``--mode score``  Score a directory of already-saved extraction JSON files
  (one ``<ImageName>.json`` per document) against the truth CSVs. No Azure needed.
* ``--self-test``   Validate the normalizer + scorer on synthetic data and exit.

Samples and truth are loaded from the canonical CMS manifest and normalized truth
JSONL. The archived source CSVs are retained only for provenance.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTAINERAPP_ROOT = REPO_ROOT / "src" / "containerapp"
sys.path.insert(0, str(CONTAINERAPP_ROOT))

from evaluation.corpus import ConduentCorpus  # noqa: E402

DATASET = "cms1500-claims"
CORPUS_DATASET = "cms1500"
CORPUS_ROOT = REPO_ROOT / "demo" / "conduent-datasets"
# The archived bad.png is pixel-identical to a commercial sample, so it is not
# treated as an independent labeled negative.
KNOWN_BAD_SAMPLES: set[str] = set()

_PUNCT_RE = re.compile(r"[\s.,/\\:#()\-_$]+")


# --------------------------------------------------------------------------- #
# Pure scoring core (no Azure dependencies)
# --------------------------------------------------------------------------- #
def normalize_value(value: Any) -> str:
    """Normalize a field value for format-insensitive comparison.

    Upper-cases, strips, and removes whitespace/punctuation so that
    ``"11/05/21"`` == ``"11 05 21"`` == ``"110521"`` and ``"LAST, FIRST"`` ==
    ``"LAST FIRST"``. Empty / None collapse to ``""``.
    """
    if value is None:
        return ""
    text = str(value).strip().upper()
    if text in {"NONE", "NULL", "N/A"}:
        return ""
    return _PUNCT_RE.sub("", text)


def image_key(name: str) -> str:
    """Case-insensitive match key for a sample/truth image name (full filename)."""
    return name.strip().upper()


def load_truth(
    corpus_root: Path = CORPUS_ROOT,
    splits: set[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Load canonical CMS truth keyed by case-insensitive original filename."""
    corpus = ConduentCorpus(corpus_root)
    return {
        image_key(case.original_filename): case.truth or {}
        for case in corpus.iter_cases([CORPUS_DATASET], splits or {"tuning", "calibration"})
    }


def flatten_extraction(extracted: Any) -> dict[str, str]:
    """Flatten an extraction result to a flat ``od_*`` -> str map.

    Handles (a) a flat dict of od_* fields, (b) the multi-page
    ``{"pages_1-N": {...}}`` structure ARGUS builds for chunked docs (first page
    range wins for single-page claims), and (c) a wrapping ``gpt_extraction_output``.
    """
    if isinstance(extracted, str):
        try:
            extracted = json.loads(extracted)
        except (TypeError, ValueError):
            return {}
    if not isinstance(extracted, dict):
        return {}
    if "gpt_extraction_output" in extracted and isinstance(extracted["gpt_extraction_output"], (dict, str)):
        return flatten_extraction(extracted["gpt_extraction_output"])

    # If keys look like od_* already, treat as flat.
    od_keys = [k for k in extracted if str(k).startswith("od_")]
    if od_keys:
        return {k: extracted[k] for k in od_keys}

    # Otherwise descend into the first nested dict (page-range structure).
    for value in extracted.values():
        if isinstance(value, dict):
            nested = flatten_extraction(value)
            if nested:
                return nested
    return {}


def score_document(truth_row: dict[str, str], extracted_flat: dict[str, str]) -> dict[str, Any]:
    """Score one document's extracted fields against its truth row.

    Accuracy is measured over truth fields that have a non-empty value (the
    information we actually need to recover). Also reports how many empty truth
    fields were correctly left empty (specificity) and per-field correctness.
    """
    per_field: dict[str, bool] = {}
    nonempty_total = 0
    nonempty_correct = 0
    empty_total = 0
    empty_correct = 0

    for field, truth_val in truth_row.items():
        t_norm = normalize_value(truth_val)
        e_norm = normalize_value(extracted_flat.get(field))
        match = t_norm == e_norm
        per_field[field] = match
        if t_norm:
            nonempty_total += 1
            nonempty_correct += int(match)
        else:
            empty_total += 1
            empty_correct += int(match)

    return {
        "nonempty_total": nonempty_total,
        "nonempty_correct": nonempty_correct,
        "nonempty_accuracy": (nonempty_correct / nonempty_total) if nonempty_total else None,
        "empty_total": empty_total,
        "empty_correct": empty_correct,
        "empty_accuracy": (empty_correct / empty_total) if empty_total else None,
        "per_field": per_field,
    }


def aggregate(doc_scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-document scores into corpus + per-field accuracy."""
    total_nonempty = sum(s["nonempty_total"] for s in doc_scores.values())
    correct_nonempty = sum(s["nonempty_correct"] for s in doc_scores.values())
    per_field_correct: dict[str, int] = {}
    per_field_seen: dict[str, int] = {}
    for score in doc_scores.values():
        for field, ok in score["per_field"].items():
            per_field_seen[field] = per_field_seen.get(field, 0) + 1
            per_field_correct[field] = per_field_correct.get(field, 0) + int(ok)

    per_field_accuracy = {
        field: per_field_correct[field] / per_field_seen[field]
        for field in sorted(per_field_seen)
        if per_field_seen[field]
    }
    worst = sorted(per_field_accuracy.items(), key=lambda kv: kv[1])[:15]
    return {
        "documents_scored": len(doc_scores),
        "field_accuracy_micro": (correct_nonempty / total_nonempty) if total_nonempty else None,
        "nonempty_fields_total": total_nonempty,
        "nonempty_fields_correct": correct_nonempty,
        "per_doc_accuracy": {name: score["nonempty_accuracy"] for name, score in sorted(doc_scores.items())},
        "worst_fields": worst,
    }


def preflight_report(flags: dict[str, dict | None]) -> dict[str, Any]:
    """Summarize flags without inventing a negative label for the duplicate bad.png."""
    flagged = {name for name, flag in flags.items() if flag and flag.get("flagged")}
    known_bad = {name for name in flags if image_key(name) in KNOWN_BAD_SAMPLES}
    known_good = set(flags) - known_bad
    true_pos = len(flagged & known_bad)
    false_neg = len(known_bad - flagged)
    false_pos = len(flagged & known_good)
    true_neg = len(known_good - flagged)
    recall = true_pos / len(known_bad) if known_bad else None
    precision = true_pos / len(flagged) if flagged else None
    return {
        "flagged": sorted(flagged),
        "known_bad": sorted(known_bad),
        "true_positives": true_pos,
        "false_negatives": false_neg,
        "false_positives": false_pos,
        "true_negatives": true_neg,
        "recall_on_known_bad": recall,
        "precision": precision,
        "false_positive_rate_on_good": (false_pos / len(known_good)) if known_good else None,
    }


# --------------------------------------------------------------------------- #
# Runners
# --------------------------------------------------------------------------- #
def _sample_paths(splits: set[str] | None = None) -> list[Path]:
    corpus = ConduentCorpus(CORPUS_ROOT)
    return [
        case.document_path
        for case in corpus.iter_cases(
            [CORPUS_DATASET],
            splits or {"tuning", "calibration"},
        )
    ]


def run_local(tiers: list[str], limit: int | None) -> dict[str, Any]:
    """Run samples in-process per tier through process_blob and score them."""
    sys.path.insert(0, str(REPO_ROOT / "src" / "containerapp"))
    import blob_processing  # noqa: E402
    from profiling import LocalBlobInputStream, _resolve_data_container, _tier_override  # noqa: E402

    truth = load_truth()
    container = _resolve_data_container(None)
    samples = _sample_paths()
    if limit:
        samples = samples[:limit]

    report: dict[str, Any] = {"dataset": DATASET, "per_tier": {}}
    for tier in tiers:
        doc_scores: dict[str, dict[str, Any]] = {}
        flags: dict[str, dict | None] = {}
        costs: list[float] = []
        for path in samples:
            blob_name = f"{DATASET}/scoring/{tier}/{path.name}"
            blob = LocalBlobInputStream(blob_name, path.read_bytes())
            try:
                with _tier_override(tier):
                    document = blob_processing.process_blob(blob, container)
            except Exception as exc:  # noqa: BLE001 - isolate each document
                print(f"  [{tier}] {path.name}: FAILED {type(exc).__name__}: {exc}")
                flags[path.name] = {"flagged": True, "reasons": [f"processing_error:{type(exc).__name__}"]}
                continue
            properties = document.get("properties") or {}
            flags[path.name] = properties.get("flag")
            cost = (properties.get("cost") or {}).get("usd_per_page")
            if isinstance(cost, (int, float)):
                costs.append(float(cost))
            extracted = flatten_extraction((document.get("extracted_data") or {}).get("gpt_extraction_output"))
            truth_row = truth.get(image_key(path.name))
            if truth_row:
                doc_scores[path.name] = score_document(truth_row, extracted)
        report["per_tier"][tier] = {
            "extraction": aggregate(doc_scores) if doc_scores else {"documents_scored": 0},
            "preflight": preflight_report(flags),
            "avg_usd_per_page": (sum(costs) / len(costs)) if costs else None,
        }
    return report


def run_api(base_url: str, api_key: str, tier_label: str, limit: int | None, poll_timeout: int) -> dict[str, Any]:
    """Upload samples to a live ARGUS deployment, poll, fetch, and score."""
    import requests  # noqa: E402 - only needed for api mode

    truth = load_truth()
    samples = _sample_paths()
    if limit:
        samples = samples[:limit]
    headers = {"X-API-Key": api_key}
    base_url = base_url.rstrip("/")

    doc_scores: dict[str, dict[str, Any]] = {}
    flags: dict[str, dict | None] = {}
    costs: list[float] = []
    for path in samples:
        with path.open("rb") as handle:
            resp = requests.post(
                f"{base_url}/api/datasets/{DATASET}/upload",
                headers=headers,
                files={"file": (path.name, handle)},
                params={"run_summary": "false", "run_evaluation": "false"},
                timeout=120,
            )
        resp.raise_for_status()
        document_id = resp.json()["document_id"]
        document = _poll_document(requests, base_url, headers, document_id, poll_timeout)
        if document is None:
            print(f"  {path.name}: TIMED OUT after {poll_timeout}s")
            continue
        properties = document.get("properties") or {}
        flags[path.name] = properties.get("flag")
        cost = (properties.get("cost") or {}).get("usd_per_page")
        if isinstance(cost, (int, float)):
            costs.append(float(cost))
        extracted = flatten_extraction((document.get("extracted_data") or {}).get("gpt_extraction_output"))
        truth_row = truth.get(image_key(path.name))
        scored = score_document(truth_row, extracted) if truth_row else None
        if scored:
            doc_scores[path.name] = scored
        acc = scored["nonempty_accuracy"] if scored else None
        flagged = bool(flags[path.name] and flags[path.name].get("flagged"))
        print(f"  {path.name}: flagged={flagged} accuracy={acc} usd/page={cost}")

    return {
        "dataset": DATASET,
        "per_tier": {
            tier_label: {
                "extraction": aggregate(doc_scores) if doc_scores else {"documents_scored": 0},
                "preflight": preflight_report(flags),
                "avg_usd_per_page": (sum(costs) / len(costs)) if costs else None,
            }
        },
    }


def _poll_document(requests_mod, base_url: str, headers: dict, document_id: str, timeout: int) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests_mod.get(f"{base_url}/api/documents/{document_id}", headers=headers, timeout=60)
        if resp.status_code == 200:
            document = resp.json()
            state = document.get("state") or {}
            # Wait for the terminal state so extraction (and the ocr_confidence
            # preflight) are fully populated. ocr_completed fires too early on the
            # GPT path (before extraction), which previously yielded empty results.
            if state.get("processing_completed") is not None:
                return document
            if document.get("errors"):
                return document
        time.sleep(5)
    return None


def run_score_dir(results_dir: Path) -> dict[str, Any]:
    """Score a directory of saved extraction JSON files against the truth CSVs."""
    truth = load_truth()
    doc_scores: dict[str, dict[str, Any]] = {}
    for path in sorted(results_dir.glob("*.json")):
        extracted = flatten_extraction(json.loads(path.read_text(encoding="utf-8")))
        # Result filename stem maps to the sample/truth image name.
        truth_row = truth.get(image_key(path.stem)) or truth.get(image_key(path.stem + ".tif"))
        if truth_row:
            doc_scores[path.name] = score_document(truth_row, extracted)
    return {"dataset": DATASET, "extraction": aggregate(doc_scores) if doc_scores else {"documents_scored": 0}}


# --------------------------------------------------------------------------- #
# Self-test (no Azure)
# --------------------------------------------------------------------------- #
def self_test() -> int:
    assert normalize_value("11/05/21") == normalize_value("11 05 21") == "110521"
    assert normalize_value("LAST, FIRST") == normalize_value("LAST FIRST") == "LASTFIRST"
    assert normalize_value("$260.00") == "26000"
    assert normalize_value(None) == normalize_value("") == normalize_value("N/A") == ""

    truth_row = {"od_pat_name_1": "ENGLIFE, ZACH", "od_pat_dob_1": "10081984", "od_blank_1": ""}
    perfect = score_document(truth_row, {"od_pat_name_1": "ENGLIFE,  ZACH", "od_pat_dob_1": "10 08 1984"})
    assert perfect["nonempty_accuracy"] == 1.0, perfect
    partial = score_document(truth_row, {"od_pat_name_1": "ENGLIFE, ZACH", "od_pat_dob_1": "WRONG"})
    assert partial["nonempty_correct"] == 1 and partial["nonempty_total"] == 2, partial

    nested = flatten_extraction({"gpt_extraction_output": {"pages_1": {"od_pat_name_1": "X"}}})
    assert nested == {"od_pat_name_1": "X"}, nested

    pf = preflight_report({"duplicate-bad-alias.png": {"flagged": True}, "good.tif": None})
    assert pf["known_bad"] == [] and pf["recall_on_known_bad"] is None, pf

    agg = aggregate({"d": perfect})
    assert agg["documents_scored"] == 1 and agg["field_accuracy_micro"] == 1.0, agg

    print("self-test: all assertions passed")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score CMS-1500 extraction against ground truth.")
    parser.add_argument("--mode", choices=["local", "api", "score"], default="local")
    parser.add_argument("--tiers", default="economy,premium", help="Comma-separated tiers for local mode.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N samples.")
    parser.add_argument("--base-url", default="", help="ARGUS backend base URL (api mode).")
    parser.add_argument("--api-key", default="", help="X-API-Key value (api mode).")
    parser.add_argument("--tier-label", default="dataset-default", help="Label for the api-mode tier.")
    parser.add_argument("--poll-timeout", type=int, default=180, help="Per-doc poll timeout in seconds (api mode).")
    parser.add_argument("--results-dir", default="", help="Directory of extraction JSON files (score mode).")
    parser.add_argument("--self-test", action="store_true", help="Run scoring self-tests and exit.")
    parser.add_argument("--out", default="", help="Optional path to write the JSON report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return self_test()

    if args.mode == "local":
        report = run_local([t.strip() for t in args.tiers.split(",") if t.strip()], args.limit)
    elif args.mode == "api":
        if not args.base_url or not args.api_key:
            print("api mode requires --base-url and --api-key", file=sys.stderr)
            return 2
        report = run_api(args.base_url, args.api_key, args.tier_label, args.limit, args.poll_timeout)
    else:
        if not args.results_dir:
            print("score mode requires --results-dir", file=sys.stderr)
            return 2
        report = run_score_dir(Path(args.results_dir))

    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\nReport written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
