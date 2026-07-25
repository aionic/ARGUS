"""Tests for Content Understanding usage capture in the CU client."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_ocr.azure import content_understanding as cu  # noqa: E402


def test_extract_usage_top_level() -> None:
    payload = {"result": {"contents": []}, "usage": {"documentPagesStandard": 2}}
    assert cu._extract_usage(payload) == {"documentPagesStandard": 2}


def test_extract_usage_nested_under_result() -> None:
    payload = {"result": {"contents": [], "usage": {"documentPagesBasic": 1}}}
    assert cu._extract_usage(payload) == {"documentPagesBasic": 1}


def test_extract_usage_absent_returns_empty() -> None:
    assert cu._extract_usage({"result": {"contents": []}}) == {}


def test_dominant_meter_prefers_highest_tier() -> None:
    assert cu._dominant_meter({"documentPagesMinimal": 5, "documentPagesStandard": 1}) == "standard"
    assert cu._dominant_meter({"documentPagesBasic": 3}) == "basic"
    assert cu._dominant_meter({"documentPagesMinimal": 4}) == "minimal"
    assert cu._dominant_meter({}) is None


def test_normalize_result_surfaces_usage() -> None:
    payload = {
        "result": {
            "contents": [{"markdown": "hello", "fields": {}}],
        },
        "usage": {
            "documentPagesStandard": 2,
            "contextualizationToken": 2000,
            "tokens": {"gpt-4.1-input": 100, "gpt-4.1-output": 10},
        },
    }

    result = cu._normalize_result(payload, example_schema={})

    assert result["usage"]["documentPagesStandard"] == 2
    assert result["usage"]["tokens"] == {"gpt-4.1-input": 100, "gpt-4.1-output": 10}
    assert result["ocr_output"] == "hello"


def test_schema_guidance_applies_descriptions_methods_and_selective_confidence() -> None:
    schema = {"Invoice Number": "", "Table": {"Total": 0.0}}
    options = {
        "humanize_field_names": True,
        "confidence_fields": ["Invoice Number"],
        "field_hints": {
            "Table.Total": {
                "description": "Final invoice amount payable.",
                "method": "extract",
            }
        },
    }

    field_schema = cu._build_field_schema("invoice", schema, options)

    invoice_number = field_schema["fields"]["Invoice_Number"]
    assert invoice_number["description"] == "Invoice Number"
    assert invoice_number["method"] == "extract"
    assert invoice_number["estimateSourceAndConfidence"] is True
    total = field_schema["fields"]["Table"]["properties"]["Total"]
    assert total["description"] == "Final invoice amount payable."
    assert total["method"] == "extract"
    assert total["estimateSourceAndConfidence"] is True


def test_analyzer_id_changes_with_processing_configuration() -> None:
    settings = {"base_analyzer_id": "prebuilt-document"}
    field_schema = cu._build_field_schema("invoice", {"Total": 0.0})
    baseline = cu._build_analyzer_definition(settings, field_schema)
    cheaper = cu._build_analyzer_definition(settings, field_schema, {"config": {"enableLayout": False}})

    assert cu._analyzer_id("invoice", baseline) != cu._analyzer_id("invoice", cheaper)


def test_long_field_names_are_stable_and_within_cu_limit() -> None:
    original_name = "qualifying_event_spouse_changes_from_full_time_to_part_time_employment"
    field_schema = cu._build_field_schema("enrollment", {original_name: ""})
    sanitized_name = next(iter(field_schema["fields"]))

    assert len(sanitized_name) == 64
    assert sanitized_name == cu.sanitize_cu_field_name(original_name)
    assert cu._normalize_object(
        {sanitized_name: {"type": "string", "valueString": "Yes"}},
        {original_name: ""},
    ) == {original_name: "Yes"}
