"""Run reproducible OCR and extraction bake-offs against the Conduent corpus."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image
from PyPDF2 import PdfReader

CONTAINERAPP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CONTAINERAPP_ROOT.parents[1] if CONTAINERAPP_ROOT.name == "containerapp" else CONTAINERAPP_ROOT
sys.path.insert(0, str(CONTAINERAPP_ROOT))

from ai_ocr.azure.doc_intelligence import get_read_confidence  # noqa: E402
from ai_ocr.cost.pricing import get_di_page_pricing  # noqa: E402
from ai_ocr.cost.tracking import CostTracker  # noqa: E402
from evaluation.adapters import AdapterConfiguration, EvaluationAdapter, get_adapter  # noqa: E402
from evaluation.blob_io import download_prefix, upload_directory  # noqa: E402
from evaluation.corpus import GOLDEN_DATASETS, ConduentCorpus, CorpusCase  # noqa: E402
from evaluation.golden import aggregate_runs, score_document  # noqa: E402
from evaluation.metrics import grounding_metrics  # noqa: E402
from evaluation.preprocessing import preprocess_document  # noqa: E402
from evaluation.schema_profiles import (  # noqa: E402
    adapt_confidence,
    adapt_extracted_data,
    build_analyzer_options,
    build_example_schema,
    json_schema_to_example,
)

DEFAULT_VARIANTS = Path(__file__).with_name("variants.json")
EVALUATOR_CONTRACT_VERSION = "v1"


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_variants(path: Path, selected: set[str] | None = None) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    variants = payload.get("variants")
    if not isinstance(variants, list):
        raise ValueError("Variant configuration must contain a 'variants' array")
    result = []
    for variant in variants:
        if not isinstance(variant, dict) or not variant.get("name"):
            raise ValueError("Each variant must be an object with a name")
        if selected and variant["name"] not in selected:
            continue
        result.append(variant)
    return result


def resolve_variant(variant: dict[str, Any], dataset: str) -> dict[str, Any] | None:
    applies_to = variant.get("applies_to")
    if isinstance(applies_to, list) and dataset not in applies_to:
        return None
    resolved = deepcopy(variant)
    dataset_overrides = (variant.get("datasets") or {}).get(dataset)
    if isinstance(dataset_overrides, dict):
        resolved = _merge(resolved, dataset_overrides)
    resolved.pop("datasets", None)
    return resolved


def build_configurations(
    corpus: ConduentCorpus,
    datasets: list[str],
    variants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    configurations = []
    for dataset in datasets:
        schema_ids = {record["schema_id"] for record in corpus.load_manifest(dataset)}
        if len(schema_ids) != 1:
            raise ValueError(f"Dataset {dataset} must reference exactly one schema")
        schema = corpus.schemas[next(iter(schema_ids))]
        flat_example_schema = json_schema_to_example(schema)
        for variant in variants:
            resolved = resolve_variant(variant, dataset)
            if not resolved:
                continue
            profile = str(resolved.get("profile") or "baseline")
            example_schema = build_example_schema(dataset, schema, profile)
            adapter = get_adapter(str(resolved.get("vendor") or "azure-content-understanding"))
            options = build_analyzer_options(
                dataset,
                schema,
                profile,
                resolved.get("analyzer_options"),
            )
            adapter_config = adapter.describe(dataset, example_schema, options)
            run_hash = _hash_json(
                {
                    "evaluator_contract": EVALUATOR_CONTRACT_VERSION,
                    "adapter_configuration_hash": adapter_config.configuration_hash,
                    "preprocessing": resolved.get("preprocessing") or {"mode": "none"},
                    "ocr_preflight": bool(resolved.get("ocr_preflight", True)),
                }
            )
            configurations.append(
                {
                    "dataset": dataset,
                    "variant": resolved["name"],
                    "description": resolved.get("description"),
                    "vendor": adapter.name,
                    "profile": profile,
                    "resolved_variant": resolved,
                    "schema": schema,
                    "example_schema": example_schema,
                    "flat_example_schema": flat_example_schema,
                    "analyzer_options": options,
                    "adapter": adapter,
                    "adapter_configuration": adapter_config,
                    "run_configuration_hash": run_hash,
                }
            )
    return configurations


def run_evaluation(
    cases: list[CorpusCase],
    configurations: list[dict[str, Any]],
    output_dir: Path,
    *,
    mode: str,
    force: bool,
    prepare_only: bool,
    corpus_hash: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runs: list[dict[str, Any]] = []
    preparation: list[dict[str, Any]] = []
    prepared: dict[tuple[str, str], str | None] = {}

    for configuration in configurations:
        key = (configuration["dataset"], configuration["variant"])
        adapter: EvaluationAdapter = configuration["adapter"]
        if mode == "score":
            prepared[key] = configuration["adapter_configuration"].configuration_id
            preparation.append({**_configuration_metadata(configuration), "status": "not_required"})
            continue
        try:
            analyzer_id = adapter.prepare(
                configuration["dataset"],
                configuration["example_schema"],
                configuration["analyzer_options"],
            )
            prepared[key] = analyzer_id
            preparation.append({**_configuration_metadata(configuration), "status": "ready"})
            print(
                f"prepared dataset={configuration['dataset']} variant={configuration['variant']} "
                f"configuration={analyzer_id}"
            )
        except Exception as exc:  # noqa: BLE001 - every preparation failure is retained in the run manifest
            message = f"{type(exc).__name__}: {exc}"
            prepared[key] = None
            preparation.append({**_configuration_metadata(configuration), "status": "failed", "error": message})
            print(f"failed dataset={configuration['dataset']} variant={configuration['variant']} error={message}")

    if prepare_only:
        return runs, preparation

    configurations_by_dataset: dict[str, list[dict[str, Any]]] = {}
    for configuration in configurations:
        configurations_by_dataset.setdefault(configuration["dataset"], []).append(configuration)

    for case in cases:
        for configuration in configurations_by_dataset.get(case.dataset, []):
            key = (case.dataset, configuration["variant"])
            if not prepared.get(key):
                runs.append(
                    {
                        **_run_identity(case, configuration),
                        "error": "Analyzer preparation failed; see run manifest.",
                    }
                )
                continue
            raw_path = _result_path(output_dir, case, configuration)
            try:
                if mode == "score":
                    envelope = _load_result(raw_path, case, configuration, corpus_hash)
                elif raw_path.exists() and not force:
                    envelope = _load_result(raw_path, case, configuration, corpus_hash)
                else:
                    envelope = _execute_case(case, configuration, output_dir, corpus_hash)
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_path.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
                run = _score_envelope(case, configuration, envelope)
                runs.append(run)
                metrics = run["score"]["metrics"]
                print(
                    f"scored dataset={case.dataset} split={case.split} variant={configuration['variant']} "
                    f"sample={case.document_id} accuracy={metrics['normalized_accuracy']} "
                    f"blank_accuracy={metrics['blank_accuracy']} cost=${run['cost']['total_usd']:.6f}"
                )
            except Exception as exc:  # noqa: BLE001 - one failed sample must not erase the comparison
                message = f"{type(exc).__name__}: {exc}"
                runs.append({**_run_identity(case, configuration), "error": message})
                print(
                    f"failed dataset={case.dataset} variant={configuration['variant']} "
                    f"sample={case.document_id} error={message}"
                )
    return runs, preparation


def _execute_case(
    case: CorpusCase,
    configuration: dict[str, Any],
    output_dir: Path,
    corpus_hash: str,
) -> dict[str, Any]:
    resolved = configuration["resolved_variant"]
    processed_path, preprocessing = preprocess_document(
        case.document_path,
        output_dir / "preprocessed",
        case.document_id,
        resolved.get("preprocessing"),
    )
    ocr = _load_or_run_ocr_preflight(
        processed_path,
        output_dir,
        enabled=bool(resolved.get("ocr_preflight", True)),
    )

    started = time.perf_counter()
    adapter: EvaluationAdapter = configuration["adapter"]
    result = adapter.run(
        processed_path,
        case.dataset,
        configuration["example_schema"],
        configuration["analyzer_options"],
    )
    analyzer_extracted_data = result.get("extracted_data") or {}
    result["analyzer_extracted_data"] = analyzer_extracted_data
    result["extracted_data"] = adapt_extracted_data(
        case.dataset,
        configuration["profile"],
        analyzer_extracted_data,
        configuration["flat_example_schema"],
    )
    result["confidence"] = adapt_confidence(
        case.dataset,
        configuration["profile"],
        result.get("confidence") or {},
        configuration["flat_example_schema"],
    )
    extraction_latency = time.perf_counter() - started
    return {
        "metadata": {
            **_run_identity(case, configuration),
            "corpus_hash": corpus_hash,
            "document_sha256": case.manifest["content_sha256"],
            "processed_document_sha256": preprocessing["output_sha256"],
            "created_at_utc": datetime.now(UTC).isoformat(),
            "attempts": 1,
        },
        "preprocessing": preprocessing,
        "ocr": ocr,
        "timing": {
            "preprocessing_seconds": preprocessing.get("latency_seconds", 0),
            "ocr_preflight_seconds": ocr.get("latency_seconds", 0),
            "extraction_seconds": extraction_latency,
            "total_seconds": preprocessing.get("latency_seconds", 0)
            + ocr.get("latency_seconds", 0)
            + extraction_latency,
        },
        "result": result,
    }


def _score_envelope(
    case: CorpusCase,
    configuration: dict[str, Any],
    envelope: dict[str, Any],
) -> dict[str, Any]:
    result = envelope["result"]
    region = os.getenv("AZURE_REGION") or os.getenv("AZURE_LOCATION") or "eastus2"
    tracker = CostTracker(region=region)
    extraction_cost = tracker.record_cu_usage(result.get("usage"), extraction_model="content-understanding")
    ocr_cost = float((envelope.get("ocr") or {}).get("cost_usd") or 0)
    cost = {
        **extraction_cost,
        "extraction_total_usd": extraction_cost["total_usd"],
        "ocr_preflight_usd": ocr_cost,
        "processed_pages": max(
            int(extraction_cost.get("total_pages") or 0),
            int((envelope.get("ocr") or {}).get("pages") or 0),
        ),
        "total_usd": extraction_cost["total_usd"] + ocr_cost,
    }
    return {
        **_run_identity(case, configuration),
        "latency_seconds": (envelope.get("timing") or {}).get("total_seconds"),
        "timing": envelope.get("timing") or {},
        "preprocessing": envelope.get("preprocessing") or {},
        "ocr": envelope.get("ocr") or {},
        "usage": result.get("usage") or {},
        "cost": cost,
        "grounding": grounding_metrics(result),
        "score": score_document(case.truth or {}, result.get("extracted_data") or {}, result.get("confidence")),
    }


def _load_or_run_ocr_preflight(path: Path, output_dir: Path, *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "cost_usd": 0.0, "latency_seconds": 0.0}
    content_hash = _sha256_file(path)
    cache_path = output_dir / "ocr" / f"{content_hash}.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("document_sha256") == content_hash and not cached.get("error"):
            return cached

    pages = _page_count(path)
    started = time.perf_counter()
    try:
        stats = get_read_confidence(str(path), None, word_min=0.70, raise_on_error=True)
        error = None
    except Exception as exc:  # noqa: BLE001 - extraction remains useful when the independent OCR probe fails
        stats = None
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    region = os.getenv("AZURE_REGION") or os.getenv("AZURE_LOCATION") or "eastus2"
    pricing = get_di_page_pricing(region)
    payload = {
        "enabled": True,
        "document_sha256": content_hash,
        "pages": pages,
        "latency_seconds": elapsed,
        "cost_usd": pages * pricing.usd_per_page if stats else 0.0,
        "pricing_source": pricing.source,
        "usd_per_page": pricing.usd_per_page,
        **(stats or {}),
        **({"error": error} if error else {}),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _load_result(
    path: Path,
    case: CorpusCase,
    configuration: dict[str, Any],
    corpus_hash: str,
) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing cached result: {path}")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    metadata = envelope.get("metadata") or {}
    expected = {
        "document_id": case.document_id,
        "document_sha256": case.manifest["content_sha256"],
        "run_configuration_hash": configuration["run_configuration_hash"],
        "corpus_hash": corpus_hash,
    }
    mismatches = [key for key, expected_value in expected.items() if metadata.get(key) != expected_value]
    if mismatches:
        raise ValueError(f"Cached result provenance mismatch ({', '.join(mismatches)}): {path}")
    return envelope


def _result_path(output_dir: Path, case: CorpusCase, configuration: dict[str, Any]) -> Path:
    config_hash = configuration["run_configuration_hash"][:16]
    return (
        output_dir
        / "raw"
        / configuration["vendor"]
        / case.dataset
        / configuration["variant"]
        / f"{case.document_id}-{config_hash}.json"
    )


def _run_identity(case: CorpusCase, configuration: dict[str, Any]) -> dict[str, Any]:
    adapter_config: AdapterConfiguration = configuration["adapter_configuration"]
    return {
        "vendor": configuration["vendor"],
        "dataset": case.dataset,
        "variant": configuration["variant"],
        "split": case.split,
        "sample": case.document_id,
        "original_filename": case.original_filename,
        "schema_id": case.schema_id,
        "configuration_id": adapter_config.configuration_id,
        "adapter_configuration_hash": adapter_config.configuration_hash,
        "run_configuration_hash": configuration["run_configuration_hash"],
    }


def _configuration_metadata(configuration: dict[str, Any]) -> dict[str, Any]:
    adapter_config: AdapterConfiguration = configuration["adapter_configuration"]
    return {
        "vendor": configuration["vendor"],
        "dataset": configuration["dataset"],
        "variant": configuration["variant"],
        "profile": configuration["profile"],
        "configuration_id": adapter_config.configuration_id,
        "adapter_configuration_hash": adapter_config.configuration_hash,
        "run_configuration_hash": configuration["run_configuration_hash"],
        "preprocessing": configuration["resolved_variant"].get("preprocessing") or {"mode": "none"},
        "ocr_preflight": bool(configuration["resolved_variant"].get("ocr_preflight", True)),
    }


def write_reports(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    summary_columns = [
        "vendor",
        "dataset",
        "variant",
        "split",
        "documents_succeeded",
        "documents_failed",
        "documents_passing",
        "normalized_accuracy",
        "blank_accuracy",
        "balanced_accuracy",
        "presence_precision",
        "presence_recall",
        "coverage",
        "brier_score",
        "expected_calibration_error",
        "ocr_word_confidence_mean",
        "ocr_low_word_fraction",
        "grounding_coverage",
        "source_text_consistency",
        "total_cost_usd",
        "avg_cost_usd",
        "cost_per_page_usd",
        "cost_per_normalized_correct_field_usd",
        "cost_per_passing_document_usd",
        "avg_latency_seconds",
        "p95_latency_seconds",
    ]
    with (output_dir / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["summaries"])

    field_columns = [
        "vendor",
        "dataset",
        "variant",
        "split",
        "sample",
        "path",
        "expected",
        "actual",
        "expected_present",
        "actual_present",
        "exact_match",
        "normalized_match",
        "confidence",
    ]
    with (output_dir / "fields.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=field_columns, extrasaction="ignore")
        writer.writeheader()
        for run in report["runs"]:
            for field in (run.get("score") or {}).get("fields", []):
                writer.writerow(
                    {
                        "vendor": run.get("vendor"),
                        "dataset": run["dataset"],
                        "variant": run["variant"],
                        "split": run.get("split"),
                        "sample": run["sample"],
                        **field,
                    }
                )

    lines = [
        "# Conduent OCR and extraction bake-off",
        "",
        "A passing document has at least 80% normalized accuracy on populated truth fields and "
        "at least 95% accuracy on known blank fields.",
        "",
        f"Total measured processing cost: ${float((report.get('totals') or {}).get('total_cost_usd') or 0):.6f} "
        f"across {int((report.get('totals') or {}).get('documents_succeeded') or 0)} successful "
        "document-variant runs.",
        "",
        "| Dataset | Split | Variant | Docs | Populated accuracy | Blank accuracy | Confidence coverage | OCR confidence | Grounding | Cost | Cost/page | Cost/correct field | P95 latency |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for summary in report["summaries"]:
        lines.append(
            f"| {summary['dataset']} | {summary['split']} | {summary['variant']} | "
            f"{summary['documents_succeeded']} | {_format_metric(summary.get('normalized_accuracy'))} | "
            f"{_format_metric(summary.get('blank_accuracy'))} | "
            f"{_format_metric(summary.get('coverage'))} | "
            f"{_format_metric(summary.get('ocr_word_confidence_mean'))} | "
            f"{_format_metric(summary.get('grounding_coverage'))} | "
            f"${_format_metric(summary.get('total_cost_usd'), 6)} | "
            f"${_format_metric(summary.get('cost_per_page_usd'), 6)} | "
            f"${_format_metric(summary.get('cost_per_normalized_correct_field_usd'), 6)} | "
            f"{_format_metric(summary.get('p95_latency_seconds'), 2)}s |"
        )
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _configuration_set_hash(
    corpus_hash: str,
    variant_file_hash: str,
    configurations: list[dict[str, Any]],
) -> str:
    return _hash_json(
        {
            "corpus_hash": corpus_hash,
            "variant_file_hash": variant_file_hash,
            "configurations": sorted(
                (
                    configuration["dataset"],
                    configuration["variant"],
                    configuration["run_configuration_hash"],
                )
                for configuration in configurations
            ),
        }
    )


def _enforce_holdout_lock(
    output_dir: Path,
    requested_splits: set[str],
    allow_holdout: bool,
    configuration_set_hash: str,
) -> None:
    if "holdout" not in requested_splits:
        return
    if not allow_holdout:
        raise ValueError("Holdout runs require --allow-holdout and a matching frozen config-lock.json")
    lock_path = output_dir / "config-lock.json"
    if not lock_path.exists():
        raise FileNotFoundError(f"Holdout config lock not found: {lock_path}")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("configuration_set_hash") != configuration_set_hash:
        raise ValueError("Holdout config lock does not match the selected corpus and analyzer configurations")


def _write_config_lock(output_dir: Path, configuration_set_hash: str, configurations: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "frozen_at_utc": datetime.now(UTC).isoformat(),
        "configuration_set_hash": configuration_set_hash,
        "configurations": [_configuration_metadata(configuration) for configuration in configurations],
    }
    (output_dir / "config-lock.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_run_manifest(
    output_dir: Path,
    *,
    args: argparse.Namespace,
    corpus_report: dict[str, Any],
    configuration_set_hash: str | None,
    configurations: list[dict[str, Any]],
    preparation: list[dict[str, Any]] | None = None,
    runs: list[dict[str, Any]] | None = None,
    status: str = "started",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "run-manifest.json"
    existing_run_id = None
    if manifest_path.exists():
        try:
            existing_run_id = json.loads(manifest_path.read_text(encoding="utf-8")).get("run_id")
        except (OSError, ValueError):
            existing_run_id = None
    payload = {
        "run_id": existing_run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "status": status,
        "mode": args.mode,
        "datasets": args.datasets,
        "splits": args.splits,
        "corpus_root": corpus_report["corpus_root"],
        "corpus_hash": corpus_report["corpus_hash"],
        "configuration_set_hash": configuration_set_hash,
        "git_commit": _git_commit(),
        "evaluator_contract_version": EVALUATOR_CONTRACT_VERSION,
        "configurations": [_configuration_metadata(configuration) for configuration in configurations],
        "preparation": preparation or [],
        "documents_attempted": len(runs or []),
        "documents_failed": sum(bool(run.get("error")) for run in runs or []),
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _page_count(path: Path) -> int:
    if path.suffix.lower() == ".pdf":
        return len(PdfReader(str(path)).pages)
    with Image.open(path) as image:
        return int(getattr(image, "n_frames", 1) or 1)


def _hash_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _format_metric(value: Any, digits: int = 3) -> str:
    return "-" if value is None else f"{float(value):.{digits}f}"


def _comma_set(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "score"], default="live")
    parser.add_argument("--corpus-root", type=Path)
    parser.add_argument("--blob-account-url", default=os.getenv("BLOB_ACCOUNT_URL"))
    parser.add_argument("--corpus-blob-container", default=os.getenv("EVALUATION_BLOB_CONTAINER"))
    parser.add_argument("--corpus-blob-prefix", default=os.getenv("EVALUATION_CORPUS_BLOB_PREFIX"))
    parser.add_argument("--output-blob-container", default=os.getenv("EVALUATION_BLOB_CONTAINER"))
    parser.add_argument("--output-blob-prefix", default=os.getenv("EVALUATION_OUTPUT_BLOB_PREFIX"))
    parser.add_argument("--resume-output", action="store_true")
    parser.add_argument("--datasets", default=",".join(GOLDEN_DATASETS))
    parser.add_argument("--splits", default="tuning,calibration")
    parser.add_argument("--variants", default="", help="Optional comma-separated variant names.")
    parser.add_argument("--variant-config", type=Path, default=DEFAULT_VARIANTS)
    parser.add_argument("--limit", type=int, default=None, help="Maximum samples per dataset.")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "artifacts" / "conduent-bakeoff")
    parser.add_argument("--force", action="store_true", help="Ignore matching cached results.")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--freeze-config", action="store_true")
    parser.add_argument("--allow-holdout", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    corpus_root = args.corpus_root
    if not corpus_root and args.blob_account_url and args.corpus_blob_container and args.corpus_blob_prefix:
        corpus_root = download_prefix(
            args.blob_account_url,
            args.corpus_blob_container,
            args.corpus_blob_prefix,
            Path(os.getenv("CONDUENT_CORPUS_CACHE", "/tmp/conduent-datasets")),
        )
    corpus = ConduentCorpus(corpus_root)
    corpus_report = corpus.validate()
    if args.validate_only:
        print(json.dumps(corpus_report, indent=2))
        return 0 if corpus_report["valid"] else 1
    if not corpus_report["valid"]:
        print(json.dumps(corpus_report, indent=2))
        return 1
    if args.resume_output:
        if not args.blob_account_url or not args.output_blob_container or not args.output_blob_prefix:
            raise ValueError("--resume-output requires blob account, container, and output prefix settings")
        download_prefix(
            args.blob_account_url,
            args.output_blob_container,
            args.output_blob_prefix,
            args.output_dir,
        )

    datasets = sorted(item for item in _comma_set(args.datasets) if item in corpus.dataset_names())
    unknown_datasets = _comma_set(args.datasets) - set(datasets)
    if unknown_datasets:
        raise ValueError(f"Unknown datasets: {', '.join(sorted(unknown_datasets))}")
    selected_variants = _comma_set(args.variants) or None
    variants = load_variants(args.variant_config, selected_variants)
    if not variants:
        raise ValueError("No analyzer variants selected")
    configurations = build_configurations(corpus, datasets, variants)

    corpus_hash = corpus_report["corpus_hash"]
    variant_file_hash = _sha256_file(args.variant_config)
    configuration_set_hash = _configuration_set_hash(corpus_hash, variant_file_hash, configurations)
    requested_splits = _comma_set(args.splits)
    if args.freeze_config and "holdout" in requested_splits:
        raise ValueError("Freeze configurations before requesting a holdout run")
    _enforce_holdout_lock(
        args.output_dir,
        requested_splits,
        args.allow_holdout,
        configuration_set_hash,
    )

    cases = corpus.iter_cases(
        datasets,
        requested_splits,
        golden_only=True,
        limit_per_dataset=args.limit,
    )
    if not cases and not args.prepare_only:
        raise ValueError("No golden corpus cases matched the selected datasets and splits")

    _write_run_manifest(
        args.output_dir,
        args=args,
        corpus_report=corpus_report,
        configuration_set_hash=configuration_set_hash,
        configurations=configurations,
    )
    runs, preparation = run_evaluation(
        cases,
        configurations,
        args.output_dir,
        mode=args.mode,
        force=args.force,
        prepare_only=args.prepare_only,
        corpus_hash=corpus_hash,
    )
    preparation_failed = any(item["status"] == "failed" for item in preparation)
    if args.freeze_config and not preparation_failed:
        _write_config_lock(args.output_dir, configuration_set_hash, configurations)
    if not args.prepare_only:
        report = aggregate_runs(runs)
        report["corpus"] = corpus_report
        report["configuration_set_hash"] = configuration_set_hash
        write_reports(report, args.output_dir)
    _write_run_manifest(
        args.output_dir,
        args=args,
        corpus_report=corpus_report,
        configuration_set_hash=configuration_set_hash,
        configurations=configurations,
        preparation=preparation,
        runs=runs,
        status="failed" if preparation_failed or any(run.get("error") for run in runs) else "completed",
    )
    if args.blob_account_url and args.output_blob_container and args.output_blob_prefix:
        upload_directory(
            args.blob_account_url,
            args.output_blob_container,
            args.output_blob_prefix,
            args.output_dir,
        )
    return 1 if preparation_failed or any(run.get("error") for run in runs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
