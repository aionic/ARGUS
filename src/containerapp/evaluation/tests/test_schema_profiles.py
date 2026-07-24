from evaluation.corpus import ConduentCorpus
from evaluation.schema_profiles import (
    adapt_confidence,
    adapt_extracted_data,
    build_analyzer_options,
    build_example_schema,
    json_schema_to_example,
)


def test_cms_schema_profile_adds_semantic_guidance() -> None:
    corpus = ConduentCorpus()
    schema = corpus.schemas["cms1500-v1"]

    example = json_schema_to_example(schema)
    options = build_analyzer_options("cms1500", schema, "selective-confidence")

    assert len(example) == 198
    assert options["field_hints"]["claim.od_pat_name_1"]["description"].startswith("Patient full name")
    assert "claim.od_total_chg_1" in options["confidence_fields"]


def test_cms_guided_contract_groups_service_lines_and_maps_back() -> None:
    corpus = ConduentCorpus()
    schema = corpus.schemas["cms1500-v1"]
    flat = json_schema_to_example(schema)
    semantic = build_example_schema("cms1500", schema, "guided")

    assert "od_pat_name_1" in semantic["claim"]
    assert "code" in semantic["service_lines"][0]
    assert "od_code_1" not in semantic["claim"]

    canonical = adapt_extracted_data(
        "cms1500",
        "guided",
        {
            "claim": {"od_pat_name_1": "PATIENT"},
            "service_lines": [{"code": "99213", "charges": 125.0}],
        },
        flat,
    )
    confidence = adapt_confidence(
        "cms1500",
        "guided",
        {"claim.od_pat_name_1": 0.9, "service_lines[0].code": 0.8},
        flat,
    )

    assert canonical["od_pat_name_1"] == "PATIENT"
    assert canonical["od_code_1"] == "99213"
    assert confidence == {"od_pat_name_1": 0.9, "od_code_1": 0.8}


def test_commercial_schema_profile_narrows_amount_types() -> None:
    corpus = ConduentCorpus()
    schema = corpus.schemas["commercial-document-v1"]

    options = build_analyzer_options("commercial-documents", schema, "guided")

    assert options["field_hints"]["invoice_total"]["type"] == "number"
    assert options["field_hints"]["invoice_number"]["type"] == "string"


def test_enrollment_profile_preserves_dependent_array() -> None:
    corpus = ConduentCorpus()
    schema = corpus.schemas["enrollment-v1"]

    example = json_schema_to_example(schema)

    assert isinstance(example["dependents"], list)


def test_invoice_selective_profile_targets_critical_fields() -> None:
    corpus = ConduentCorpus()
    schema = corpus.schemas["invoice-demo-v1"]

    options = build_analyzer_options("invoice-demo", schema, "selective-confidence")

    assert "Invoice Number" in options["confidence_fields"]
    assert "Table.Total" in options["confidence_fields"]
