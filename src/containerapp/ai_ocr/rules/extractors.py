from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

CONFIDENCE_THRESHOLD = 0.70

NAMED_PATTERNS = {
    "amount": r"(?P<value>\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?)",
    "currency": r"(?P<value>\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?)",
    "date": r"(?P<value>\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b)",
    "email": r"(?P<value>[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
    "invoice_number": r"(?:invoice|inv)\s*(?:number|no\.?|#)?\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9-]+)",
    "number": r"(?P<value>\b\d+(?:\.\d+)?\b)",
    "phone": r"(?P<value>\+?\d[\d\s().-]{7,}\d)",
}


@dataclass(frozen=True)
class ExtractorResult:
    """Result of deterministic rules-based OCR field extraction."""

    filled: dict[str, Any]
    confidence: dict[str, float]
    remaining_fields: list[str]
    all_required_filled: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "filled": dict(self.filled),
            "confidence": dict(self.confidence),
            "remaining_fields": list(self.remaining_fields),
            "all_required_filled": self.all_required_filled,
        }


def run_extractors(ocr_text: str, schema: dict, rules: dict) -> ExtractorResult:
    """Run deterministic rules against OCR text before LLM extraction.

    Args:
        ocr_text: Markdown or plain OCR text.
        schema: Extraction schema. Top-level keys or JSON-schema ``properties``
            are treated as field names.
        rules: Per-field rules in the approved shape:
            ``{field: {type: regex|keyword|positional, ...}}``.

    Returns:
        ExtractorResult with confidently filled fields and fields left for LLM.
    """
    text = ocr_text or ""
    schema_fields = _schema_field_names(schema)
    if not schema_fields and isinstance(rules, dict):
        schema_fields = list(rules)

    filled: dict[str, Any] = {}
    confidence: dict[str, float] = {}

    for field_name in schema_fields:
        rule = rules.get(field_name, {}) if isinstance(rules, dict) else {}
        if not isinstance(rule, dict):
            continue

        value, score = _extract_field(text, field_name, rule)
        if value is not None and score >= CONFIDENCE_THRESHOLD:
            filled[field_name] = value
            confidence[field_name] = round(score, 3)

    remaining_fields = [field_name for field_name in schema_fields if field_name not in filled]
    required_fields = _required_fields(schema, rules)
    all_required_filled = all(field_name in filled for field_name in required_fields)

    return ExtractorResult(
        filled=filled,
        confidence=confidence,
        remaining_fields=remaining_fields,
        all_required_filled=all_required_filled,
    )


def reduce_schema(schema: dict, remaining_fields: list[str]) -> dict:
    """Return a shrunk schema copy containing only unfilled fields."""
    remaining = set(remaining_fields)
    if not isinstance(schema, dict):
        return {}

    reduced = copy.deepcopy(schema)
    properties = reduced.get("properties")
    if isinstance(properties, dict):
        reduced["properties"] = {key: value for key, value in properties.items() if key in remaining}
        if isinstance(reduced.get("required"), list):
            reduced["required"] = [field_name for field_name in reduced["required"] if field_name in remaining]
        return reduced

    return {key: copy.deepcopy(value) for key, value in schema.items() if key in remaining}


def _extract_field(text: str, field_name: str, rule: dict) -> tuple[Any | None, float]:
    rule_type = str(rule.get("type", "")).lower()
    if rule_type == "regex":
        return _extract_regex(text, field_name, rule)
    if rule_type == "keyword":
        return _extract_keyword(text, field_name, rule)
    if rule_type == "positional":
        return _extract_positional(text, field_name, rule)
    return None, 0.0


def _extract_regex(text: str, field_name: str, rule: dict) -> tuple[str | None, float]:
    patterns = _coerce_list(rule.get("pattern") or rule.get("patterns") or rule.get("name") or field_name)

    for pattern in patterns:
        regex_text = NAMED_PATTERNS.get(str(pattern).lower(), str(pattern))
        try:
            match = re.search(regex_text, text, flags=re.IGNORECASE | re.MULTILINE)
        except re.error:
            continue
        if not match:
            continue

        value = _match_value(match, field_name)
        if value:
            score = 0.95 if match.groupdict() else 0.90
            return value, score

    return None, 0.0


