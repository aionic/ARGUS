import pytest

from ai_ocr.cost import pricing


def test_get_pricing_uses_fallback_when_retail_api_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    pricing.clear_pricing_cache()

    def fail_fetch(_filter_expr: str) -> list[dict[str, object]]:
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(pricing, "_fetch_retail_items", fail_fetch)

    result = pricing.get_pricing("argus-gpt-4o-mini-prod", "East US")

    assert result.model == "gpt-4o-mini"
    assert result.region == "eastus"
    assert result.input_per_1k == pytest.approx(0.00015)
    assert result.output_per_1k == pytest.approx(0.0006)
    assert result.source == "fallback"


def test_parse_retail_token_prices_normalizes_units() -> None:
    payload = {
        "Items": [
            {
                "currencyCode": "USD",
                "serviceName": "Cognitive Services",
                "productName": "Azure OpenAI GPT 4.1 Mini",
                "skuName": "Global",
                "meterName": "GPT 4.1 Mini Input Tokens",
                "unitOfMeasure": "1K Tokens",
                "retailPrice": 0.0004,
            },
            {
                "currencyCode": "USD",
                "serviceName": "Cognitive Services",
                "productName": "Azure OpenAI GPT 4.1 Mini",
                "skuName": "Global",
                "meterName": "GPT 4.1 Mini Output Tokens",
                "unitOfMeasure": "1M Tokens",
                "retailPrice": 1.6,
            },
        ]
    }

    parsed = pricing._parse_token_prices_from_items(payload["Items"], "gpt-4.1-mini")

    assert parsed == pytest.approx((0.0004, 0.0016))


def test_parse_retail_di_price_normalizes_pages() -> None:
    payload = {
        "Items": [
            {
                "currencyCode": "USD",
                "serviceName": "Cognitive Services",
                "productName": "Azure AI Document Intelligence",
                "skuName": "Standard",
                "meterName": "Document Intelligence Read Pages",
                "unitOfMeasure": "1K Pages",
                "retailPrice": 1.5,
            }
        ]
    }

    parsed = pricing._parse_di_page_price_from_items(payload["Items"])

    assert parsed == pytest.approx(0.0015)
