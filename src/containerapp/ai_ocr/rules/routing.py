from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

ALLOWED_TIERS = {"economy", "standard", "premium"}

DEFAULT_LOW_QUALITY_PAGE_FRACTION = 0.50
DEFAULT_LOW_QUALITY_MIN_PAGES = 1
DEFAULT_MIN_OCR_TEXT_LENGTH = 20
DEFAULT_ECONOMY_MAX_PAGES = 1


@dataclass(frozen=True)
class RoutingDecision:
    """Decision produced before LLM extraction."""

    tier: str
    skip_extraction: bool
    route_to_review: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "tier": self.tier,
            "skip_extraction": self.skip_extraction,
            "route_to_review": self.route_to_review,
            "reasons": list(self.reasons),
        }


def apply_routing(
    doc_properties: dict,
    processing_options: dict,
    env: Mapping,
) -> RoutingDecision:
    """Apply low-quality and unreadable-OCR gates, then choose an extraction tier.

    Thresholds can be supplied in ``processing_options["routing_thresholds"]`` or
    by env vars: ``ROUTING_LOW_QUALITY_PAGE_FRACTION`` (0.5),
    ``ROUTING_LOW_QUALITY_MIN_PAGES`` (1), ``ROUTING_MIN_OCR_TEXT_LENGTH`` (20),
    and ``ROUTING_ECONOMY_MAX_PAGES`` (1).
    """
    properties = doc_properties or {}
    options = processing_options or {}
    reasons: list[str] = []

    thresholds = _routing_thresholds(options, env)
    reports = _image_quality_reports(properties, options)
    num_pages = _num_pages(properties, options, reports)
    ocr_text_length = _ocr_text_length(properties, options)

    low_quality_count = sum(1 for report in reports if _is_low_quality_report(report))
    low_quality_fraction = low_quality_count / max(len(reports), num_pages, 1)
    if low_quality_count and (
        low_quality_count >= thresholds["low_quality_min_pages"]
        or low_quality_fraction >= thresholds["low_quality_page_fraction"]
    ):
        reasons.append(
            "low_quality_pages="
            f"{low_quality_count}/{max(len(reports), num_pages, 1)} "
            f"(fraction={low_quality_fraction:.2f})"
        )

    if _ocr_expected(options) and ocr_text_length is not None and ocr_text_length < thresholds["min_ocr_text_length"]:
        reasons.append(f"ocr_text_unreadable_or_empty={ocr_text_length}<{thresholds['min_ocr_text_length']}")

    route_to_review = bool(reasons)
    rules_all_required_filled = _rules_all_required_filled(properties, options)
    skip_extraction = route_to_review or rules_all_required_filled
    tier = _choose_tier(options, env, route_to_review, num_pages, ocr_text_length, thresholds)

    if rules_all_required_filled:
        reasons.append("rules_all_required_fields_filled")

    return RoutingDecision(
        tier=tier,
        skip_extraction=skip_extraction,
        route_to_review=route_to_review,
        reasons=reasons,
    )


def _routing_thresholds(options: dict, env: Mapping) -> dict[str, float | int]:
    configured = options.get("routing_thresholds") if isinstance(options, dict) else None
    configured = configured if isinstance(configured, dict) else {}

    return {
        "low_quality_page_fraction": _float_setting(
            configured,
            env,
            "low_quality_page_fraction",
            "ROUTING_LOW_QUALITY_PAGE_FRACTION",
            DEFAULT_LOW_QUALITY_PAGE_FRACTION,
        ),
        "low_quality_min_pages": _int_setting(
            configured,
            env,
            "low_quality_min_pages",
            "ROUTING_LOW_QUALITY_MIN_PAGES",
            DEFAULT_LOW_QUALITY_MIN_PAGES,
        ),
        "min_ocr_text_length": _int_setting(
            configured,
            env,
            "min_ocr_text_length",
            "ROUTING_MIN_OCR_TEXT_LENGTH",
            DEFAULT_MIN_OCR_TEXT_LENGTH,
        ),
        "economy_max_pages": _int_setting(
            configured,
            env,
            "economy_max_pages",
            "ROUTING_ECONOMY_MAX_PAGES",
            DEFAULT_ECONOMY_MAX_PAGES,
        ),
    }


def _image_quality_reports(properties: dict, options: dict) -> list[dict]:
    reports = properties.get("image_quality") or options.get("image_quality") or []
    if isinstance(reports, dict):
        return [reports]
    if isinstance(reports, list):
        return [report for report in reports if isinstance(report, dict)]
    return []


def _num_pages(properties: dict, options: dict, reports: list[dict]) -> int:
    for key in ("num_pages", "page_count", "pages"):
        value = properties.get(key, options.get(key))
        parsed = _as_int(value)
        if parsed is not None and parsed > 0:
            return parsed
    return max(len(reports), 1)


def _ocr_text_length(properties: dict, options: dict) -> int | None:
    for source in (properties, options):
        for key in ("ocr_text_length", "ocr_length"):
            parsed = _as_int(source.get(key))
            if parsed is not None:
                return parsed

        for key in ("ocr_text", "ocr_output"):
            value = source.get(key)
            if value is not None:
                return len(str(value).strip())
    return None


def _is_low_quality_report(report: dict) -> bool:
    return any(_as_bool(report.get(key), False) for key in ("flagged_low_quality", "is_low_quality", "low_quality"))


def _ocr_expected(options: dict) -> bool:
    if "enable_ocr" in options:
        return _as_bool(options.get("enable_ocr"), True)
    if "include_ocr" in options:
        return _as_bool(options.get("include_ocr"), True)
    return True


def _rules_all_required_filled(properties: dict, options: dict) -> bool:
    return any(
        _as_bool(source.get(key), False)
        for source in (options, properties)
        for key in ("rules_all_required_filled", "all_required_filled")
    )


def _choose_tier(
    options: dict,
    env: Mapping,
    route_to_review: bool,
    num_pages: int,
    ocr_text_length: int | None,
    thresholds: dict[str, float | int],
) -> str:
    explicit = _normalize_tier(options.get("tier") or options.get("extraction_tier"))
    if explicit:
        return explicit

    default_tier = _normalize_tier(_env_get(env, "DEFAULT_EXTRACTION_TIER", "default_extraction_tier"))
    default_tier = default_tier or "standard"
    ocr_is_readable = ocr_text_length is None or ocr_text_length >= thresholds["min_ocr_text_length"]
    if not route_to_review and num_pages <= thresholds["economy_max_pages"] and ocr_is_readable:
        return "economy"
    return default_tier


def _float_setting(
    configured: dict,
    env: Mapping,
    option_key: str,
    env_key: str,
    default: float,
) -> float:
    value = configured.get(option_key)
    if value is None:
        value = _env_get(env, env_key, option_key)
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _int_setting(
    configured: dict,
    env: Mapping,
    option_key: str,
    env_key: str,
    default: int,
) -> int:
    value = configured.get(option_key)
    if value is None:
        value = _env_get(env, env_key, option_key)
    parsed = _as_int(value)
    return parsed if parsed is not None else default


def _normalize_tier(value: Any) -> str | None:
    if value is None:
        return None
    tier = str(value).strip().lower()
    return tier if tier in ALLOWED_TIERS else None


def _env_get(env: Mapping, env_key: str, config_key: str, default: Any = None) -> Any:
    if env_key in env:
        return env[env_key]
    if config_key in env:
        return env[config_key]
    return default


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