def _extract_keyword(text: str, field_name: str, rule: dict) -> tuple[str | None, float]:
    keywords = _coerce_list(rule.get("keywords") or rule.get("keyword") or field_name.replace("_", " "))
    lines = text.splitlines()

    for keyword in keywords:
        keyword_text = str(keyword).strip()
        if not keyword_text:
            continue

        for index, line in enumerate(lines):
            if keyword_text.lower() not in line.lower():
                continue

            table_value = _table_value_after_keyword(line, keyword_text)
            if table_value:
                return table_value, 0.83

            same_line = _value_after_keyword(line, keyword_text)
            if same_line:
                return same_line, 0.86

            next_line = _next_non_empty_line(lines, index + 1)
            if next_line:
                return next_line, 0.75

    return None, 0.0


def _extract_positional(text: str, field_name: str, rule: dict) -> tuple[str | None, float]:
    coords = rule.get("coords") or {}
    lines = [line for line in text.splitlines() if line.strip()]

    if isinstance(coords, dict):
        line_index = _positional_line_index(coords)
        if line_index is not None and 0 <= line_index < len(lines):
            line = lines[line_index]
            column_value = _positional_column_value(line, coords)
            if column_value:
                return column_value, 0.72

            label_value = _value_after_keyword(line, field_name.replace("_", " "))
            if label_value:
                return label_value, 0.78
            return _clean_value(line), 0.70

        label = coords.get("label") or coords.get("keyword")
        if label:
            return _extract_keyword(text, field_name, {"keywords": [label]})

    if isinstance(coords, list) and coords and isinstance(coords[0], int):
        line_index = coords[0]
        if 0 <= line_index < len(lines):
            return _clean_value(lines[line_index]), 0.70

    return None, 0.0


def _schema_field_names(schema: dict) -> list[str]:
    if not isinstance(schema, dict):
        return []

    properties = schema.get("properties")
    if isinstance(properties, dict):
        return list(properties)

    reserved = {"$schema", "additionalProperties", "description", "required", "title", "type"}
    return [key for key in schema if key not in reserved]


def _required_fields(schema: dict, rules: dict) -> set[str]:
    required = set()
    if isinstance(schema, dict) and isinstance(schema.get("required"), list):
        required.update(str(field_name) for field_name in schema["required"])

    if isinstance(rules, dict):
        required.update(
            field_name
            for field_name, rule in rules.items()
            if isinstance(rule, dict) and _as_bool(rule.get("required"), False)
        )
    return required


def _coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _match_value(match: re.Match[str], field_name: str) -> str | None:
    group_dict = match.groupdict()
    if group_dict:
        value = group_dict.get(field_name) or group_dict.get("value")
        if value:
            return _clean_value(value)
        for item in group_dict.values():
            if item:
                return _clean_value(item)

    if match.groups():
        for item in match.groups():
            if item:
                return _clean_value(item)

    return _clean_value(match.group(0))


def _table_value_after_keyword(line: str, keyword: str) -> str | None:
    if "|" not in line:
        return None

    cells = [_clean_value(cell) for cell in line.strip("|").split("|")]
    for index, cell in enumerate(cells[:-1]):
        if keyword.lower() in cell.lower():
            return cells[index + 1] or None
    return None


def _value_after_keyword(line: str, keyword: str) -> str | None:
    pattern = rf"{re.escape(keyword)}\s*(?:[:#\-–—]|\bis\b)?\s*(?P<value>.+)$"
    match = re.search(pattern, line, flags=re.IGNORECASE)
    if not match:
        return None

    value = _clean_value(match.group("value"))
    if not value or value.lower() == keyword.lower():
        return None
    return value


def _next_non_empty_line(lines: list[str], start_index: int) -> str | None:
    for line in lines[start_index : start_index + 3]:
        value = _clean_value(line)
        if value:
            return value
    return None


def _positional_line_index(coords: dict) -> int | None:
    if isinstance(coords.get("line_index"), int):
        return coords["line_index"]
    if isinstance(coords.get("row"), int):
        return coords["row"]
    if isinstance(coords.get("line"), int):
        return max(coords["line"] - 1, 0)
    return None


def _positional_column_value(line: str, coords: dict) -> str | None:
    column = coords.get("column")
    if not isinstance(column, int):
        return None

    cells = [_clean_value(cell) for cell in line.strip("|").split("|")]
    if 0 <= column < len(cells):
        return cells[column] or None
    return None


def _clean_value(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n|:-–—")
    cleaned = cleaned.strip()
    return cleaned or None


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
