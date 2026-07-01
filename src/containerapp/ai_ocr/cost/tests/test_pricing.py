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


def test_get_cu_pricing_uses_fallback_defaults() -> None:
    result = pricing.get_cu_pricing("East US")

    assert result.region == "eastus"
    assert result.minimal_per_page == pytest.approx(0.001)
    assert result.basic_per_page == pytest.approx(0.0015)
    assert result.standard_per_page == pytest.approx(0.005)
    assert result.contextualization_per_1k == pytest.approx(0.001)
    assert result.source == "fallback"
    # page_price() maps meter tier names, defaulting to standard for unknowns.
    assert result.page_price("basic") == pytest.approx(0.0015)
    assert result.page_price("mystery") == pytest.approx(0.005)


def test_get_cu_pricing_honors_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTENT_UNDERSTANDING_STANDARD_PRICE_USD", "0.0075")
    monkeypatch.setenv("CONTENT_UNDERSTANDING_CONTEXTUALIZATION_PRICE_USD_PER_1K", "0.002")

    result = pricing.get_cu_pricing("eastus2")

    assert result.standard_per_page == pytest.approx(0.0075)
    assert result.contextualization_per_1k == pytest.approx(0.002)
    # Untouched meters keep fallback defaults.
    assert result.minimal_per_page == pytest.approx(0.001)
