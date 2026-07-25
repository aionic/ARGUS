"""Deterministic extraction, confidence, cost, and latency evaluation."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from ai_ocr.azure.content_understanding import sanitize_cu_field_name

_NORMALIZE_RE = re.compile(r"[\s.,/\\:#()\-_$%]+")


def flatten_json(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dictionaries and arrays into stable dotted field paths."""
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(flatten_json(child, path))
        return flattened
    if isinstance(value, list):
        flattened = {}
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]"
            flattened.update(flatten_json(child, path))
        return flattened
    return {prefix: value}


def normalize_value(value: Any) -> str:
    """Normalize scalar values for format-insensitive golden comparison."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and math.isfinite(value):
        text = format(value, "f").rstrip("0").rstrip(".")
    else:
        text = str(value)
    text = text.strip().upper()
    if text in {"NONE", "NULL", "N/A"}:
        return ""
    numeric_candidate = text.replace("$", "").replace(",", "").replace("%", "").strip()
    if "." in numeric_candidate and re.fullmatch(r"-?\d+\.\d+", numeric_candidate):
        try:
            return format(Decimal(numeric_candidate).normalize(), "f")
        except InvalidOperation:
            pass
    return _NORMALIZE_RE.sub("", text)


def exact_value(value: Any) -> str:
    """Serialize a scalar without normalizing type or punctuation."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def confidence_path(field_path: str) -> str:
    """Convert an ARGUS field path to the sanitized path returned by CU."""
    parts = []
    for segment in field_path.split("."):
        match = re.fullmatch(r"([^\[]+)(.*)", segment)
        if not match:
            parts.append(sanitize_cu_field_name(segment))
            continue
        parts.append(f"{sanitize_cu_field_name(match.group(1))}{match.group(2)}")
    return ".".join(parts)


