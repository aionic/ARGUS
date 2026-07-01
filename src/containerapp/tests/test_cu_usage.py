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
