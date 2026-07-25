"""Azure AI Content Understanding extraction backend.

Content Understanding (CU) runs in *full-analyzer* mode for ARGUS: a single
``:analyze`` call performs OCR **and** schema-based field extraction, replacing
both the OCR and GPT-extraction stages for datasets that select it.

Flow
----
1. Build (or reuse) a custom analyzer whose ``fieldSchema`` is derived from the
   dataset's ARGUS example-JSON schema. Analyzers are addressed by a stable id
   that embeds a hash of the schema, so a schema change creates a fresh analyzer.
2. POST the document (base64-encoded) to ``{analyzerId}:analyze`` and poll the
   returned ``Operation-Location`` until the operation succeeds.
3. Normalize CU's typed ``fields`` + ``markdown`` back into the ARGUS document
   shape (``gpt_extraction_output`` + ``ocr_output``) plus a flat confidence map.

Auth is Microsoft Entra ID (managed identity) using the
``https://cognitiveservices.azure.com/.default`` scope — the same token provider
already used for Azure OpenAI.

REST API: Content Understanding ``2025-11-01`` (GA).
"""

import base64
import fnmatch
import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import httpx

from ai_ocr.azure.config import get_config

logger = logging.getLogger(__name__)

# Field name accepted by CU analyzer field schema.
_NAME_RE = re.compile(r"[^a-zA-Z0-9_]")
# Analyzer id pattern: ^[a-zA-Z0-9._]{1,64}$ (hyphens are NOT permitted).
_ANALYZER_ID_RE = re.compile(r"[^a-zA-Z0-9._]")
# Bump when the analyzer build contract changes (base analyzer, config, schema
# mapping) so a fresh analyzer id is used instead of a previously failed one.
_ANALYZER_VERSION = "v3"

# Cache of analyzer ids we have already confirmed exist (per process).
_ready_analyzers: set[str] = set()

_DEFAULT_TIMEOUT = httpx.Timeout(300.0, connect=30.0)
_POLL_INTERVAL_SEC = 2.0
_POLL_MAX_SEC = 600.0


# ─────────────────────────────────────────────────────────────────────────────
# Config / auth helpers
# ─────────────────────────────────────────────────────────────────────────────
def _cu_settings(cosmos_config_container=None) -> dict[str, Any]:
    config = get_config(cosmos_config_container)
    endpoint = config.get("content_understanding_endpoint")
    if not endpoint:
        raise ValueError(
            "Content Understanding endpoint is not configured. "
            "Set AZURE_CONTENT_UNDERSTANDING_ENDPOINT to the Foundry/AIServices account endpoint."
        )
    return {
        "endpoint": endpoint.rstrip("/"),
        "api_version": config.get("content_understanding_api_version", "2025-11-01"),
        "token_provider": config["azure_openai_token_provider"],
        # Optional explicit model deployments for the analyzer. When unset, the
        # CU resource-level defaults are used.
        "completion_model": os.getenv("CONTENT_UNDERSTANDING_COMPLETION_MODEL"),
        "embedding_model": os.getenv("CONTENT_UNDERSTANDING_EMBEDDING_MODEL"),
        "base_analyzer_id": os.getenv("CONTENT_UNDERSTANDING_BASE_ANALYZER", "prebuilt-document"),
    }


