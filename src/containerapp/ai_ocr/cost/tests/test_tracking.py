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


def _stub_price(model: str, region: str) -> PricingResult:
    return PricingResult(
        model=model,
        region=region,
        input_per_1k=0.002,
        output_per_1k=0.008,
        source="fallback",
    )


def test_record_cu_usage_prices_meters_contextualization_and_llm() -> None:
    from ai_ocr.cost.pricing import CuPricing

    tracker = CostTracker(region="eastus", price_fn=_stub_price)
    cu_pricing = CuPricing(
        region="eastus",
        minimal_per_page=0.001,
        basic_per_page=0.0015,
        standard_per_page=0.005,
        contextualization_per_1k=0.001,
        source="fallback",
    )
    usage = {
        "documentPagesMinimal": 0,
        "documentPagesBasic": 0,
        "documentPagesStandard": 2,
        "contextualizationToken": 2000,
        "tokens": {"gpt-4.1-input": 10400, "gpt-4.1-output": 360},
    }

    breakdown = tracker.record_cu_usage(usage, cu_pricing=cu_pricing)

    # Content extraction (2 standard pages) + contextualization (2K tokens).
    assert breakdown["meter"] == "standard"
    assert breakdown["extraction_usd"] == pytest.approx(0.01)
    assert breakdown["contextualization_usd"] == pytest.approx(0.002)
    assert breakdown["cu_usd"] == pytest.approx(0.012)
    # LLM tokens priced on the Foundry deployment via the stub price fn.
    assert breakdown["llm_tokens"] == {"gpt-4.1": {"input": 10400, "output": 360}}
    assert breakdown["llm_usd"] == pytest.approx(10400 / 1000 * 0.002 + 360 / 1000 * 0.008)
    assert breakdown["total_usd"] == pytest.approx(breakdown["cu_usd"] + breakdown["llm_usd"])

    cost = tracker.aggregate(num_pages=2)
    stages = {entry["stage"] for entry in cost["per_stage"]}
    assert stages == {"content_understanding", "content_understanding_llm"}
    cu_entry = next(e for e in cost["per_stage"] if e["stage"] == "content_understanding")
    assert cu_entry["usd"] == pytest.approx(0.012)
    llm_entry = next(e for e in cost["per_stage"] if e["stage"] == "content_understanding_llm")
    assert llm_entry["input_tokens"] == 10400
    assert llm_entry["output_tokens"] == 360


def test_record_cu_usage_extraction_only_has_no_llm_stage() -> None:
    from ai_ocr.cost.pricing import CuPricing

    tracker = CostTracker(region="eastus", price_fn=_stub_price)
    cu_pricing = CuPricing(
        region="eastus",
        minimal_per_page=0.001,
        basic_per_page=0.0015,
        standard_per_page=0.005,
        contextualization_per_1k=0.001,
        source="fallback",
    )
    usage = {"documentPagesBasic": 3}  # OCR-only, no generative tokens

    breakdown = tracker.record_cu_usage(usage, cu_pricing=cu_pricing)

    assert breakdown["meter"] == "basic"
    assert breakdown["extraction_usd"] == pytest.approx(0.0045)
    assert breakdown["contextualization_usd"] == pytest.approx(0.0)
    assert breakdown["llm_tokens"] == {}
    assert breakdown["llm_usd"] == pytest.approx(0.0)

    cost = tracker.aggregate(num_pages=3)
    assert {e["stage"] for e in cost["per_stage"]} == {"content_understanding"}


def test_record_cu_usage_ignores_empty_usage() -> None:
    tracker = CostTracker(region="eastus", price_fn=_stub_price)

    breakdown = tracker.record_cu_usage({})

    assert breakdown["total_usd"] == pytest.approx(0.0)
    assert tracker.aggregate(num_pages=0)["per_stage"] == []
