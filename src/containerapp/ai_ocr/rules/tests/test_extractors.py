from ai_ocr.rules import reduce_schema, run_extractors


def test_regex_and_keyword_extractors_fill_fields_and_reduce_schema():
    schema = {
        "invoice_id": {},
        "vendor": {},
        "total": {},
        "due_date": {},
    }
    rules = {
        "invoice_id": {
            "type": "regex",
            "pattern": r"Invoice\s*#:\s*(?P<invoice_id>[A-Z0-9-]+)",
            "required": True,
        },
        "vendor": {"type": "keyword", "keywords": ["Vendor"], "required": True},
        "total": {"type": "keyword", "keywords": ["Total"], "required": True},
    }
    ocr_text = """
    Invoice #: INV-1001
    Vendor: Contoso Ltd
    Total: $1,234.56
    """

    result = run_extractors(ocr_text, schema, rules)

    assert result.filled == {
        "invoice_id": "INV-1001",
        "vendor": "Contoso Ltd",
        "total": "$1,234.56",
    }
    assert result.remaining_fields == ["due_date"]
    assert result.all_required_filled
    assert all(score >= 0.70 for score in result.confidence.values())
    assert reduce_schema(schema, result.remaining_fields) == {"due_date": {}}


def test_reduce_schema_handles_json_schema_properties_and_required_fields():
    schema = {
        "type": "object",
        "properties": {
            "invoice_id": {"type": "string"},
            "due_date": {"type": "string"},
            "total": {"type": "string"},
        },
        "required": ["invoice_id", "due_date"],
    }

    reduced = reduce_schema(schema, ["due_date"])

    assert reduced == {
        "type": "object",
        "properties": {"due_date": {"type": "string"}},
        "required": ["due_date"],
    }


def test_positional_extractor_uses_line_index_best_effort():
    schema = {"invoice_id": {}, "customer": {}}
    rules = {
        "customer": {
            "type": "positional",
            "coords": {"line": 2},
            "required": True,
        }
    }
    ocr_text = "Invoice\nCustomer: Fabrikam\nTotal: 25.00"

    result = run_extractors(ocr_text, schema, rules)

    assert result.filled["customer"] == "Fabrikam"
    assert result.remaining_fields == ["invoice_id"]
    assert result.all_required_filled
