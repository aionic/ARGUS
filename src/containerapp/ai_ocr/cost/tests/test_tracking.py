import pytest

from ai_ocr.cost.pricing import PricingResult
from ai_ocr.cost.tracking import CostTracker


def test_cost_tracker_aggregates_tokens_pages_and_sources() -> None:
    def stub_price(model: str, region: str) -> PricingResult:
        return PricingResult(
            model=model,
            region=region,
            input_per_1k=0.001,
            output_per_1k=0.002,
            source="azure_retail",
        )

    tracker = CostTracker(region="eastus", price_fn=stub_price)
    tracker.record("extraction", "gpt-test", 1000, 500)
    tracker.record("extraction", "gpt-test", 500, 500)
    tracker.record("summary", "gpt-test", 2000, 1000)
    tracker.record_pages("ocr", "document-intelligence", 2, 0.003)

    cost = tracker.aggregate(num_pages=2)

    assert cost["total_input_tokens"] == 3500
    assert cost["total_output_tokens"] == 2000
    assert cost["total_usd"] == pytest.approx(0.0105)
    assert cost["usd_per_page"] == pytest.approx(0.00525)
    assert cost["pricing_source"] == "mixed"
    assert cost["model_breakdown"] == {
        "gpt-test": pytest.approx(0.0075),
        "document-intelligence": pytest.approx(0.003),
    }
    assert cost["per_stage"] == [
        {
            "stage": "extraction",
            "model": "gpt-test",
            "input_tokens": 1500,
            "output_tokens": 1000,
            "usd": pytest.approx(0.0035),
        },
        {
            "stage": "summary",
            "model": "gpt-test",
            "input_tokens": 2000,
            "output_tokens": 1000,
            "usd": pytest.approx(0.004),
        },
        {
            "stage": "ocr",
            "model": "document-intelligence",
            "input_tokens": 0,
            "output_tokens": 0,
            "usd": pytest.approx(0.003),
        },
    ]