def _headers(settings: dict[str, Any], *, json_body: bool = True) -> dict[str, str]:
    token = settings["token_provider"]()
    headers = {"Authorization": f"Bearer {token}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


# ─────────────────────────────────────────────────────────────────────────────
# Schema mapping: ARGUS example-JSON  ->  CU fieldSchema
# ─────────────────────────────────────────────────────────────────────────────
_FIELD_NAME_MAX_LENGTH = 64


def sanitize_cu_field_name(name: str) -> str:
    """Convert an ARGUS field name to a CU-safe field key."""
    safe = _NAME_RE.sub("_", name).strip("_")
    if not safe:
        safe = "field"
    if not safe[0].isalpha():
        safe = f"f_{safe}"
    if len(safe) > _FIELD_NAME_MAX_LENGTH:
        suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        safe = f"{safe[: _FIELD_NAME_MAX_LENGTH - len(suffix) - 1]}_{suffix}"
    return safe


def _sanitize_name(name: str) -> str:
    return sanitize_cu_field_name(name)


def _deduplicate_name(key: str, index: int) -> str:
    suffix = f"_{index}"
    return f"{key[: _FIELD_NAME_MAX_LENGTH - len(suffix)]}{suffix}"


def _humanize_field_name(name: str) -> str:
    text = re.sub(r"^od_", "", name, flags=re.IGNORECASE)
    text = re.sub(r"_+", " ", text).strip()
    return text[:1].upper() + text[1:] if text else name


def _path_matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _field_options(path: str, options: dict[str, Any]) -> dict[str, Any]:
    hints = options.get("field_hints") or {}
    hint = hints.get(path, {}) if isinstance(hints, dict) else {}
    return hint if isinstance(hint, dict) else {}


def _example_value_to_field(
    value: Any,
    original_name: str,
    path: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Map a single ARGUS example value to a CU field definition."""
    if isinstance(value, dict):
        field = {"type": "object", "properties": _example_object_to_properties(value, options, path)}
    elif isinstance(value, list):
        item = value[0] if value else ""
        item_path = f"{path}[]"
        field = {
            "type": "array",
            "items": _example_value_to_field(item, original_name, item_path, options),
        }
    elif isinstance(value, bool):
        field = {"type": "boolean"}
    elif isinstance(value, int):
        field = {"type": "integer"}
    elif isinstance(value, float):
        field = {"type": "number"}
    else:
        # Strings (including the empty-string placeholders ARGUS uses) -> string.
        field = {"type": "string"}

    hint = _field_options(path, options)
    description = hint.get("description")
    if not description:
        description = _humanize_field_name(original_name) if options.get("humanize_field_names") else original_name
    field["description"] = str(description)

    type_override = hint.get("type")
    if type_override:
        field["type"] = type_override
    if isinstance(hint.get("enum"), list):
        field["enum"] = hint["enum"]

    is_leaf = not isinstance(value, (dict, list))
    method = hint.get("method") or (options.get("default_method") if is_leaf else None)
    confidence_patterns = options.get("confidence_fields") or []
    if is_leaf and isinstance(confidence_patterns, list) and _path_matches(path, confidence_patterns):
        method = method or "extract"
        field["estimateSourceAndConfidence"] = True
    if method:
        field["method"] = method
        if method == "extract":
            field.setdefault("estimateSourceAndConfidence", True)
    if "estimateSourceAndConfidence" in hint:
        field["estimateSourceAndConfidence"] = bool(hint["estimateSourceAndConfidence"])
    return field


def _example_object_to_properties(
    obj: dict[str, Any],
    options: dict[str, Any] | None = None,
    prefix: str = "",
) -> dict[str, Any]:
    """Map an ARGUS example object to CU ``properties`` (sanitized keys)."""
    options = options or {}
    properties: dict[str, Any] = {}
    used: set[str] = set()
    for original_key, value in obj.items():
        key = _sanitize_name(str(original_key))
        # Guarantee uniqueness within this object level.
        candidate = key
        i = 1
        while candidate in used:
            candidate = _deduplicate_name(key, i)
            i += 1
        used.add(candidate)
        path = f"{prefix}.{original_key}" if prefix else str(original_key)
        properties[candidate] = _example_value_to_field(value, str(original_key), path, options)
    return properties


def _build_field_schema(
    dataset_name: str,
    example_schema: dict[str, Any],
    analyzer_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = analyzer_options or {}
    return {
        "name": _sanitize_name(str(options.get("schema_name") or dataset_name))[:64] or "ArgusSchema",
        "description": str(options.get("schema_description") or f"ARGUS dataset '{dataset_name}' extraction schema"),
        "fields": _example_object_to_properties(example_schema, options),
    }


def _analyzer_id(dataset_name: str, analyzer_definition: dict[str, Any]) -> str:
    """Deterministic analyzer id embedding a hash of the schema."""
    canonical = json.dumps(analyzer_definition, sort_keys=True, separators=(",", ":"))
    schema_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
    base = _ANALYZER_ID_RE.sub("_", f"argus_{_ANALYZER_VERSION}_{dataset_name}").strip("_").lower()
    return f"{base[:48]}_{schema_hash}"[:64]


# ─────────────────────────────────────────────────────────────────────────────
# Result normalization: CU fields  ->  ARGUS extracted_data
# ─────────────────────────────────────────────────────────────────────────────
_VALUE_KEYS = (
    "valueString",
    "valueNumber",
    "valueInteger",
    "valueBoolean",
    "valueDate",
    "valueTime",
)


def _field_value(cu_field: Any, example_value: Any) -> Any:
    """Extract a python value from a CU field, shaped like ``example_value``."""
    if not isinstance(cu_field, dict):
        return cu_field if cu_field is not None else ("" if isinstance(example_value, str) else example_value)

    ftype = cu_field.get("type")
    if ftype == "object" or isinstance(example_value, dict):
        sub = cu_field.get("valueObject", {}) or {}
        template = example_value if isinstance(example_value, dict) else {}
        return _normalize_object(sub, template)
    if ftype == "array" or isinstance(example_value, list):
        arr = cu_field.get("valueArray", []) or []
        item_template = example_value[0] if isinstance(example_value, list) and example_value else ""
        return [_field_value(item, item_template) for item in arr]

    for key in _VALUE_KEYS:
        if key in cu_field:
            return cu_field[key]
    # Field present but no value extracted -> preserve ARGUS empty placeholder.
    return "" if isinstance(example_value, str) else None


def _normalize_object(cu_fields: dict[str, Any], example_obj: dict[str, Any]) -> dict[str, Any]:
    """Rebuild an ARGUS-shaped object by parallel-walking the example schema."""
    result: dict[str, Any] = {}
    used: set[str] = set()
    for original_key, example_value in example_obj.items():
        key = _sanitize_name(str(original_key))
        candidate = key
        i = 1
        while candidate in used:
            candidate = _deduplicate_name(key, i)
            i += 1
        used.add(candidate)
        cu_field = cu_fields.get(candidate)
        result[original_key] = _field_value(cu_field, example_value)
    return result


def _collect_confidence(cu_fields: dict[str, Any], prefix: str = "") -> dict[str, float]:
    """Flatten field-level confidence scores for telemetry."""
    scores: dict[str, float] = {}
    if not isinstance(cu_fields, dict):
        return scores
    for name, field in cu_fields.items():
        if not isinstance(field, dict):
            continue
        path = f"{prefix}{name}"
        if isinstance(field.get("confidence"), (int, float)):
            scores[path] = float(field["confidence"])
        if field.get("type") == "object":
            scores.update(_collect_confidence(field.get("valueObject", {}) or {}, f"{path}."))
        elif field.get("type") == "array":
            for idx, item in enumerate(field.get("valueArray", []) or []):
                if isinstance(item, dict) and item.get("type") == "object":
                    scores.update(_collect_confidence(item.get("valueObject", {}) or {}, f"{path}[{idx}]."))
    return scores


# ─────────────────────────────────────────────────────────────────────────────
# Analyzer lifecycle
# ─────────────────────────────────────────────────────────────────────────────
_defaults_set = False


def _ensure_defaults(client: httpx.Client, settings: dict[str, Any]) -> None:
    """Set the Content Understanding resource-level default model deployments.

    Custom analyzers require resource defaults to be configured at least once via
    ``PATCH /contentunderstanding/defaults``. This is idempotent and cached per
    process. The completion (and optional embedding) deployments are taken from
    ``CONTENT_UNDERSTANDING_COMPLETION_MODEL`` / ``CONTENT_UNDERSTANDING_EMBEDDING_MODEL``.
    """
    global _defaults_set
    if _defaults_set:
        return

    completion = settings.get("completion_model")
    embedding = settings.get("embedding_model")
    if not completion and not embedding:
        # Nothing to set; assume defaults were configured out of band.
        _defaults_set = True
        return

    # Content Understanding maps default deployments by model name. Deployment
    # names in this solution match the model names, so key == value.
    model_deployments: dict[str, str] = {}
    if completion:
        model_deployments[completion] = completion
    if embedding:
        model_deployments[embedding] = embedding

    url = f"{settings['endpoint']}/contentunderstanding/defaults?api-version={settings['api_version']}"
    headers = _headers(settings)
    headers["Content-Type"] = "application/merge-patch+json"
    logger.info("Setting Content Understanding defaults: %s", model_deployments)
    resp = client.patch(url, headers=headers, json={"modelDeployments": model_deployments})
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"Failed to set Content Understanding defaults: {resp.status_code} - {resp.text}")
    _defaults_set = True


def _ensure_analyzer(
    client: httpx.Client,
    settings: dict[str, Any],
    analyzer_id: str,
    analyzer_definition: dict[str, Any],
) -> None:
    """Create the analyzer if it does not already exist (cached per process)."""
    if analyzer_id in _ready_analyzers:
        return

    _ensure_defaults(client, settings)

    base = settings["endpoint"]
    api_version = settings["api_version"]
    get_url = f"{base}/contentunderstanding/analyzers/{analyzer_id}?api-version={api_version}"

    existing = client.get(get_url, headers=_headers(settings, json_body=False))
    if existing.status_code == 200:
        status = ""
        try:
            status = str(existing.json().get("status", "")).lower()
        except (ValueError, KeyError):
            status = ""
        if status in ("", "ready", "succeeded", "active"):
            logger.info("Content Understanding analyzer '%s' already exists", analyzer_id)
            _ready_analyzers.add(analyzer_id)
            return
        # Analyzer exists in a non-usable (e.g. Failed) state. Delete it so the
        # create-or-update below does not return the stale failure reason.
        logger.warning("Deleting Content Understanding analyzer '%s' in state '%s'", analyzer_id, status)
        client.delete(get_url, headers=_headers(settings, json_body=False))

    put_url = f"{base}/contentunderstanding/analyzers/{analyzer_id}?api-version={api_version}"
    logger.info("Creating Content Understanding analyzer '%s'", analyzer_id)
    resp = client.put(put_url, headers=_headers(settings), json=analyzer_definition)
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to create Content Understanding analyzer '{analyzer_id}': {resp.status_code} - {resp.text}"
        )

    operation_location = resp.headers.get("Operation-Location")
    if operation_location:
        _poll_operation(
            client,
            settings,
            operation_location,
            terminal_field="status",
            success_values=("succeeded", "ready", "active", "creating"),
        )
    # Confirm the analyzer is queryable before use.
    _wait_analyzer_ready(client, settings, analyzer_id)
    _ready_analyzers.add(analyzer_id)


def _build_analyzer_definition(
    settings: dict[str, Any],
    field_schema: dict[str, Any],
    analyzer_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = analyzer_options or {}
    config = {"enableOcr": True, "enableLayout": True, "returnDetails": True}
    option_config = options.get("config")
    if isinstance(option_config, dict):
        config.update(option_config)

    definition: dict[str, Any] = {
        "description": str(options.get("description") or field_schema.get("description") or "ARGUS analyzer"),
        "baseAnalyzerId": str(options.get("base_analyzer_id") or settings["base_analyzer_id"]),
        "config": config,
        "fieldSchema": field_schema,
    }

    models: dict[str, str] = {}
    if settings.get("completion_model"):
        models["completion"] = settings["completion_model"]
    if settings.get("embedding_model"):
        models["embedding"] = settings["embedding_model"]
    option_models = options.get("models")
    if isinstance(option_models, dict):
        models.update({str(key): str(value) for key, value in option_models.items() if value})
    if models:
        definition["models"] = models
    return definition


def _wait_analyzer_ready(client: httpx.Client, settings: dict[str, Any], analyzer_id: str) -> None:
    base = settings["endpoint"]
    api_version = settings["api_version"]
    url = f"{base}/contentunderstanding/analyzers/{analyzer_id}?api-version={api_version}"
    deadline = time.monotonic() + _POLL_MAX_SEC
    while time.monotonic() < deadline:
        resp = client.get(url, headers=_headers(settings, json_body=False))
        if resp.status_code == 200:
            status = str(resp.json().get("status", "")).lower()
            if status in ("ready", "succeeded", "active", ""):
                return
            if status == "failed":
                raise RuntimeError(f"Content Understanding analyzer '{analyzer_id}' creation failed")
        time.sleep(_POLL_INTERVAL_SEC)
    raise TimeoutError(f"Timed out waiting for analyzer '{analyzer_id}' to become ready")


def _poll_operation(
    client: httpx.Client,
    settings: dict[str, Any],
    operation_location: str,
    *,
    terminal_field: str = "status",
    success_values: tuple[str, ...] = ("succeeded",),
) -> dict[str, Any]:
    """Poll an async operation URL until it reaches a terminal state."""
    deadline = time.monotonic() + _POLL_MAX_SEC
    while time.monotonic() < deadline:
        resp = client.get(operation_location, headers=_headers(settings, json_body=False))
        resp.raise_for_status()
        payload = resp.json()
        status = str(payload.get(terminal_field, "")).lower()
        if status in success_values:
            return payload
        if status == "failed":
            raise RuntimeError(f"Content Understanding operation failed: {payload}")
        time.sleep(_POLL_INTERVAL_SEC)
    raise TimeoutError("Timed out polling Content Understanding operation")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────
def get_cu_extraction(
    file_path: str,
    example_schema: dict[str, Any],
    dataset_name: str = "default",
    cosmos_config_container=None,
    analyzer_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run Content Understanding full-analyzer extraction on a document.

    Returns a dict with:
      * ``ocr_output``      - concatenated markdown across analyzed contents
      * ``extracted_data``  - ARGUS-shaped extraction matching ``example_schema``
      * ``confidence``      - flat {field_path: score} map
      * ``raw``             - the raw CU ``result`` block (for diagnostics)
    """
    settings = _cu_settings(cosmos_config_container)
    field_schema = _build_field_schema(dataset_name, example_schema, analyzer_options)
    analyzer_definition = _build_analyzer_definition(settings, field_schema, analyzer_options)
    analyzer_id = _analyzer_id(dataset_name, analyzer_definition)

    with open(file_path, "rb") as fh:
        data_b64 = base64.b64encode(fh.read()).decode("utf-8")
    mime_type = "application/pdf" if file_path.lower().endswith(".pdf") else _guess_mime(file_path)

    base = settings["endpoint"]
    api_version = settings["api_version"]

    with httpx.Client(timeout=_DEFAULT_TIMEOUT) as client:
        _ensure_analyzer(client, settings, analyzer_id, analyzer_definition)

        analyze_url = f"{base}/contentunderstanding/analyzers/{analyzer_id}:analyze?api-version={api_version}"
        payload = {"inputs": [{"data": data_b64, "mimeType": mime_type, "name": os.path.basename(file_path)}]}
        logger.info("Submitting document to Content Understanding analyzer '%s'", analyzer_id)
        resp = client.post(analyze_url, headers=_headers(settings), json=payload)
        if resp.status_code not in (200, 202):
            raise RuntimeError(f"Content Understanding analyze failed: {resp.status_code} - {resp.text}")

        operation_location = resp.headers.get("Operation-Location")
        if not operation_location:
            # Some responses return the result inline.
            result_payload = resp.json()
        else:
            result_payload = _poll_operation(client, settings, operation_location, success_values=("succeeded",))

    return _normalize_result(result_payload, example_schema)


def prepare_cu_analyzer(
    example_schema: dict[str, Any],
    dataset_name: str = "default",
    cosmos_config_container=None,
    analyzer_options: dict[str, Any] | None = None,
) -> str:
    """Create or reuse a deterministic analyzer without processing a document."""
    settings = _cu_settings(cosmos_config_container)
    field_schema = _build_field_schema(dataset_name, example_schema, analyzer_options)
    analyzer_definition = _build_analyzer_definition(settings, field_schema, analyzer_options)
    analyzer_id = _analyzer_id(dataset_name, analyzer_definition)
    with httpx.Client(timeout=_DEFAULT_TIMEOUT) as client:
        _ensure_analyzer(client, settings, analyzer_id, analyzer_definition)
    return analyzer_id


def describe_cu_analyzer(
    example_schema: dict[str, Any],
    dataset_name: str = "default",
    cosmos_config_container=None,
    analyzer_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the deterministic analyzer id, definition, and configuration hash."""
    settings = _cu_settings(cosmos_config_container)
    field_schema = _build_field_schema(dataset_name, example_schema, analyzer_options)
    analyzer_definition = _build_analyzer_definition(settings, field_schema, analyzer_options)
    canonical = json.dumps(analyzer_definition, sort_keys=True, separators=(",", ":"))
    return {
        "analyzer_id": _analyzer_id(dataset_name, analyzer_definition),
        "definition": analyzer_definition,
        "configuration_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _normalize_result(result_payload: dict[str, Any], example_schema: dict[str, Any]) -> dict[str, Any]:
    result = result_payload.get("result", result_payload)
    contents = result.get("contents", []) or []

    markdown_parts: list[str] = []
    merged_fields: dict[str, Any] = {}
    for content in contents:
        if isinstance(content, dict):
            if content.get("markdown"):
                markdown_parts.append(content["markdown"])
            fields = content.get("fields")
            if isinstance(fields, dict):
                merged_fields.update(fields)

    extracted = _normalize_object(merged_fields, example_schema)
    confidence = _collect_confidence(merged_fields)
    usage = _extract_usage(result_payload)
    logger.info(
        "Content Understanding extraction complete: %d field(s), %d content block(s)",
        len(merged_fields),
        len(contents),
    )
    if usage:
        logger.info(
            "Content Understanding usage: meter=%s pages(minimal=%s basic=%s standard=%s) "
            "contextualization_tokens=%s llm_tokens=%s",
            _dominant_meter(usage),
            usage.get("documentPagesMinimal", 0),
            usage.get("documentPagesBasic", 0),
            usage.get("documentPagesStandard", 0),
            usage.get("contextualizationToken", usage.get("contextualizationTokens", 0)),
            usage.get("tokens") or {},
        )
    return {
        "ocr_output": "\n\n".join(markdown_parts),
        "extracted_data": extracted,
        "confidence": confidence,
        "usage": usage,
        "raw": result,
    }


def _extract_usage(result_payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the CU ``usage`` block from an analyze response, wherever it sits.

    Across API versions the ``usage`` object appears either at the top level of
    the operation payload or nested under ``result``; check both.
    """
    for candidate in (result_payload.get("usage"), (result_payload.get("result") or {}).get("usage")):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _dominant_meter(usage: dict[str, Any]) -> str | None:
    """Highest content-extraction tier actually exercised (the CU 'level' used)."""
    for key, meter in (
        ("documentPagesStandard", "standard"),
        ("documentPagesBasic", "basic"),
        ("documentPagesMinimal", "minimal"),
    ):
        try:
            if int(usage.get(key) or 0) > 0:
                return meter
        except (TypeError, ValueError):
            continue
    return None


def _guess_mime(file_path: str) -> str:
    lower = file_path.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".tiff") or lower.endswith(".tif"):
        return "image/tiff"
    return "application/pdf"
