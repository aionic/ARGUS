from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

ALLOWED_TIERS = {"economy", "standard", "premium"}
CHEAP_MODEL_DEPLOYMENT = "gpt-4.1-mini"
DEFAULT_PREMIUM_MODEL_DEPLOYMENT = "gpt-4.1"

_DEFAULT_EXTRACTION_MODEL = "__azure_openai_default__"
_DEFAULT_SUMMARY_MODEL = "__summary_default__"

TIER_PRESETS: dict[str, dict[str, Any]] = {
    "economy": {
        "tier": "economy",
        "extraction_model": CHEAP_MODEL_DEPLOYMENT,
        "enable_ocr": True,
        "enable_images": False,
        "enable_evaluation": False,
        "enable_summary": False,
        "summary_model": CHEAP_MODEL_DEPLOYMENT,
        "use_rules_engine": True,
        "enable_preprocessing": False,
    },
    "standard": {
        "tier": "standard",
        "extraction_model": CHEAP_MODEL_DEPLOYMENT,
        "enable_ocr": True,
        "enable_images": True,
        "enable_evaluation": False,
        "enable_summary": False,
        "summary_model": CHEAP_MODEL_DEPLOYMENT,
        "use_rules_engine": True,
        "enable_preprocessing": False,
    },
    "premium": {
        "tier": "premium",
        "extraction_model": _DEFAULT_EXTRACTION_MODEL,
        "enable_ocr": True,
        "enable_images": True,
        "enable_evaluation": True,
        "enable_summary": True,
        "summary_model": _DEFAULT_SUMMARY_MODEL,
        "use_rules_engine": True,
        "enable_preprocessing": False,
    },
}

_OPTION_ALIASES = {
    "include_images": "enable_images",
    "include_ocr": "enable_ocr",
    "model_deployment": "extraction_model",
    "openai_model_deployment": "extraction_model",
    "rules_engine": "use_rules_engine",
    "summary_model_deployment": "summary_model",
}


@dataclass(frozen=True)
class EffectiveConfig:
    """Resolved extraction behavior from tier preset, options, and env."""

    tier: str
    extraction_model: str
    enable_ocr: bool
    enable_images: bool
    enable_evaluation: bool
    enable_summary: bool
    summary_model: str
    use_rules_engine: bool
    enable_preprocessing: bool

    def to_dict(self) -> dict[str, Any]:
        """Return the authoritative config contract as a dict."""
        return asdict(self)


def resolve_effective_config(
    tier: str | None,
    processing_options: dict,
    env: Mapping,
) -> EffectiveConfig:
    """Resolve the authoritative tier/effective-config contract.

    Precedence is tier preset < env defaults < processing options < explicit
    ``tier`` argument. Supported env keys include ``DEFAULT_EXTRACTION_TIER``
    (standard), ``AZURE_OPENAI_MODEL_DEPLOYMENT_NAME``,
    ``SUMMARY_MODEL_DEPLOYMENT_NAME``, ``ENABLE_IMAGE_PREPROCESSING``, and the
    existing ``EXTRACTION_BACKEND`` as a last-resort premium model fallback.
    """
    options = processing_options or {}
    selected_tier = _select_tier(tier, options, env)
    config = copy.deepcopy(TIER_PRESETS[selected_tier])
    config["tier"] = selected_tier

    config["enable_preprocessing"] = _env_bool(
        env,
        "ENABLE_IMAGE_PREPROCESSING",
        "enable_image_preprocessing",
        config["enable_preprocessing"],
    )
    _resolve_model_defaults(config, env)
    _overlay_processing_options(config, options)
    _resolve_model_defaults(config, env)

    return EffectiveConfig(
        tier=str(config["tier"]),
        extraction_model=str(config["extraction_model"]),
        enable_ocr=_as_bool(config["enable_ocr"], True),
        enable_images=_as_bool(config["enable_images"], True),
        enable_evaluation=_as_bool(config["enable_evaluation"], False),
        enable_summary=_as_bool(config["enable_summary"], False),
        summary_model=str(config["summary_model"]),
        use_rules_engine=_as_bool(config["use_rules_engine"], True),
        enable_preprocessing=_as_bool(config["enable_preprocessing"], False),
    )


def _select_tier(tier: str | None, options: dict, env: Mapping) -> str:
    selected = (
        _normalize_tier(tier)
        or _normalize_tier(options.get("tier"))
        or _normalize_tier(options.get("extraction_tier"))
        or _normalize_tier(_env_get(env, "DEFAULT_EXTRACTION_TIER", "default_extraction_tier"))
    )
    return selected or "standard"


def _resolve_model_defaults(config: dict[str, Any], env: Mapping) -> None:
    if config.get("extraction_model") == _DEFAULT_EXTRACTION_MODEL:
        config["extraction_model"] = (
            _env_get(env, "AZURE_OPENAI_MODEL_DEPLOYMENT_NAME", "openai_model_deployment")
            or _env_get(env, "EXTRACTION_BACKEND", "extraction_backend")
            or DEFAULT_PREMIUM_MODEL_DEPLOYMENT
        )

    if config.get("summary_model") == _DEFAULT_SUMMARY_MODEL:
        config["summary_model"] = (
            _env_get(env, "SUMMARY_MODEL_DEPLOYMENT_NAME", "summary_model_deployment") or config["extraction_model"]
        )


def _overlay_processing_options(config: dict[str, Any], options: dict) -> None:
    valid_keys = set(TIER_PRESETS["standard"])
    for raw_key, value in options.items():
        if value is None:
            continue
        key = _OPTION_ALIASES.get(raw_key, raw_key)
        if key in {"tier", "extraction_tier"}:
            continue
        if key in valid_keys:
            config[key] = value


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


def _env_bool(env: Mapping, env_key: str, config_key: str, default: bool) -> bool:
    return _as_bool(_env_get(env, env_key, config_key), default)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
