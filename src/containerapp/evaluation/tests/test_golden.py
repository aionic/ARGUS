from evaluation.golden import aggregate_runs, confidence_path, normalize_value, score_document


def test_normalize_value_ignores_common_formatting() -> None:
    assert normalize_value("11/05/21") == normalize_value("11 05 21")
    assert normalize_value("$260.00") == normalize_value(260)


def test_score_document_reports_accuracy_presence_and_confidence() -> None:
    score = score_document(
        {"Invoice Number": "INV-123", "Total": 10.0, "Optional": ""},
        {"Invoice Number": "INV 123", "Total": "10.00", "Optional": "unexpected"},
        {"Invoice_Number": 0.9, "Total": 0.4},
    )

    assert score["metrics"]["exact_accuracy"] == 0
    assert score["metrics"]["normalized_accuracy"] == 1
    assert score["metrics"]["presence_precision"] == 2 / 3
    assert score["metrics"]["presence_recall"] == 1
    assert score["metrics"]["blank_accuracy"] == 0
    assert score["metrics"]["hallucination_rate"] == 1
    fields = {field["path"]: field for field in score["fields"]}
    assert fields["Invoice Number"]["confidence"] == 0.9


def test_confidence_path_matches_content_understanding_sanitization() -> None:
    assert confidence_path("Table.Items[0].Item#") == "Table.Items[0].Item"


def test_hallucination_rate_includes_unknown_array_items() -> None:
    score = score_document(
        {"items": [{"value": "expected"}]},
        {"items": [{"value": "expected"}, {"value": "fabricated"}]},
    )

    assert score["metrics"]["unknown_false_positive_fields"] == 1
    assert score["metrics"]["hallucination_rate"] == 1


def test_aggregate_runs_includes_cost_latency_and_calibration() -> None:
    score = score_document({"A": "x", "B": "y"}, {"A": "x", "B": "wrong"}, {"A": 0.9, "B": 0.8})
    report = aggregate_runs(
        [
            {
                "dataset": "invoice",
                "variant": "baseline",
                "sample": "one",
                "latency_seconds": 2.0,
                "cost": {"total_usd": 0.02},
                "score": score,
            }
        ]
    )

    summary = report["summaries"][0]
    assert summary["normalized_accuracy"] == 0.5
    assert summary["coverage"] == 1
    assert summary["total_cost_usd"] == 0.02
    assert summary["avg_latency_seconds"] == 2