def score_document(
    ground_truth: dict[str, Any],
    actual: dict[str, Any],
    confidence: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Score one extraction against golden values at leaf-field granularity."""
    truth_fields = flatten_json(ground_truth)
    actual_fields = flatten_json(actual)
    confidence = confidence or {}
    field_paths = sorted(set(truth_fields) | set(actual_fields))
    field_results: list[dict[str, Any]] = []

    for path in field_paths:
        expected_known = path in truth_fields
        expected = truth_fields.get(path)
        observed = actual_fields.get(path)
        expected_present = expected_known and normalize_value(expected) != ""
        actual_present = normalize_value(observed) != ""
        exact_match = expected_known and exact_value(expected) == exact_value(observed)
        normalized_match = expected_known and normalize_value(expected) == normalize_value(observed)
        score = confidence.get(path)
        if score is None:
            score = confidence.get(confidence_path(path))
        field_results.append(
            {
                "path": path,
                "expected_known": expected_known,
                "expected": expected,
                "actual": observed,
                "expected_present": expected_present,
                "actual_present": actual_present,
                "exact_match": exact_match,
                "normalized_match": normalized_match,
                "confidence": float(score) if isinstance(score, (int, float)) else None,
            }
        )

    return {"fields": field_results, "metrics": aggregate_field_results(field_results)}


def aggregate_field_results(field_results: list[dict[str, Any]]) -> dict[str, Any]:
    expected = [field for field in field_results if field["expected_present"]]
    expected_blank = [field for field in field_results if field.get("expected_known") and not field["expected_present"]]
    true_positive_presence = sum(field["expected_present"] and field["actual_present"] for field in field_results)
    false_positive_presence = sum(not field["expected_present"] and field["actual_present"] for field in field_results)
    unknown_false_positive_presence = sum(
        not field.get("expected_known") and field["actual_present"] for field in field_results
    )
    false_negative_presence = sum(field["expected_present"] and not field["actual_present"] for field in field_results)
    exact_correct = sum(field["exact_match"] for field in expected)
    normalized_correct = sum(field["normalized_match"] for field in expected)
    blank_correct = sum(not field["actual_present"] for field in expected_blank)

    precision_denominator = true_positive_presence + false_positive_presence
    recall_denominator = true_positive_presence + false_negative_presence
    positive_accuracy = normalized_correct / len(expected) if expected else None
    blank_accuracy = blank_correct / len(expected_blank) if expected_blank else None
    balanced_accuracy = (
        (positive_accuracy + blank_accuracy) / 2
        if positive_accuracy is not None and blank_accuracy is not None
        else positive_accuracy
    )
    return {
        "expected_fields": len(expected),
        "expected_blank_fields": len(expected_blank),
        "exact_correct": exact_correct,
        "normalized_correct": normalized_correct,
        "blank_correct": blank_correct,
        "false_positive_fields": false_positive_presence,
        "unknown_false_positive_fields": unknown_false_positive_presence,
        "exact_accuracy": exact_correct / len(expected) if expected else None,
        "normalized_accuracy": positive_accuracy,
        "blank_accuracy": blank_accuracy,
        "balanced_accuracy": balanced_accuracy,
        "blank_field_false_positive_rate": (
            (false_positive_presence - unknown_false_positive_presence) / len(expected_blank)
            if expected_blank
            else None
        ),
        "hallucination_rate": (
            false_positive_presence / (len(expected_blank) + unknown_false_positive_presence)
            if len(expected_blank) + unknown_false_positive_presence
            else None
        ),
        "presence_precision": true_positive_presence / precision_denominator if precision_denominator else None,
        "presence_recall": true_positive_presence / recall_denominator if recall_denominator else None,
    }


def confidence_calibration(field_results: list[dict[str, Any]], bins: int = 10) -> dict[str, Any]:
    """Measure confidence coverage and calibration against normalized correctness."""
    scored = [
        field
        for field in field_results
        if field["expected_present"] and isinstance(field.get("confidence"), (int, float))
    ]
    expected_count = sum(field["expected_present"] for field in field_results)
    if not scored:
        return {
            "coverage": 0.0 if expected_count else None,
            "fields_scored": 0,
            "brier_score": None,
            "expected_calibration_error": None,
            "mean_confidence_correct": None,
            "mean_confidence_incorrect": None,
        }

    correct_confidence = [field["confidence"] for field in scored if field["normalized_match"]]
    incorrect_confidence = [field["confidence"] for field in scored if not field["normalized_match"]]
    brier = sum((field["confidence"] - float(field["normalized_match"])) ** 2 for field in scored) / len(scored)

    ece = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        bucket = [
            field
            for field in scored
            if lower <= field["confidence"] < upper or (index == bins - 1 and field["confidence"] == 1.0)
        ]
        if not bucket:
            continue
        mean_confidence = sum(field["confidence"] for field in bucket) / len(bucket)
        accuracy = sum(field["normalized_match"] for field in bucket) / len(bucket)
        ece += len(bucket) / len(scored) * abs(accuracy - mean_confidence)

    return {
        "coverage": len(scored) / expected_count if expected_count else None,
        "fields_scored": len(scored),
        "brier_score": brier,
        "expected_calibration_error": ece,
        "mean_confidence_correct": (sum(correct_confidence) / len(correct_confidence) if correct_confidence else None),
        "mean_confidence_incorrect": (
            sum(incorrect_confidence) / len(incorrect_confidence) if incorrect_confidence else None
        ),
    }


def aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate document runs by dataset and analyzer variant."""
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        groups[
            (
                run.get("vendor", "unknown"),
                run["dataset"],
                run["variant"],
                run.get("split", "unknown"),
            )
        ].append(run)

    summaries: list[dict[str, Any]] = []
    for (vendor, dataset, variant, split), group in sorted(groups.items()):
        successful = [run for run in group if not run.get("error")]
        fields = [field for run in successful for field in run["score"]["fields"]]
        field_metrics = aggregate_field_results(fields)
        calibration = confidence_calibration(fields)
        total_cost = sum(float(run.get("cost", {}).get("total_usd") or 0) for run in successful)
        total_pages = sum(int(run.get("cost", {}).get("processed_pages") or 0) for run in successful)
        extraction_cost = sum(float(run.get("cost", {}).get("extraction_total_usd") or 0) for run in successful)
        ocr_cost = sum(float(run.get("cost", {}).get("ocr_preflight_usd") or 0) for run in successful)
        latencies = sorted(float(run.get("latency_seconds") or 0) for run in successful)
        ocr_word_count = sum(int((run.get("ocr") or {}).get("n_words") or 0) for run in successful)
        ocr_confidence = (
            sum(
                float((run.get("ocr") or {}).get("mean") or 0) * int((run.get("ocr") or {}).get("n_words") or 0)
                for run in successful
            )
            / ocr_word_count
            if ocr_word_count
            else None
        )
        ocr_low_fraction = (
            sum(
                float((run.get("ocr") or {}).get("frac_low") or 0) * int((run.get("ocr") or {}).get("n_words") or 0)
                for run in successful
            )
            / ocr_word_count
            if ocr_word_count
            else None
        )
        extracted_grounding_fields = sum(
            int((run.get("grounding") or {}).get("extracted_fields") or 0) for run in successful
        )
        grounded_fields = sum(int((run.get("grounding") or {}).get("grounded_fields") or 0) for run in successful)
        source_consistent_fields = sum(
            int((run.get("grounding") or {}).get("source_text_consistent_fields") or 0) for run in successful
        )
        passing_documents = sum(_document_passed((run.get("score") or {}).get("metrics") or {}) for run in successful)
        summaries.append(
            {
                "vendor": vendor,
                "dataset": dataset,
                "variant": variant,
                "split": split,
                "documents_attempted": len(group),
                "documents_succeeded": len(successful),
                "documents_failed": len(group) - len(successful),
                "documents_passing": passing_documents,
                **field_metrics,
                **calibration,
                "ocr_word_confidence_mean": ocr_confidence,
                "ocr_low_word_fraction": ocr_low_fraction,
                "grounding_coverage": (
                    grounded_fields / extracted_grounding_fields if extracted_grounding_fields else None
                ),
                "source_text_consistency": (
                    source_consistent_fields / extracted_grounding_fields if extracted_grounding_fields else None
                ),
                "total_cost_usd": total_cost,
                "extraction_cost_usd": extraction_cost,
                "ocr_preflight_cost_usd": ocr_cost,
                "avg_cost_usd": total_cost / len(successful) if successful else None,
                "cost_per_page_usd": total_cost / total_pages if total_pages else None,
                "avg_latency_seconds": sum(latencies) / len(latencies) if latencies else None,
                "p50_latency_seconds": _percentile(latencies, 0.50),
                "p95_latency_seconds": _percentile(latencies, 0.95),
                "cost_per_normalized_correct_field_usd": (
                    total_cost / field_metrics["normalized_correct"] if field_metrics["normalized_correct"] else None
                ),
                "cost_per_passing_document_usd": (total_cost / passing_documents if passing_documents else None),
            }
        )
    successful_runs = [run for run in runs if not run.get("error")]
    totals = {
        "documents_attempted": len(runs),
        "documents_succeeded": len(successful_runs),
        "documents_failed": len(runs) - len(successful_runs),
        "total_cost_usd": sum(float(run.get("cost", {}).get("total_usd") or 0) for run in successful_runs),
        "extraction_cost_usd": sum(
            float(run.get("cost", {}).get("extraction_total_usd") or 0) for run in successful_runs
        ),
        "ocr_preflight_cost_usd": sum(
            float(run.get("cost", {}).get("ocr_preflight_usd") or 0) for run in successful_runs
        ),
    }
    return {"summaries": summaries, "runs": runs, "totals": totals}


def _document_passed(metrics: dict[str, Any]) -> bool:
    positive_accuracy = metrics.get("normalized_accuracy")
    blank_accuracy = metrics.get("blank_accuracy")
    return bool(
        isinstance(positive_accuracy, (int, float))
        and positive_accuracy >= 0.80
        and (blank_accuracy is None or blank_accuracy >= 0.95)
    )


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    index = round((len(values) - 1) * quantile)
    return values[index]
