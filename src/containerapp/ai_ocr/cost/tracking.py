"""Cost aggregation for ARGUS document-extraction stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from .pricing import CuPricing, PricingResult, get_cu_pricing, get_pricing

PricingSource = Literal["azure_retail", "fallback", "mixed"]
PriceFunction = Callable[..., PricingResult | dict[str, Any]]

_CU_METERS = ("minimal", "basic", "standard")


def _classify_cu_call(total_pages: int, contextualization_tokens: int, llm_tokens: dict[str, Any]) -> str:
    """Classify Content Understanding processing depth for cost transparency.

    - ``content_extraction``: OCR/layout only (no field or generative work).
    - ``field_extraction``:   structured field extraction (contextualization
                              tokens) without generative LLM tokens.
    - ``end_to_end``:         content + field extraction + generative LLM (the
                              full ARGUS analyzer path).
    """
    if llm_tokens:
        return "end_to_end"
    if contextualization_tokens:
        return "field_extraction"
    return "content_extraction"


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

    def record_cu_usage(
        self,
        usage: dict[str, Any] | None,
        *,
        stage: str = "content_understanding",
        extraction_model: str = "content-understanding",
        cu_pricing: CuPricing | None = None,
    ) -> dict[str, Any]:
        """Record meter-accurate Content Understanding cost from a CU ``usage`` block.

        The CU ``:analyze`` response reports a ``usage`` object describing exactly
        which content-extraction meter tier each page hit
        (``documentPagesMinimal`` / ``Basic`` / ``Standard``), how many
        ``contextualizationToken`` were consumed, and the generative LLM
        ``tokens`` (billed on the linked Foundry deployment). This prices each
        component per the Content Understanding pricing model:

          * content extraction: pages x per-meter rate
          * contextualization:  tokens / 1K x contextualization rate
          * generative LLM:      input/output tokens priced via :func:`get_pricing`

        Extraction + contextualization are recorded under ``stage`` (default
        ``content_understanding``); LLM tokens are recorded under
        ``"<stage>_llm"`` with the real model name so per-stage token costs stay
        visible. Returns a structured breakdown (meter tier, page/token counts,
        component USD, effective rates) for logging and telemetry.
        """
        usage = usage or {}
        pricing = cu_pricing or get_cu_pricing(self.region, self.pricing_container)

        pages = {meter: _usage_int(usage, f"documentPages{meter.capitalize()}") for meter in _CU_METERS}
        contextualization_tokens = _usage_int(usage, "contextualizationToken") or _usage_int(
            usage, "contextualizationTokens"
        )

        extraction_usd = sum(count * pricing.page_price(meter) for meter, count in pages.items())
        contextualization_usd = contextualization_tokens / 1000 * pricing.contextualization_per_1k
        cu_usd = extraction_usd + contextualization_usd

        total_pages = sum(pages.values())
        if total_pages or contextualization_tokens:
            self._entries.append(
                _CostEntry(
                    stage=stage,
                    model=extraction_model,
                    input_tokens=0,
                    output_tokens=0,
                    usd=cu_usd,
                    source=pricing.source,
                )
            )

        llm_tokens = _parse_llm_tokens(usage.get("tokens"))
        llm_usd = 0.0
        for model, io in llm_tokens.items():
            self.record(f"{stage}_llm", model, io["input"], io["output"])
            llm_usd += self._entries[-1].usd

        # Highest tier actually exercised describes the "level" of CU used.
        meter = next((tier for tier in reversed(_CU_METERS) if pages.get(tier)), None)
        # The call type captures processing depth: content extraction only,
        # structured field extraction, or full end-to-end (generative).
        call_type = _classify_cu_call(total_pages, contextualization_tokens, llm_tokens)

        return {
            "meter": meter,
            "call_type": call_type,
            "pages": {f"{meter}": count for meter, count in pages.items()},
            "total_pages": total_pages,
            "contextualization_tokens": contextualization_tokens,
            "llm_tokens": llm_tokens,
            "extraction_usd": extraction_usd,
            "contextualization_usd": contextualization_usd,
            "llm_usd": llm_usd,
            "cu_usd": cu_usd,
            "total_usd": cu_usd + llm_usd,
            "pricing_source": pricing.source,
            "rates": {
                "minimal_per_page": pricing.minimal_per_page,
                "basic_per_page": pricing.basic_per_page,
                "standard_per_page": pricing.standard_per_page,
                "contextualization_per_1k": pricing.contextualization_per_1k,
            },
        }

    def aggregate(
        self,
        num_pages: int,
        discount_pct: float = 0.0,
        consumption_available: bool = False,
    ) -> dict[str, Any]:
        """Produce the Cost object contract consumed by pipeline integration.

        ``total_usd`` reflects the **net** price the customer pays after applying an
        agreement discount off Azure list price. ``list_total_usd`` always carries the
        undiscounted Azure list price. With ``discount_pct == 0`` the two are equal, so
        existing consumers see no behavioural change. ``consumption_available`` is a
        passthrough flag surfaced in the UI to note Azure consumption (PAYG/commit)
        pricing may further reduce this cost.
        """
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
        list_total_usd = total_usd
        pct = max(0.0, min(float(discount_pct or 0.0), 100.0))
        discount_factor = 1.0 - pct / 100.0
        net_total_usd = list_total_usd * discount_factor
        return {
            "per_stage": [
                {
                    "stage": entry.stage,
                    "model": entry.model,
                    "input_tokens": entry.input_tokens,
                    "output_tokens": entry.output_tokens,
                    "usd": entry.usd * discount_factor,
                    **({"list_usd": entry.usd} if pct > 0 else {}),
                }
                for entry in grouped.values()
            ],
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_usd": net_total_usd,
            "list_total_usd": list_total_usd,
            "discount_pct": pct,
            "consumption_available": bool(consumption_available),
            "usd_per_page": net_total_usd / page_count if page_count > 0 else 0.0,
            "list_usd_per_page": list_total_usd / page_count if page_count > 0 else 0.0,
            "pricing_source": _aggregate_source(sources),
            "model_breakdown": {model: usd * discount_factor for model, usd in model_breakdown.items()},
            **({"list_model_breakdown": model_breakdown} if pct > 0 else {}),
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


def _usage_int(usage: dict[str, Any], key: str) -> int:
    try:
        return int(usage.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _parse_llm_tokens(tokens: Any) -> dict[str, dict[str, int]]:
    """Parse a CU ``usage.tokens`` map into ``{model: {input, output}}``.

    CU reports generative token usage with keys like ``"gpt-4.1-input"`` and
    ``"gpt-4.1-output"``. Unknown suffixes are ignored so a schema change never
    breaks cost tracking.
    """
    result: dict[str, dict[str, int]] = {}
    if not isinstance(tokens, dict):
        return result
    for raw_key, raw_value in tokens.items():
        key = str(raw_key)
        if key.endswith("-input"):
            model, direction = key[: -len("-input")], "input"
        elif key.endswith("-output"):
            model, direction = key[: -len("-output")], "output"
        else:
            continue
        try:
            value = int(raw_value or 0)
        except (TypeError, ValueError):
            value = 0
        entry = result.setdefault(model, {"input": 0, "output": 0})
        entry[direction] += value
    return result


def allocate_cu_usage_by_page(
    breakdown: dict[str, Any],
    *,
    page_start: int,
    page_count: int,
    discount_pct: float = 0.0,
) -> list[dict[str, Any]]:
    """Allocate a CU chunk's priced components equally across its pages.

    Content Understanding reports usage per analyze request, so the caller cannot
    attribute contextualization or generative-token spend to an individual page.
    This helper records that limitation explicitly while producing page records
    that reconcile exactly to the chunk-level usage breakdown.
    """
    if page_count <= 0:
        return []

    list_components = {
        "meter_usd": float(breakdown.get("extraction_usd") or 0.0),
        "contextualization_usd": float(breakdown.get("contextualization_usd") or 0.0),
        "llm_usd": float(breakdown.get("llm_usd") or 0.0),
    }
    discount_factor = 1.0 - max(0.0, min(float(discount_pct or 0.0), 100.0)) / 100.0
    allocated_components = {name: 0.0 for name in list_components}
    metrics: list[dict[str, Any]] = []

    for offset in range(page_count):
        is_final_page = offset == page_count - 1
        allocation: dict[str, float] = {}
        for name, amount in list_components.items():
            share = amount - allocated_components[name] if is_final_page else amount / page_count
            allocation[name] = share
            allocated_components[name] += share

        list_total = sum(allocation.values())

        metrics.append(
            {
                "page_number": page_start + offset,
                "allocation_method": "equal_per_page_within_cu_chunk",
                "meter": breakdown.get("meter"),
                "meter_usage": breakdown.get("pages") or {},
                "list_meter_usd": allocation["meter_usd"],
                "list_contextualization_usd": allocation["contextualization_usd"],
                "list_llm_usd": allocation["llm_usd"],
                "list_estimated_all_in_usd": list_total,
                "allocated_meter_usd": allocation["meter_usd"] * discount_factor,
                "allocated_contextualization_usd": allocation["contextualization_usd"] * discount_factor,
                "allocated_llm_usd": allocation["llm_usd"] * discount_factor,
                "estimated_all_in_usd": list_total * discount_factor,
            }
        )

    return metrics
