import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_routes import (  # noqa: E402
    _build_flag_email_context,
    _fallback_email_body,
    _humanize_reason,
    _humanize_reasons,
)

# The raw, technical reason tokens emitted across the processing pipeline.
RAW_REASONS = [
    "low_ocr_confidence (mean 0.42 < 0.60)",
    "high_low_confidence_word_fraction (40% of words < 0.50)",
    "cu_mean_confidence 0.42 < 0.60",
    "paddle_low_confidence (mean 0.42 < 0.60)",
    "paddle_high_low_confidence_fraction (40% of lines < 0.50)",
    "low_quality",
    "low_quality_pages=2/3 (fraction=0.67)",
    "blurry (laplacian=80.0 < 120.0)",
    "too dark (brightness=40.0 < 60.0)",
    "too bright/washed out (brightness=230.0 > 220.0, contrast=15.0 < 25.0)",
    "low contrast (std=12.0 < 25.0)",
    "blank page",
    "low resolution (640x480 < 1000x1000)",
    "failed to load image",
    "quality assessment failed",
    "ocr_text_unreadable_or_empty=12<100",
    "rules_all_required_fields_filled",
]

# Substrings that would indicate a raw internal code/metric leaked into the email.
JARGON_MARKERS = [
    "low_ocr_confidence",
    "high_low_confidence_word_fraction",
    "cu_mean_confidence",
    "paddle_",
    "low_quality_pages",
    "ocr_text_unreadable_or_empty",
    "rules_all_required_fields_filled",
    "laplacian",
    "fraction=",
    "< 0.",
    "_",
]


def _doc_with_reasons(reasons):
    return {
        "id": "default-dataset__invoice.pdf",
        "properties": {"flag": {"flagged": True, "reasons": reasons}},
    }


def test_known_tokens_translate_to_business_language():
    expectations = {
        "low_ocr_confidence (mean 0.42 < 0.60)": "text recognition (OCR) was low",
        "blurry (laplacian=80.0 < 120.0)": "blurry or out of focus",
        "too dark (brightness=40.0 < 60.0)": "too dark",
        "low resolution (640x480 < 1000x1000)": "resolution is too low",
        "ocr_text_unreadable_or_empty=12<100": "little or no readable text",
        "blank page": "appear to be blank",
    }
    for raw, expected in expectations.items():
        assert expected in _humanize_reason(raw)


def test_no_raw_token_or_metric_leaks_for_any_reason():
    for raw in RAW_REASONS:
        friendly = _humanize_reason(raw)
        lowered = friendly.lower()
        for marker in JARGON_MARKERS:
            assert marker not in lowered, f"jargon marker {marker!r} leaked for {raw!r}: {friendly!r}"


def test_unknown_token_is_cleaned_not_passed_through():
    friendly = _humanize_reason("some_future_unmapped_token (detail=1)")
    assert "_" not in friendly
    assert "(" not in friendly
    assert friendly == "Some future unmapped token"


def test_humanize_reasons_deduplicates_collapsed_reasons():
    # Two distinct "blurry" tokens collapse to the same business sentence.
    result = _humanize_reasons(["blurry (laplacian=80 < 120)", "blurry (laplacian=90 < 120)", "blank page"])
    assert len(result) == 2
    assert result == list(dict.fromkeys(result))  # order-preserving, unique


def test_context_reasons_text_is_business_friendly():
    ctx = _build_flag_email_context(
        _doc_with_reasons(["low_ocr_confidence (mean 0.42 < 0.60)", "blurry (laplacian=80 < 120)"])
    )
    reasons_text = ctx["reasons_text"].lower()
    assert "text recognition (ocr) was low" in reasons_text
    assert "blurry" in reasons_text
    for marker in ("low_ocr_confidence", "laplacian", "< 0."):
        assert marker not in reasons_text


def test_context_handles_no_reasons():
    ctx = _build_flag_email_context({"id": "d__f.pdf", "properties": {"flag": {"flagged": True}}})
    assert "No specific reasons" in ctx["reasons"]
    assert "No specific reasons" in ctx["reasons_text"]


def test_fallback_body_uses_plain_language_lead_in():
    body = _fallback_email_body(_doc_with_reasons(["low_ocr_confidence (mean 0.42 < 0.60)"]))
    assert "quality of the scan was too low for the following reasons" in body
    assert "re-upload" in body.lower()
    assert "low_ocr_confidence" not in body
