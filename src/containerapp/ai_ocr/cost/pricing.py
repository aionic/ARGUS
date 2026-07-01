"""Azure Retail Prices API helpers for ARGUS cost tracking.

Token prices are normalized to USD per 1K tokens. Azure OpenAI retail meters
usually publish prices per 1K or 1M tokens; when the unit cannot be parsed, the
retail price is treated as already being per 1K tokens.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import Any, Iterable, Literal, Optional

import httpx

RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
RETAIL_API_VERSION = "2023-01-01-preview"
CACHE_TTL = timedelta(hours=24)


def _use_retail_api() -> bool:
    """Whether to query the live Azure Retail Prices API.

    Honours ``PRICING_USE_RETAIL_API`` (default true). When disabled, pricing
    resolves straight from the bundled static fallback table.
    """
    return os.getenv("PRICING_USE_RETAIL_API", "true").strip().lower() not in ("false", "0", "no")


PriceSource = Literal["azure_retail", "fallback"]


@dataclass(frozen=True)
class PricingResult:
    """Normalized Azure OpenAI token price in USD per 1K tokens."""

    model: str
    region: str
    input_per_1k: float
    output_per_1k: float
    source: PriceSource


@dataclass(frozen=True)
class PagePriceResult:
    """Normalized Document Intelligence price in USD per page."""

    region: str
    usd_per_page: float
    source: PriceSource


@dataclass(frozen=True)
class CuPricing:
    """Azure Content Understanding meter prices.

    Content Understanding bills per *content extraction* meter tier plus a fixed
    *contextualization* token charge (see
    https://learn.microsoft.com/azure/ai-services/content-understanding/pricing-explainer).
    The generative LLM tokens are billed separately on the Foundry model
    deployment and are priced via :func:`get_pricing`.

    Meter tiers (per page):
      * ``minimal``  - digital documents (DOCX/XLSX/HTML/TXT/MSG/EML), no OCR
      * ``basic``    - image-based documents, OCR only (read)
      * ``standard`` - image-based documents, layout analysis (tables/structure)

    ``contextualization_per_1k`` is USD per 1K contextualization tokens (docs
    quote ~1,000 tokens/page at $1 per 1M tokens => $0.001 per 1K).
    """

    region: str
    minimal_per_page: float
    basic_per_page: float
    standard_per_page: float
    contextualization_per_1k: float
    source: PriceSource

    def page_price(self, meter: str) -> float:
        """Return the USD/page rate for a meter tier name."""
        return {
            "minimal": self.minimal_per_page,
            "basic": self.basic_per_page,
            "standard": self.standard_per_page,
        }.get((meter or "").strip().lower(), self.standard_per_page)


_PRICE_CACHE: dict[tuple[str, str], tuple[datetime, PricingResult]] = {}
_DI_CACHE: dict[str, tuple[datetime, PagePriceResult]] = {}
_FALLBACK: Optional[dict[str, Any]] = None
_KNOWN_MODELS = (
    "text-embedding-3-large",
    "gpt-4.1-mini",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4o",
)


def clear_pricing_cache() -> None:
    """Clear in-memory pricing caches, primarily for tests."""
    _PRICE_CACHE.clear()
    _DI_CACHE.clear()


def get_pricing(model: str, region: str, container: Any | None = None) -> PricingResult:
    """Return Azure OpenAI input/output prices for ``model`` and ``region``.

    Prices come from Azure Retail Prices when available, otherwise from the
    bundled static fallback file. ``container`` may be an Azure Cosmos container;
    when supplied, a best-effort 24-hour cache document is read/written without
    coupling this module to the app's global Cosmos client.
    """
    model_key = _canonical_model(model)
    region_key = _normalize_region(region)
    cache_key = (model_key, region_key)

    if cached := _read_memory_price(cache_key):
        return cached
    if cached := _read_cosmos_price(container, _price_doc_id(model_key, region_key)):
        _PRICE_CACHE[cache_key] = (datetime.now(UTC), cached)
        return cached

    try:
        if not _use_retail_api():
            result = _fallback_pricing(model_key, region_key)
        else:
            items = _fetch_retail_items(_cognitive_services_filter(region_key))
            parsed = _parse_token_prices_from_items(items, model_key)
            if parsed is not None:
                result = PricingResult(
                    model=model_key,
                    region=region_key,
                    input_per_1k=parsed[0],
                    output_per_1k=parsed[1],
                    source="azure_retail",
                )
            else:
                result = _fallback_pricing(model_key, region_key)
    except Exception:  # noqa: BLE001 - pricing should never break extraction
        result = _fallback_pricing(model_key, region_key)

    _PRICE_CACHE[cache_key] = (datetime.now(UTC), result)
    _write_cosmos_price(container, _price_doc_id(model_key, region_key), result)
    return result


def di_page_price(region: str, container: Any | None = None) -> float:
    """Return Azure Document Intelligence OCR price in USD per page."""
    return get_di_page_pricing(region, container=container).usd_per_page


def get_di_page_pricing(region: str, container: Any | None = None) -> PagePriceResult:
    """Return Azure Document Intelligence OCR pricing with source metadata."""
    region_key = _normalize_region(region)
    if cached := _read_memory_di(region_key):
        return cached
    if cached := _read_cosmos_di(container, _di_doc_id(region_key)):
        _DI_CACHE[region_key] = (datetime.now(UTC), cached)
        return cached

    try:
        if not _use_retail_api():
            result = _fallback_di_pricing(region_key)
        else:
            items = _fetch_retail_items(_cognitive_services_filter(region_key))
            parsed = _parse_di_page_price_from_items(items)
            result = (
                PagePriceResult(region=region_key, usd_per_page=parsed, source="azure_retail")
                if parsed is not None
                else _fallback_di_pricing(region_key)
            )
    except Exception:  # noqa: BLE001 - pricing should never break extraction
        result = _fallback_di_pricing(region_key)

    _DI_CACHE[region_key] = (datetime.now(UTC), result)
    _write_cosmos_di(container, _di_doc_id(region_key), result)
    return result


def get_cu_pricing(region: str, container: Any | None = None) -> CuPricing:
    """Return Azure Content Understanding meter prices for ``region``.

    Content Understanding page meters are not reliably exposed by the Azure
    Retail Prices API, so this resolves from environment overrides first, then
    the bundled fallback table. Environment overrides (USD):

      * ``CONTENT_UNDERSTANDING_MINIMAL_PRICE_USD``   - per page (minimal meter)
      * ``CONTENT_UNDERSTANDING_BASIC_PRICE_USD``     - per page (basic meter)
      * ``CONTENT_UNDERSTANDING_STANDARD_PRICE_USD``  - per page (standard meter)
      * ``CONTENT_UNDERSTANDING_CONTEXTUALIZATION_PRICE_USD_PER_1K`` - per 1K tokens

    ``container`` is accepted for signature parity with the other pricing
    resolvers but is unused (the values are static/env-driven).
    """
    _ = container
    region_key = _normalize_region(region)
    fallback = _load_fallback().get("content_understanding", {})
    minimal = _env_price("CONTENT_UNDERSTANDING_MINIMAL_PRICE_USD", fallback.get("minimal_per_page", 0.001))
    basic = _env_price("CONTENT_UNDERSTANDING_BASIC_PRICE_USD", fallback.get("basic_per_page", 0.0015))
    standard = _env_price("CONTENT_UNDERSTANDING_STANDARD_PRICE_USD", fallback.get("standard_per_page", 0.005))
    contextualization = _env_price(
        "CONTENT_UNDERSTANDING_CONTEXTUALIZATION_PRICE_USD_PER_1K",
        fallback.get("contextualization_per_1k_tokens", 0.001),
    )
    return CuPricing(
        region=region_key,
        minimal_per_page=minimal,
        basic_per_page=basic,
        standard_per_page=standard,
        contextualization_per_1k=contextualization,
        source="fallback",
    )


def _env_price(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return float(default)


def _fetch_retail_items(filter_expr: str, timeout: float = 15.0) -> list[dict[str, Any]]:
    """Fetch all Retail Prices items for a filter, following NextPageLink."""
    items: list[dict[str, Any]] = []
    url: str | None = RETAIL_PRICES_URL
    params: dict[str, str] | None = {"api-version": RETAIL_API_VERSION, "$filter": filter_expr}
    with httpx.Client(timeout=timeout) as client:
        while url:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            items.extend(payload.get("Items") or [])
            url = payload.get("NextPageLink")
            params = None
    return items


def _parse_token_prices_from_items(items: Iterable[dict[str, Any]], model: str) -> tuple[float, float] | None:
    """Parse Azure OpenAI input/output token meters from Retail Prices items."""
    model_key = _canonical_model(model)
    input_price: float | None = None
    output_price: float | None = None

    for item in items:
        if not _is_usd(item) or not _is_model_match(item, model_key):
            continue
        direction = _token_direction(item)
        if direction is None:
            continue
        price = _normalize_token_price(item)
        if price is None:
            continue
        if direction == "input":
            input_price = price if input_price is None else min(input_price, price)
        else:
            output_price = price if output_price is None else min(output_price, price)

    if input_price is None:
        return None
    if output_price is None:
        if model_key.startswith("text-embedding"):
            output_price = 0.0
        else:
            return None
    return input_price, output_price


def _parse_di_page_price_from_items(items: Iterable[dict[str, Any]]) -> float | None:
    """Parse Document Intelligence/Form Recognizer page meters from Retail Prices items."""
    best_price: float | None = None
    for item in items:
        if not _is_usd(item):
            continue
        text = _item_text(item)
        if not any(term in text for term in ("document intelligence", "form recognizer")):
            continue
        if "page" not in text or any(term in text for term in ("commitment", "free")):
            continue
        price = _normalize_page_price(item)
        if price is None:
            continue
        best_price = price if best_price is None else min(best_price, price)
    return best_price


def _cognitive_services_filter(region: str) -> str:
    parts = ["serviceName eq 'Cognitive Services'", "currencyCode eq 'USD'"]
    if region:
        parts.append(f"armRegionName eq '{region}'")
    return " and ".join(parts)


def _normalize_region(region: str) -> str:
    return (region or "global").strip().lower().replace(" ", "")


def _canonical_model(model: str) -> str:
    normalized = (model or "default").strip().lower()
    normalized_compact = _compact(normalized)
    for known in _KNOWN_MODELS:
        if _compact(known) in normalized_compact:
            return known
    return normalized or "default"


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _item_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(name) or "").lower()
        for name in ("meterName", "productName", "skuName", "serviceName", "unitOfMeasure")
    )


def _is_usd(item: dict[str, Any]) -> bool:
    return str(item.get("currencyCode") or "USD").upper() == "USD"


def _is_model_match(item: dict[str, Any], model: str) -> bool:
    text = _compact(_item_text(item))
    model_compact = _compact(model)
    if model_compact in text:
        return True
    return model == "default" and any(_compact(known) in text for known in _KNOWN_MODELS)


def _token_direction(item: dict[str, Any]) -> Literal["input", "output"] | None:
    text = _item_text(item)
    if any(term in text for term in ("cached", "cache", "batch", "training", "fine tuning")):
        return None
    if re.search(r"\b(input|inp|prompt)\b", text):
        return "input"
    if re.search(r"\b(output|outp|completion)\b", text):
        return "output"
    return None


def _normalize_token_price(item: dict[str, Any]) -> float | None:
    price = _item_price(item)
    if price is None:
        return None
    token_count = _unit_count(str(item.get("unitOfMeasure") or ""), default=1000)
    return price * 1000 / token_count


def _normalize_page_price(item: dict[str, Any]) -> float | None:
    price = _item_price(item)
    if price is None:
        return None
    page_count = _unit_count(str(item.get("unitOfMeasure") or ""), default=1)
    return price / page_count


def _item_price(item: dict[str, Any]) -> float | None:
    for field in ("retailPrice", "unitPrice"):
        value = item.get(field)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _unit_count(unit: str, default: int) -> int:
    normalized = unit.lower().replace(",", "")
    if "1m" in normalized or "million" in normalized or "1000000" in normalized:
        return 1_000_000
    if "1k" in normalized or "thousand" in normalized or "1000" in normalized:
        return 1_000
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:tokens?|pages?)", normalized)
    if match:
        return max(int(float(match.group(1))), 1)
    return default


def _fallback_pricing(model: str, region: str) -> PricingResult:
    fallback = _load_fallback()["models"]
    values = fallback.get(model) or fallback["default"]
    return PricingResult(
        model=model,
        region=region,
        input_per_1k=float(values["input_per_1k"]),
        output_per_1k=float(values["output_per_1k"]),
        source="fallback",
    )


def _fallback_di_pricing(region: str) -> PagePriceResult:
    values = _load_fallback()["document_intelligence"]
    return PagePriceResult(region=region, usd_per_page=float(values["default_page"]), source="fallback")


def _load_fallback() -> dict[str, Any]:
    global _FALLBACK
    if _FALLBACK is None:
        data = resources.files(__package__).joinpath("pricing_fallback.json").read_text(encoding="utf-8")
        _FALLBACK = json.loads(data)
    return _FALLBACK


def _read_memory_price(cache_key: tuple[str, str]) -> PricingResult | None:
    cached = _PRICE_CACHE.get(cache_key)
    if not cached:
        return None
    timestamp, result = cached
    if datetime.now(UTC) - timestamp > CACHE_TTL:
        _PRICE_CACHE.pop(cache_key, None)
        return None
    return result


def _read_memory_di(region: str) -> PagePriceResult | None:
    cached = _DI_CACHE.get(region)
    if not cached:
        return None
    timestamp, result = cached
    if datetime.now(UTC) - timestamp > CACHE_TTL:
        _DI_CACHE.pop(region, None)
        return None
    return result


def _price_doc_id(model: str, region: str) -> str:
    return f"aoai-price:{region}:{model}".replace("/", "-")


def _di_doc_id(region: str) -> str:
    return f"di-page-price:{region}".replace("/", "-")


def _read_cosmos_price(container: Any | None, doc_id: str) -> PricingResult | None:
    doc = _read_cosmos_doc(container, doc_id)
    if not doc:
        return None
    try:
        return PricingResult(
            model=str(doc["model"]),
            region=str(doc["region"]),
            input_per_1k=float(doc["input_per_1k"]),
            output_per_1k=float(doc["output_per_1k"]),
            source=doc["source"],
        )
    except Exception:  # noqa: BLE001 - bad cache document should be ignored
        return None


def _read_cosmos_di(container: Any | None, doc_id: str) -> PagePriceResult | None:
    doc = _read_cosmos_doc(container, doc_id)
    if not doc:
        return None
    try:
        return PagePriceResult(region=str(doc["region"]), usd_per_page=float(doc["usd_per_page"]), source=doc["source"])
    except Exception:  # noqa: BLE001 - bad cache document should be ignored
        return None


def _read_cosmos_doc(container: Any | None, doc_id: str) -> dict[str, Any] | None:
    if container is None:
        return None
    try:
        doc = container.read_item(item=doc_id, partition_key=doc_id)
        updated_at = datetime.fromisoformat(str(doc.get("updated_at")))
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        if datetime.now(UTC) - updated_at <= CACHE_TTL:
            return doc
    except Exception:  # noqa: BLE001 - cache miss/SDK variance should not fail pricing
        return None
    return None


def _write_cosmos_price(container: Any | None, doc_id: str, result: PricingResult) -> None:
    doc = {
        "id": doc_id,
        "kind": "aoai_token_price",
        "model": result.model,
        "region": result.region,
        "input_per_1k": result.input_per_1k,
        "output_per_1k": result.output_per_1k,
        "source": result.source,
        "updated_at": datetime.now(UTC).isoformat(),
        "ttl": int(CACHE_TTL.total_seconds()),
    }
    _write_cosmos_doc(container, doc)


def _write_cosmos_di(container: Any | None, doc_id: str, result: PagePriceResult) -> None:
    doc = {
        "id": doc_id,
        "kind": "di_page_price",
        "region": result.region,
        "usd_per_page": result.usd_per_page,
        "source": result.source,
        "updated_at": datetime.now(UTC).isoformat(),
        "ttl": int(CACHE_TTL.total_seconds()),
    }
    _write_cosmos_doc(container, doc)


def _write_cosmos_doc(container: Any | None, doc: dict[str, Any]) -> None:
    if container is None:
        return
    try:
        container.upsert_item(doc)
    except Exception:  # noqa: BLE001 - cache write failures should not fail pricing
        return
