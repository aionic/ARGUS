"""Cost aggregation for ARGUS document-extraction stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from .pricing import PricingResult, get_pricing

PricingSource = Literal["azure_retail", "fallback", "mixed"]
PriceFunction = Callable[..., PricingResult | dict[str, Any]]


@dataclass
class _CostEntry:
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    usd: float
    source: str


class CostTracker:
    """Records and aggregates token/page costs for ARGUS pipeline stages."""

    def __init__(
        self,
        region: str = "eastus",
        price_fn: PriceFunction | None = None,
        pricing_container: Any | None = None,
    ) -> None:
        self.region = region
        self.price_fn = price_fn or get_pricing
        self.pricing_container = pricing_container
        self._entries: list[_CostEntry] = []

    def record(self, stage: str, model: str, input_tokens: int | None, output_tokens: int | None) -> None:
        """Record token usage and compute USD using the configured price function."""
        in_tokens = int(input_tokens or 0)
        out_tokens = int(output_tokens or 0)
        price = self._resolve_price(model)
        usd = (in_tokens / 1000 * price.input_per_1k) + (out_tokens / 1000 * price.output_per_1k)
        self._entries.append(
            _CostEntry(
                stage=stage,
                model=price.model or model,
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                usd=usd,
                source=price.source,
            )
        )

    def record_pages(self, stage: str, model: str, pages: int, usd: float) -> None:
        """Record OCR/content-understanding page or flat USD costs."""
        _ = pages
        self._entries.append(
            _CostEntry(
                stage=stage,
                model=model,
                input_tokens=0,
                output_tokens=0,
                usd=float(usd or 0),
                source="fallback",
            )
        )

    def aggregate(self, num_pages: int) -> dict[str, Any]:
        """Produce the Cost object contract consumed by pipeline integration."""
        grouped: dict[tuple[str, str], _CostEntry] = {}
        model_breakdown: dict[str, float] = {}
        total_input_tokens = 0
        total_output_tokens = 0
        total_usd = 0.0
        sources: list[str] = []

        for entry in self._entries:
            key = (entry.stage, entry.model)
            if key not in grouped:
                grouped[key] = _CostEntry(entry.stage, entry.model, 0, 0, 0.0, entry.source)
            grouped_entry = grouped[key]
            grouped_entry.input_tokens += entry.input_tokens
            grouped_entry.output_tokens += entry.output_tokens
            grouped_entry.usd += entry.usd
            grouped_entry.source = _combine_sources(grouped_entry.source, entry.source)

            total_input_tokens += entry.input_tokens
            total_output_tokens += entry.output_tokens
            total_usd += entry.usd
            model_breakdown[entry.model] = model_breakdown.get(entry.model, 0.0) + entry.usd
            sources.append(entry.source)

        page_count = int(num_pages or 0)
        return {
            "per_stage": [
                {
                    "stage": entry.stage,
                    "model": entry.model,
                    "input_tokens": entry.input_tokens,
                    "output_tokens": entry.output_tokens,
                    "usd": entry.usd,
                }
                for entry in grouped.values()
            ],
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_usd": total_usd,
            "usd_per_page": total_usd / page_count if page_count > 0 else 0.0,
            "pricing_source": _aggregate_source(sources),
            "model_breakdown": model_breakdown,
        }

    def _resolve_price(self, model: str) -> PricingResult:
        try:
            raw = self.price_fn(model=model, region=self.region, container=self.pricing_container)
        except TypeError:
            raw = self.price_fn(model, self.region)
        if isinstance(raw, PricingResult):
            return raw
        return PricingResult(
            model=str(raw.get("model") or model),
            region=str(raw.get("region") or self.region),
            input_per_1k=float(raw["input_per_1k"]),
            output_per_1k=float(raw["output_per_1k"]),
            source=str(raw.get("source") or "fallback"),  # type: ignore[arg-type]
        )


def _aggregate_source(sources: list[str]) -> PricingSource:
    normalized = {source for source in sources if source in {"azure_retail", "fallback"}}
    if not normalized:
        return "fallback"
    if normalized == {"azure_retail"}:
        return "azure_retail"
    if normalized == {"fallback"}:
        return "fallback"
    return "mixed"


def _combine_sources(first: str, second: str) -> str:
    return first if first == second else "mixed"
