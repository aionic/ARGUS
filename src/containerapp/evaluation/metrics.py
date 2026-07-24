"""Grounding and scan telemetry helpers for bake-off results."""

from __future__ import annotations

from typing import Any

from evaluation.golden import normalize_value

_VALUE_KEYS = (
    "valueString",
    "valueNumber",
    "valueInteger",
    "valueBoolean",
    "valueDate",
    "valueTime",
)


def grounding_metrics(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("raw") or {}
    contents = raw.get("contents") or []
    ocr_text = normalize_value(result.get("ocr_output") or "")
    leaf_fields: list[dict[str, Any]] = []
    for content in contents:
        if isinstance(content, dict):
            leaf_fields.extend(_flatten_cu_fields(content.get("fields") or {}))

    extracted = [field for field in leaf_fields if normalize_value(field.get("value"))]
    grounded = [field for field in extracted if field.get("has_source")]
    text_consistent = [
        field
        for field in extracted
        if normalize_value(field.get("value")) and normalize_value(field.get("value")) in ocr_text
    ]
    return {
        "extracted_fields": len(extracted),
        "grounded_fields": len(grounded),
        "grounding_coverage": len(grounded) / len(extracted) if extracted else None,
        "source_text_consistent_fields": len(text_consistent),
        "source_text_consistency": len(text_consistent) / len(extracted) if extracted else None,
        "spatial_accuracy_available": False,
    }


def _flatten_cu_fields(fields: dict[str, Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for field in fields.values():
        if not isinstance(field, dict):
            continue
        field_type = field.get("type")
        if field_type == "object":
            flattened.extend(_flatten_cu_fields(field.get("valueObject") or {}))
            continue
        if field_type == "array":
            for item in field.get("valueArray") or []:
                if isinstance(item, dict) and item.get("type") == "object":
                    flattened.extend(_flatten_cu_fields(item.get("valueObject") or {}))
                elif isinstance(item, dict):
                    flattened.append(_leaf_metadata(item))
            continue
        flattened.append(_leaf_metadata(field))
    return flattened


def _leaf_metadata(field: dict[str, Any]) -> dict[str, Any]:
    value = next((field[key] for key in _VALUE_KEYS if key in field), None)
    source = field.get("source") or field.get("sources") or field.get("spans") or field.get("boundingRegions")
    return {"value": value, "has_source": bool(source)}
