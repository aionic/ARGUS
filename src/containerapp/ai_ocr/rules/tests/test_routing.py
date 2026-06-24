from ai_ocr.rules import apply_routing


def test_routing_sends_low_quality_docs_to_review_and_skips_extraction():
    properties = {
        "num_pages": 2,
        "ocr_text": "Readable invoice text with enough characters.",
        "image_quality": [
            {"flagged_low_quality": True, "blur_score": 12.0, "brightness": 240.0},
            {"flagged_low_quality": False, "blur_score": 180.0, "brightness": 120.0},
        ],
    }

    decision = apply_routing(properties, {}, {})

    assert decision.route_to_review
    assert decision.skip_extraction
    assert decision.tier == "standard"
    assert any("low_quality_pages=1/2" in reason for reason in decision.reasons)


def test_routing_chooses_economy_for_clean_single_page_doc():
    properties = {
        "num_pages": 1,
        "ocr_text": "Clean single-page invoice text that is readable.",
        "image_quality": [{"flagged_low_quality": False}],
    }

    decision = apply_routing(properties, {}, {"DEFAULT_EXTRACTION_TIER": "premium"})

    assert not decision.route_to_review
    assert not decision.skip_extraction
    assert decision.tier == "economy"
    assert decision.reasons == []


def test_routing_empty_ocr_routes_to_review_when_ocr_is_expected():
    decision = apply_routing({"num_pages": 1, "ocr_text": ""}, {}, {})

    assert decision.route_to_review
    assert decision.skip_extraction
    assert any("ocr_text_unreadable_or_empty=0<20" in reason for reason in decision.reasons)


def test_routing_skips_when_rules_already_filled_required_fields():
    decision = apply_routing(
        {"num_pages": 1, "ocr_text": "Readable invoice text.", "rules_all_required_filled": True},
        {},
        {},
    )

    assert decision.skip_extraction
    assert not decision.route_to_review
    assert "rules_all_required_fields_filled" in decision.reasons
