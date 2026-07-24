"""
API route handlers for ARGUS Container App
"""

import asyncio
import copy
import json
import logging
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Dict

from azure.identity import DefaultAzureCredential
from fastapi import BackgroundTasks, HTTPException, Request

from blob_processing import process_blob_event
from dependencies import (
    get_blob_service_client,
    get_conf_container,
    get_data_container,
    get_logic_app_manager,
    set_global_processing_semaphore,
)
from models import EventGridEvent
from profiling import run_cost_profile

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "functionapp"))
from ai_ocr.agents import assistant_message, run_chat, text_content, user_message
from ai_ocr.cost import get_pricing
from ai_ocr.process import connect_to_cosmos, fetch_model_prompt_and_schema

logger = logging.getLogger(__name__)

DEFAULT_FLAG_EMAIL_PROMPT_TEMPLATE = """You are drafting a courteous email to the original uploader of a document that was flagged for review. The uploader is a business user, not a technical specialist.

Document context:
- Filename: {filename}
- Why it was flagged (written in plain business language):
{reasons_text}
- Additional quality notes: {image_quality_summary}

Write a warm, concise, professional email that:
1. Briefly thanks the uploader and explains, in plain everyday language, that the document could not be processed reliably.
2. Summarizes the issue(s) using the plain-language reasons above. Do NOT use internal codes, field names, jargon, raw metric values, or thresholds (for example, never write tokens like "low_ocr_confidence", "laplacian", or "< 0.60"). Translate everything into language a non-technical business user understands.
3. Proposes concrete, friendly next steps, such as: re-scan the document at a higher quality/resolution, ensure good lighting with the page laid flat, then re-upload the corrected file.

Keep the tone helpful and reassuring, not blaming. Return only JSON with string fields "subject" and "body"."""
DEFAULT_FLAG_EMAIL_FROM = "noreply@argus.example"
DEFAULT_FLAG_EMAIL_TO_FALLBACK = "uploader@argus.example"


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _default_flag_email_config() -> dict:
    return {
        "prompt_template": DEFAULT_FLAG_EMAIL_PROMPT_TEMPLATE,
        "from": os.getenv("FLAG_EMAIL_FROM", DEFAULT_FLAG_EMAIL_FROM),
        "to_fallback": os.getenv("FLAG_EMAIL_TO_FALLBACK", DEFAULT_FLAG_EMAIL_TO_FALLBACK),
    }


def _apply_flag_email_defaults(config: dict) -> dict:
    config_with_defaults = copy.deepcopy(config)
    flag_email = config_with_defaults.get("flag_email")
    if not isinstance(flag_email, dict):
        flag_email = {}

    for key, value in _default_flag_email_config().items():
        if not flag_email.get(key):
            flag_email[key] = value

    config_with_defaults["flag_email"] = flag_email
    return config_with_defaults


def _get_flag_email_config() -> dict:
    conf_container = get_conf_container()
    if not conf_container:
        return _default_flag_email_config()

    try:
        config_item = conf_container.read_item(item="configuration", partition_key="configuration")
        return _apply_flag_email_defaults(config_item)["flag_email"]
    except Exception as e:
        logger.warning("Could not read flag email configuration, using defaults: %s", e)
        return _default_flag_email_config()


def _get_document_by_id(data_container, document_id: str) -> dict:
    items = list(
        data_container.query_items(
            query="SELECT * FROM c WHERE c.id = @document_id",
            parameters=[{"name": "@document_id", "value": document_id}],
            enable_cross_partition_query=True,
        )
    )
    if not items:
        raise HTTPException(status_code=404, detail="Document not found")
    return items[0]


def _get_document_dataset(document: dict) -> str:
    document_id = document.get("id", "")
    if document.get("dataset"):
        return document["dataset"]
    if "__" in document_id:
        return document_id.split("__", 1)[0]
    return "default-dataset"


def _get_document_filename(document: dict) -> str:
    properties = document.get("properties") or {}
    document_id = document.get("id", "")
    filename = (
        document.get("file_name")
        or document.get("filename")
        or properties.get("file_name")
        or properties.get("filename")
    )
    if filename:
        return filename
    if "__" in document_id:
        return document_id.split("__", 1)[1]
    return document_id


def _as_reason_list(reasons: Any) -> list[str]:
    if isinstance(reasons, list):
        return [str(reason) for reason in reasons if str(reason).strip()]
    if reasons:
        return [str(reasons)]
    return []


def _summarize_image_quality(image_quality: Any) -> str | None:
    if not image_quality:
        return None
    if isinstance(image_quality, str):
        return image_quality
    if not isinstance(image_quality, dict):
        return json.dumps(image_quality, default=str)

    issues = image_quality.get("issues")
    if isinstance(issues, list) and issues:
        return "; ".join(str(issue) for issue in issues)

    summary_parts = []
    for key in ("summary", "overall", "status", "dpi", "resolution", "blur_score", "brightness", "contrast"):
        value = image_quality.get(key)
        if value not in (None, "", []):
            summary_parts.append(f"{key}: {value}")
    return "; ".join(summary_parts) if summary_parts else json.dumps(image_quality, default=str)


def _document_is_flagged(document: dict) -> bool:
    flag = (document.get("properties") or {}).get("flag") or {}
    return flag.get("flagged") is True


# Maps the raw, technical flag-reason tokens emitted by the processing pipeline
# (e.g. "low_ocr_confidence (mean 0.42 < 0.60)") to plain-English explanations a
# non-technical business user can understand. Keys are matched against the leading
# token of each reason (the part before any "(" or "="), longest/most-specific first.
_REASON_HUMANIZERS: dict[str, str] = {
    "low_ocr_confidence": (
        "The quality of the text recognition (OCR) was low - the system was unsure about "
        "many of the words it read from the scan, so the extracted information may be unreliable."
    ),
    "high_low_confidence_word_fraction": (
        "A large portion of the words on the page were read with low confidence, "
        "which usually means the scan is faint, blurry, or hard to read."
    ),
    "cu_mean_confidence": (
        "The automated reading service returned low overall confidence for this document, "
        "indicating the page was difficult to interpret reliably."
    ),
    "paddle_low_confidence": ("An initial quality check found that the scanned text was faint or hard to read."),
    "paddle_high_low_confidence_fraction": (
        "An initial quality check found that many lines on the page were difficult to read."
    ),
    "low_quality_pages": ("One or more pages were assessed as poor scan quality and could not be read reliably."),
    "low_quality": "One or more pages were assessed as poor scan quality.",
    "blurry": "The scan appears blurry or out of focus, making the text hard to read.",
    "too dark": "The scan is too dark, which makes the text difficult to read.",
    "too bright/washed out": ("The scan looks overexposed or washed out, so the text does not stand out clearly."),
    "low contrast": ("The scan has low contrast, so the text does not stand out clearly from the background."),
    "blank page": "One or more pages appear to be blank.",
    "low resolution": (
        "The scan resolution is too low to read reliably - a higher-quality (higher-DPI) scan is needed."
    ),
    "failed to load image": "The document image could not be opened for a quality check.",
    "quality assessment failed": ("The automated quality check could not be completed for this document."),
    "ocr_text_unreadable_or_empty": ("Very little or no readable text could be extracted from the document."),
    "rules_all_required_fields_filled": ("The document was routed for a manual review based on business rules."),
}


def _humanize_reason(reason: str) -> str:
    """Translate a single raw flag-reason token into plain business language."""
    text = str(reason).strip()
    if not text:
        return text
    # The token is the part before any parenthetical detail or metric comparison.
    token = text.split("(", 1)[0].split("=", 1)[0].strip().lower()
    if token in _REASON_HUMANIZERS:
        return _REASON_HUMANIZERS[token]
    # Fall back to a prefix match (longest key first) for tokens that carry suffixes.
    for key in sorted(_REASON_HUMANIZERS, key=len, reverse=True):
        if token.startswith(key):
            return _REASON_HUMANIZERS[key]
    # Unknown reason: clean it up (drop technical detail, de-snake-case) so the email
    # never surfaces a raw internal code.
    cleaned = token.replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else text


def _humanize_reasons(reasons: list[str]) -> list[str]:
    """Humanize a list of reasons, de-duplicating any that collapse to the same text."""
    seen: set[str] = set()
    humanized: list[str] = []
    for reason in reasons:
        friendly = _humanize_reason(reason)
        if friendly and friendly not in seen:
            seen.add(friendly)
            humanized.append(friendly)
    return humanized


def _build_flag_email_context(document: dict) -> dict:
    properties = document.get("properties") or {}
    flag = properties.get("flag") or {}
    reasons = _as_reason_list(flag.get("reasons"))
    friendly_reasons = _humanize_reasons(reasons)
    image_quality = properties.get("image_quality")
    image_quality_summary = _summarize_image_quality(image_quality) or "No additional quality notes were recorded."

    return {
        "document_id": document.get("id", ""),
        "dataset": _get_document_dataset(document),
        "filename": _get_document_filename(document),
        "stage": flag.get("stage", ""),
        "flagged_at": flag.get("flagged_at", ""),
        "reasons": "; ".join(friendly_reasons) if friendly_reasons else "No specific reasons were recorded.",
        "reasons_text": (
            "\n".join(f"- {reason}" for reason in friendly_reasons)
            if friendly_reasons
            else "- No specific reasons were recorded."
        ),
        "image_quality_summary": image_quality_summary,
        "quality_metrics": json.dumps(image_quality or {}, indent=2, default=str),
    }


def _render_flag_email_template(template: str, document: dict) -> str:
    context = _build_flag_email_context(document)
    try:
        rendered_template = template.format_map(_SafeFormatDict(context))
    except ValueError:
        logger.warning("Flag email prompt template contains invalid format placeholders; appending context instead")
        rendered_template = template

    return f"{rendered_template}\n\nResolved document context:\n{json.dumps(context, indent=2, default=str)}"


def _extract_json_object(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        cleaned = cleaned.removesuffix("```").strip()

    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _fallback_email_body(document: dict) -> str:
    context = _build_flag_email_context(document)
    return (
        "Hello,\n\n"
        f'Thank you for uploading "{context["filename"]}". Unfortunately, we were unable to '
        "process it reliably because the quality of the scan was too low for the following reasons:\n\n"
        f"{context['reasons_text']}\n\n"
        "To help us process your document, please:\n"
        "- Re-scan it at a higher quality/resolution (a higher DPI setting).\n"
        "- Make sure the page is laid flat with good, even lighting.\n"
        "- Re-upload the corrected file.\n\n"
        "Once you re-upload, we'll take another look right away. Thank you for your help!"
    )


def _parse_email_draft(text: str, document: dict) -> dict:
    parsed = _extract_json_object(text)
    if parsed:
        subject = str(parsed.get("subject") or "").strip()
        body = str(parsed.get("body") or "").strip()
        if subject and body:
            return {"subject": subject, "body": body}

    filename = _get_document_filename(document)
    return {
        "subject": f"Action needed: re-upload flagged document {filename}",
        "body": text.strip() or _fallback_email_body(document),
    }


def _get_uploader_email(document: dict) -> str | None:
    properties = document.get("properties") or {}
    candidates = [
        document.get("uploader_email"),
        document.get("uploaded_by_email"),
        document.get("created_by_email"),
        properties.get("uploader_email"),
        properties.get("uploaded_by_email"),
        properties.get("created_by_email"),
    ]
    uploader = properties.get("uploader")
    if isinstance(uploader, dict):
        candidates.append(uploader.get("email"))

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


async def root():
    """Health check endpoint"""
    return {"status": "healthy", "service": "ARGUS Backend"}


async def health_check():
    """Detailed health check"""
    try:
        blob_service_client = get_blob_service_client()
        data_container = get_data_container()
        conf_container = get_conf_container()

        # Check if we can connect to storage
        if blob_service_client:
            container_client = blob_service_client.get_container_client(os.getenv("CONTAINER_NAME", "datasets"))
            container_client.get_container_properties()

        # Check if we can connect to Cosmos DB
        if data_container and conf_container:
            # Try to query Cosmos DB
            list(data_container.query_items(query="SELECT TOP 1 * FROM c", enable_cross_partition_query=True))

        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "services": {"storage": "connected", "cosmos_db": "connected"},
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service unhealthy")


async def handle_blob_created(request: Request, background_tasks: BackgroundTasks):
    """Handle Event Grid blob created events"""
    try:
        # Parse the Event Grid request
        request_body = await request.json()

        # Handle Event Grid subscription validation
        if isinstance(request_body, list) and len(request_body) > 0:
            event = request_body[0]

            # Handle subscription validation
            if event.get("eventType") == "Microsoft.EventGrid.SubscriptionValidationEvent":
                validation_code = event.get("data", {}).get("validationCode")
                if validation_code:
                    return {"validationResponse": validation_code}

        # Process blob created events
        events = request_body if isinstance(request_body, list) else [request_body]

        for event_data in events:
            event = EventGridEvent(event_data)

            if event.event_type == "Microsoft.Storage.BlobCreated":
                blob_url = event.data.get("url")
                if blob_url and "/datasets/" in blob_url:
                    logger.info(f"Processing blob created event for: {blob_url}")

                    # Add to background tasks for async processing
                    background_tasks.add_task(process_blob_event, blob_url, event.data)

        return {"status": "accepted", "message": "Events queued for processing"}

    except Exception as e:
        logger.error(f"Error handling blob created event: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal server error")


async def process_blob_manual(request: Request, background_tasks: BackgroundTasks):
    """Manually trigger blob processing (for testing)"""
    try:
        request_body = await request.json()
        blob_url = request_body.get("blob_url")

        if not blob_url:
            raise HTTPException(status_code=400, detail="blob_url is required")

        # Add to background tasks
        background_tasks.add_task(process_blob_event, blob_url, {"url": blob_url})

        return {"status": "accepted", "message": "Blob queued for processing"}

    except Exception as e:
        logger.error(f"Error in manual blob processing: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


async def get_configuration():
    """Get current configuration from Cosmos DB"""
    try:
        conf_container = get_conf_container()
        if not conf_container:
            raise HTTPException(status_code=503, detail="Configuration container not available")

        try:
            # Try to get the main configuration item
            config_item = conf_container.read_item(item="configuration", partition_key="configuration")
            # Remove Cosmos DB specific fields
            clean_config = {k: v for k, v in config_item.items() if not k.startswith("_")}
            config_with_defaults = _apply_flag_email_defaults(clean_config)
            if clean_config.get("flag_email") != config_with_defaults.get("flag_email"):
                try:
                    conf_container.upsert_item(config_with_defaults)
                except Exception as upsert_error:
                    logger.warning("Could not persist default flag email configuration: %s", upsert_error)
            return config_with_defaults
        except Exception as e:
            logger.warning(f"Configuration item not found, returning default: {e}")
            # Return default configuration structure
            default_config = _apply_flag_email_defaults(
                {"id": "configuration", "partitionKey": "configuration", "datasets": {}}
            )
            try:
                conf_container.upsert_item(default_config)
            except Exception as upsert_error:
                logger.warning("Could not persist default configuration: %s", upsert_error)
            return default_config

    except Exception as e:
        logger.error(f"Error fetching configuration: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch configuration")


async def update_configuration(request: Request):
    """Update configuration in Cosmos DB"""
    try:
        conf_container = get_conf_container()
        if not conf_container:
            raise HTTPException(status_code=503, detail="Configuration container not available")

        config_data = await request.json()

        # Ensure the configuration has required fields
        if "id" not in config_data:
            config_data["id"] = "configuration"
        if "partitionKey" not in config_data:
            config_data["partitionKey"] = "configuration"
        config_data = _apply_flag_email_defaults(config_data)

        # Upsert the single configuration item
        conf_container.upsert_item(config_data)

        return {"status": "success", "message": "Configuration updated"}

    except Exception as e:
        logger.error(f"Error updating configuration: {e}")
        raise HTTPException(status_code=500, detail="Failed to update configuration")


async def refresh_configuration():
    """Force refresh configuration by reloading demo datasets"""
    try:
        conf_container = get_conf_container()
        if not conf_container:
            raise HTTPException(status_code=503, detail="Configuration container not available")

        logger.info("Forcing configuration refresh from demo files")

        try:
            # This will force reload the configuration from demo files
            prompt, schema, max_pages, options = fetch_model_prompt_and_schema("default-dataset", force_refresh=True)
            logger.info(
                f"Configuration refreshed successfully - prompt length: {len(prompt)}, schema size: {len(str(schema))}"
            )

            return {
                "status": "success",
                "message": "Configuration refreshed successfully",
                "prompt_length": len(prompt),
                "schema_size": len(str(schema)),
                "schema_empty": not bool(schema),
            }
        except Exception as inner_e:
            logger.error(f"Error during configuration refresh: {inner_e}")
            return {"status": "error", "message": f"Failed to refresh configuration: {str(inner_e)}"}

    except Exception as e:
        logger.error(f"Error refreshing configuration: {e}")
        raise HTTPException(status_code=500, detail="Failed to refresh configuration")


async def get_concurrency_settings():
    """Get current Logic App concurrency settings"""
    try:
        logic_app_manager = get_logic_app_manager()
        if not logic_app_manager:
            raise HTTPException(status_code=503, detail="Logic App Manager not initialized")

        settings = await logic_app_manager.get_concurrency_settings()

        if "error" in settings:
            if not settings.get("enabled", False):
                raise HTTPException(status_code=503, detail=settings["error"])
            else:
                raise HTTPException(status_code=500, detail=settings["error"])

        return settings

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting concurrency settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to get concurrency settings")


async def update_concurrency_settings(request: Request):
    """Update Logic App concurrency settings"""
    try:
        logic_app_manager = get_logic_app_manager()
        if not logic_app_manager:
            raise HTTPException(status_code=503, detail="Logic App Manager not initialized")

        request_body = await request.json()
        max_runs = request_body.get("max_runs")

        if max_runs is None:
            raise HTTPException(status_code=400, detail="max_runs is required")

        if not isinstance(max_runs, int):
            raise HTTPException(status_code=400, detail="max_runs must be an integer")

        result = await logic_app_manager.update_concurrency_settings(max_runs)

        if not result.get("success", False):
            error_msg = result.get("error", "Unknown error occurred")
            raise HTTPException(status_code=400, detail=error_msg)

        # Update the global semaphore to match the new concurrency setting
        global_processing_semaphore = asyncio.Semaphore(max_runs)
        set_global_processing_semaphore(global_processing_semaphore)
        logger.info(f"Updated global processing semaphore to allow {max_runs} concurrent operations")

        # Add semaphore info to the result
        result["backend_semaphore_updated"] = True
        result["backend_max_concurrent"] = max_runs

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating concurrency settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to update concurrency settings")


async def get_workflow_definition():
    """Get the complete Logic App workflow definition for inspection"""
    try:
        logic_app_manager = get_logic_app_manager()
        if not logic_app_manager:
            raise HTTPException(status_code=503, detail="Logic App Manager not initialized")

        definition = await logic_app_manager.get_workflow_definition()

        if not definition.get("enabled", False):
            error_msg = definition.get("error", "Unknown error occurred")
            raise HTTPException(status_code=400, detail=error_msg)

        return definition

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting workflow definition: {e}")
        raise HTTPException(status_code=500, detail="Failed to get workflow definition")


async def update_full_concurrency_settings(request: Request):
    """Update Logic App concurrency settings for both triggers and actions"""
    try:
        logic_app_manager = get_logic_app_manager()
        if not logic_app_manager:
            raise HTTPException(status_code=503, detail="Logic App Manager not initialized")

        request_body = await request.json()
        max_runs = request_body.get("max_runs")

        if max_runs is None:
            raise HTTPException(status_code=400, detail="max_runs is required")

        if not isinstance(max_runs, int):
            raise HTTPException(status_code=400, detail="max_runs must be an integer")

        result = await logic_app_manager.update_action_concurrency_settings(max_runs)

        if not result.get("success", False):
            error_msg = result.get("error", "Unknown error occurred")
            raise HTTPException(status_code=400, detail=error_msg)

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating full concurrency settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to update full concurrency settings")


async def process_file(request: Request, background_tasks: BackgroundTasks):
    """Process file endpoint called by Logic App"""
    try:
        request_body = await request.json()
        logger.info(f"Received process-file request: {request_body}")

        # Extract parameters from Logic App request
        filename = request_body.get("filename")
        dataset = request_body.get("dataset")
        blob_path = request_body.get("blob_path")
        trigger_source = request_body.get("trigger_source", "logic_app")

        if not all([filename, dataset, blob_path]):
            logger.error(
                f"Missing required parameters. filename: {filename}, dataset: {dataset}, blob_path: {blob_path}"
            )
            raise HTTPException(status_code=400, detail="Missing required parameters: filename, dataset, blob_path")

        # Convert to blob URL format expected by our processing function
        storage_account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
        if not storage_account_name:
            raise HTTPException(status_code=500, detail="Storage account name not configured")

        # Parse the blob_path to extract container and blob name
        path_parts = blob_path.strip("/").split("/", 1)  # Split into at most 2 parts
        if len(path_parts) != 2:
            raise HTTPException(status_code=400, detail="Invalid blob_path format. Expected: /container/blob-name")

        container_name, blob_name = path_parts
        blob_url = f"https://{storage_account_name}.blob.core.windows.net/{container_name}/{blob_name}"

        logger.info(f"Processing file: {filename} from dataset: {dataset}")
        logger.info(f"Blob path: {blob_path}")
        logger.info(f"Constructed blob URL: {blob_url}")

        # Add to background tasks using our existing processing function
        background_tasks.add_task(
            process_blob_event,
            blob_url,
            {"url": blob_url, "filename": filename, "dataset": dataset, "trigger_source": trigger_source},
        )

        return {
            "status": "accepted",
            "message": f"File {filename} queued for processing",
            "filename": filename,
            "dataset": dataset,
            "blob_url": blob_url,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in process-file endpoint: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal server error")


async def run_profiling(request: Request):
    """Run the cost profiling harness across demo documents and tiers."""
    try:
        try:
            request_body = await request.json()
        except Exception:
            request_body = {}
        if not isinstance(request_body, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")

        dataset = request_body.get("dataset") or "default-dataset"
        files = request_body.get("files")
        tiers = request_body.get("tiers")
        if files is not None and not isinstance(files, list):
            raise HTTPException(status_code=400, detail="files must be a list of file names")
        if tiers is not None and not isinstance(tiers, list):
            raise HTTPException(status_code=400, detail="tiers must be a list of tier names")

        data_container = get_data_container()
        if not data_container:
            raise HTTPException(status_code=503, detail="Data container not available")

        return await asyncio.to_thread(
            run_cost_profile,
            dataset=dataset,
            files=files,
            tiers=tiers,
            data_container=data_container,
            persist=True,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Error running profiling harness: %s", e)
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Profiling run failed")


async def get_openai_settings():
    """Get current OpenAI configuration from environment variables (read-only)"""
    try:
        # Return current environment variable values (for display purposes only)
        return {
            "openai_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            "openai_key": "***HIDDEN***" if os.getenv("AZURE_OPENAI_KEY") else "",
            "deployment_name": os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME", ""),
            "ocr_provider": os.getenv("OCR_PROVIDER", "azure"),
            "mistral_endpoint": os.getenv("MISTRAL_DOC_AI_ENDPOINT", ""),
            "mistral_key": "***HIDDEN***" if os.getenv("MISTRAL_DOC_AI_KEY") else "",
            "mistral_model": os.getenv("MISTRAL_DOC_AI_MODEL", "mistral-document-ai-2505"),
            "note": "Configuration is read from environment variables only. Update via deployment/infrastructure.",
        }

    except Exception as e:
        logger.error(f"Error fetching OpenAI settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch OpenAI settings")


async def update_openai_settings(request: Request):
    """Update OpenAI settings by modifying environment variables"""
    try:
        data = await request.json()

        # Update environment variables
        if "openai_endpoint" in data:
            os.environ["AZURE_OPENAI_ENDPOINT"] = data["openai_endpoint"]
        if "openai_key" in data:
            os.environ["AZURE_OPENAI_KEY"] = data["openai_key"]
        if "openai_deployment_name" in data:
            os.environ["AZURE_OPENAI_MODEL_DEPLOYMENT_NAME"] = data["openai_deployment_name"]
        if "ocr_provider" in data:
            os.environ["OCR_PROVIDER"] = data["ocr_provider"]
        if "mistral_endpoint" in data:
            os.environ["MISTRAL_DOC_AI_ENDPOINT"] = data["mistral_endpoint"]
        if "mistral_key" in data:
            os.environ["MISTRAL_DOC_AI_KEY"] = data["mistral_key"]
        if "mistral_model" in data:
            os.environ["MISTRAL_DOC_AI_MODEL"] = data["mistral_model"]

        # Return success response with updated config (hide keys)
        updated_config = {
            "openai_endpoint": os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
            "openai_key": "***hidden***" if os.environ.get("AZURE_OPENAI_KEY") else "",
            "openai_deployment_name": os.environ.get("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME", ""),
            "ocr_provider": os.environ.get("OCR_PROVIDER", "azure"),
            "mistral_endpoint": os.environ.get("MISTRAL_DOC_AI_ENDPOINT", ""),
            "mistral_key": "***hidden***" if os.environ.get("MISTRAL_DOC_AI_KEY") else "",
            "mistral_model": os.environ.get("MISTRAL_DOC_AI_MODEL", "mistral-document-ai-2505"),
            "env_var_only": True,
        }

        return {"message": "Environment variables updated successfully", "config": updated_config}

    except Exception as e:
        logger.error(f"Error updating OpenAI settings: {e}")
        raise HTTPException(status_code=400, detail=f"Error updating settings: {str(e)}")


DEFAULT_DISCOUNT_PCT = 28.0


def _default_pricing_settings() -> dict:
    """Solution-wide pricing knobs with env-var fallbacks."""
    discount = DEFAULT_DISCOUNT_PCT
    raw = os.getenv("PRICING_DISCOUNT_PCT")
    if raw:
        try:
            discount = float(raw)
        except ValueError:
            discount = DEFAULT_DISCOUNT_PCT
    return {
        "discount_pct": max(0.0, min(discount, 100.0)),
        "consumption_available": os.getenv("PRICING_CONSUMPTION_AVAILABLE", "false").lower() in ("1", "true", "yes"),
    }


async def get_pricing_settings():
    """Get solution-wide pricing settings (agreement discount + consumption flag)."""
    settings = _default_pricing_settings()
    try:
        conf_container = get_conf_container()
        config_item = conf_container.read_item(item="configuration", partition_key="configuration")
        pricing = config_item.get("pricing") or {}
        if "discount_pct" in pricing:
            settings["discount_pct"] = max(0.0, min(float(pricing.get("discount_pct") or 0.0), 100.0))
        if "consumption_available" in pricing:
            settings["consumption_available"] = bool(pricing.get("consumption_available"))
    except Exception as e:  # noqa: BLE001 - best-effort; fall back to env defaults
        logger.warning("Could not read pricing settings, using defaults: %s", e)
    return settings


async def update_pricing_settings(request: Request):
    """Persist solution-wide pricing settings to the Cosmos configuration document."""
    try:
        data = await request.json()
        conf_container = get_conf_container()
        try:
            config_item = conf_container.read_item(item="configuration", partition_key="configuration")
        except Exception:
            config_item = {"id": "configuration", "partitionKey": "configuration", "datasets": {}}

        pricing = config_item.get("pricing") or {}
        if "discount_pct" in data:
            pricing["discount_pct"] = max(0.0, min(float(data.get("discount_pct") or 0.0), 100.0))
        if "consumption_available" in data:
            pricing["consumption_available"] = bool(data.get("consumption_available"))
        config_item["pricing"] = pricing
        config_item = _apply_flag_email_defaults(config_item)
        conf_container.upsert_item(config_item)
        return {
            "message": "Pricing settings updated successfully",
            "pricing": {
                "discount_pct": pricing.get("discount_pct", 0.0),
                "consumption_available": pricing.get("consumption_available", False),
            },
        }
    except Exception as e:
        logger.error(f"Error updating pricing settings: {e}")
        raise HTTPException(status_code=400, detail=f"Error updating pricing settings: {str(e)}")


async def chat_with_document(request: Request):
    """
    Chat endpoint for asking questions about a specific document.
    Uses the GPT extraction as context for answering questions.
    """
    try:
        data = await request.json()
        document_id = data.get("document_id")
        message = data.get("message", "").strip()
        chat_history = data.get("chat_history", [])

        if not document_id or not message:
            raise HTTPException(status_code=400, detail="document_id and message are required")

        # Get the document from Cosmos DB
        cosmos_container, cosmos_config_container = connect_to_cosmos()
        if not cosmos_container:
            raise HTTPException(status_code=500, detail="Unable to connect to Cosmos DB")

        try:
            # Fetch the document using a parameterized query (prevents NoSQL injection)
            items = list(
                cosmos_container.query_items(
                    query="SELECT * FROM c WHERE c.id = @id",
                    parameters=[{"name": "@id", "value": document_id}],
                    enable_cross_partition_query=True,
                )
            )

            if not items:
                raise HTTPException(status_code=404, detail="Document not found")

            document = items[0]
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error fetching document {document_id}: {e}")
            raise HTTPException(status_code=404, detail="Document not found")

        # Extract GPT extraction data to use as context
        extracted_data = document.get("extracted_data", {})
        gpt_extraction = extracted_data.get("gpt_extraction_output")
        ocr_data = extracted_data.get("ocr_output", "")

        if not gpt_extraction and not ocr_data:
            raise HTTPException(status_code=400, detail="No extracted data available for this document")

        # Prepare context for the chat
        context_parts = []

        if gpt_extraction:
            if isinstance(gpt_extraction, dict):
                context_parts.append("GPT EXTRACTED DATA:")
                context_parts.append(json.dumps(gpt_extraction, indent=2))
            else:
                context_parts.append("GPT EXTRACTED DATA:")
                context_parts.append(str(gpt_extraction))

        if ocr_data and len(context_parts) == 0:
            # Only include OCR if no GPT extraction available
            context_parts.append("DOCUMENT TEXT (OCR):")
            # Limit OCR data to prevent token overflow
            ocr_snippet = ocr_data[:3000] + "..." if len(ocr_data) > 3000 else ocr_data
            context_parts.append(ocr_snippet)

        document_context = "\n\n".join(context_parts)

        # Build chat history for context
        conversation_context = ""
        if chat_history:
            conversation_context = "\n\nPREVIOUS CONVERSATION:\n"
            for i, chat_item in enumerate(chat_history[-5:]):  # Last 5 messages only
                role = chat_item.get("role", "user")
                content = chat_item.get("content", "")
                conversation_context += f"{role.upper()}: {content}\n"

        # Create the system prompt
        system_prompt = f"""You are an AI assistant helping users understand and analyze document content.

The user has uploaded a document that has been processed and analyzed. You have access to the extracted data from this document.

Your role is to:
- Answer questions about the document content accurately
- Help users understand specific details from the document
- Provide insights based on the extracted information
- Be concise but thorough in your responses
- If information is not available in the extracted data, clearly state that

DOCUMENT CONTEXT:
{document_context}
{conversation_context}

Please answer the user's question based on this document context."""

        # Run the chat turn via Microsoft Agent Framework
        result = await run_chat(
            [user_message([text_content(message)])],
            instructions=system_prompt,
        )

        assistant_message = result.text
        finish_reason = result.finish_reason

        # Check for truncation
        if finish_reason == "length":
            assistant_message += "\n\n[Note: Response was truncated due to length limits. Please ask for more specific details if needed.]"

        return {
            "response": assistant_message,
            "finish_reason": finish_reason,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Chat processing failed: {str(e)}")


async def argus_list_documents(
    dataset: Annotated[str, "Filter by dataset name"] = "",
    status: Annotated[str, "Filter by status (pending, processing, completed, failed)"] = "",
    limit: Annotated[int, "Maximum number of documents to return"] = 50,
) -> Any:
    """List all processed documents with optional filtering by dataset or status."""
    args: Dict[str, Any] = {"limit": limit}
    if dataset:
        args["dataset"] = dataset
    if status:
        args["status"] = status
    return await _execute_mcp_tool("argus_list_documents", args)


async def argus_get_document(
    document_id: Annotated[str, "The unique identifier of the document"],
) -> Any:
    """Get detailed information about a specific document including OCR text, extracted data, and evaluation results."""
    return await _execute_mcp_tool("argus_get_document", {"document_id": document_id})


async def argus_chat_with_document(
    document_id: Annotated[str, "The document to chat about"],
    question: Annotated[str, "Your question about the document"],
) -> Any:
    """Ask natural language questions about a specific document's content."""
    return await _execute_mcp_tool("argus_chat_with_document", {"document_id": document_id, "question": question})


async def argus_list_datasets() -> Any:
    """List all available dataset configurations in ARGUS."""
    return await _execute_mcp_tool("argus_list_datasets", {})


async def argus_get_dataset_config(
    dataset_name: Annotated[str, "The name of the dataset"],
) -> Any:
    """Get the configuration for a specific dataset including system prompt and output schema."""
    return await _execute_mcp_tool("argus_get_dataset_config", {"dataset_name": dataset_name})


async def argus_search_documents(
    query: Annotated[str, "Search keyword or phrase"],
    dataset: Annotated[str, "Limit search to specific dataset"] = "",
    limit: Annotated[int, "Maximum results"] = 20,
) -> Any:
    """Search documents by filename or content keywords."""
    args: Dict[str, Any] = {"query": query, "limit": limit}
    if dataset:
        args["dataset"] = dataset
    return await _execute_mcp_tool("argus_search_documents", args)


async def argus_get_extraction(
    document_id: Annotated[str, "The document ID"],
) -> Any:
    """Get just the extracted structured data from a document."""
    return await _execute_mcp_tool("argus_get_extraction", {"document_id": document_id})


async def argus_process_document_url(
    blob_url: Annotated[str, "Full Azure Blob Storage URL"],
    dataset: Annotated[str, "Dataset to use"] = "default-dataset",
) -> Any:
    """Manually queue a document for processing. NOTE: Only use this for RE-PROCESSING existing documents or processing documents uploaded through external means. Files uploaded through this chat are automatically processed - do not call this tool for newly uploaded attachments."""
    return await _execute_mcp_tool("argus_process_document_url", {"blob_url": blob_url, "dataset": dataset})


async def argus_get_upload_url(
    filename: Annotated[str, "Name for the uploaded file"],
    dataset: Annotated[str, "Target dataset"] = "default-dataset",
) -> Any:
    """Get a pre-signed SAS URL for uploading a document to ARGUS."""
    return await _execute_mcp_tool("argus_get_upload_url", {"filename": filename, "dataset": dataset})


async def argus_create_dataset(
    dataset_name: Annotated[str, "Unique name for the dataset (alphanumeric and hyphens only)"],
    system_prompt: Annotated[str, "Instructions for the AI on how to extract data from documents in this dataset"],
    output_schema: Annotated[
        Dict[str, Any],
        "JSON schema defining the structure of extracted data. Use empty strings as placeholders for values.",
    ],
    max_pages_per_chunk: Annotated[int, "Maximum pages to process per chunk"] = 10,
) -> Any:
    """Create a new dataset configuration with a custom system prompt and output schema for document extraction."""
    return await _execute_mcp_tool(
        "argus_create_dataset",
        {
            "dataset_name": dataset_name,
            "system_prompt": system_prompt,
            "output_schema": output_schema,
            "max_pages_per_chunk": max_pages_per_chunk,
        },
    )


# Agent Framework tool set exposed by the MCP-powered chat endpoint.
MCP_CHAT_TOOLS = [
    argus_list_documents,
    argus_get_document,
    argus_chat_with_document,
    argus_list_datasets,
    argus_get_dataset_config,
    argus_search_documents,
    argus_get_extraction,
    argus_process_document_url,
    argus_get_upload_url,
    argus_create_dataset,
]


async def mcp_chat(request: Request):
    """
    MCP-powered chat endpoint with tool calling capabilities.
    Uses the Microsoft Agent Framework with auto tool-calling to execute ARGUS MCP tools.
    """
    try:
        data = await request.json()
        message = data.get("message", "").strip()
        chat_history = data.get("chat_history", [])
        attachments = data.get("attachments", [])  # List of {filename, content_type, blob_url or upload pending}

        if not message:
            raise HTTPException(status_code=400, detail="message is required")

        # Build the ARGUS assistant system prompt
        system_prompt = """You are ARGUS AI Assistant, a helpful AI that helps users interact with the ARGUS Document Intelligence Platform.

You have access to ARGUS tools that allow you to:
- List and search documents processed by ARGUS
- Get detailed information about specific documents
- Chat about document content and ask questions
- View and create dataset configurations
- Generate upload URLs for new documents

When users ask about documents, data extraction, or document processing, use the appropriate tools.
Be helpful, concise, and accurate. If you need to look up information, use the tools available.

IMPORTANT: When users attach and upload files through this chat, the files are AUTOMATICALLY queued for processing by the system. DO NOT call argus_process_document_url after a file upload - the processing is already triggered automatically. Only use argus_process_document_url if you need to re-process an existing document or process a document that was uploaded through other means.

If the user has attached files, inform them that the files have been uploaded and will be automatically processed. They can check the status later using the list_documents or get_document tools."""

        # Assemble the conversation for the Agent Framework
        af_messages = []
        for hist in chat_history[-10:]:  # Last 10 messages
            content_text = hist.get("content", "")
            if not content_text:
                continue
            role = (hist.get("role") or "user").lower()
            if role == "assistant":
                af_messages.append(assistant_message([text_content(content_text)]))
            else:
                af_messages.append(user_message([text_content(content_text)]))

        # Add attachment context if any
        if attachments:
            attachment_details = []
            for a in attachments:
                detail = f"- {a.get('filename', 'unknown')}"
                if a.get("document_id"):
                    detail += f" (document_id: {a.get('document_id')})"
                attachment_details.append(detail)
            attachment_info = (
                "\n\n[User has uploaded the following files which are now being automatically processed:\n"
                + "\n".join(attachment_details)
                + "\n\nYou can use the document_id to check processing status with argus_get_document.]"
            )
            message = message + attachment_info

        af_messages.append(user_message([text_content(message)]))

        # Run the agent with ARGUS tools (Agent Framework auto-invokes tool calls)
        result = await run_chat(
            af_messages,
            instructions=system_prompt,
            tools=MCP_CHAT_TOOLS,
        )

        final_response = result.text or "I apologize, but I couldn't generate a response. Please try again."

        return {
            "response": final_response,
            "tool_calls": result.tool_calls,
            "finish_reason": result.finish_reason,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in MCP chat endpoint: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"MCP chat processing failed: {str(e)}")


async def _execute_mcp_tool(tool_name: str, arguments: dict) -> Any:
    """Execute an MCP tool and return the result"""
    try:
        data_container = get_data_container()
        conf_container = get_conf_container()
        blob_service_client = get_blob_service_client()

        if tool_name == "argus_list_documents":
            dataset = arguments.get("dataset")
            status = arguments.get("status")
            limit = arguments.get("limit", 50)

            if not data_container:
                return {"error": "Data container not available"}

            if dataset:
                items = list(
                    data_container.query_items(
                        query="SELECT * FROM c WHERE c.dataset = @dataset AND NOT IS_DEFINED(c.kind)",
                        parameters=[{"name": "@dataset", "value": dataset}],
                        enable_cross_partition_query=True,
                    )
                )
            else:
                items = list(
                    data_container.query_items(
                        query="SELECT * FROM c WHERE NOT IS_DEFINED(c.kind)", enable_cross_partition_query=True
                    )
                )

            documents = []
            for item in items:
                doc_status = _get_document_status(item)
                if status and doc_status != status:
                    continue
                documents.append(
                    {
                        "id": item.get("id"),
                        "filename": item.get("file_name") or item.get("filename") or item.get("id", "").split("/")[-1],
                        "dataset": item.get("dataset", "default-dataset"),
                        "status": doc_status,
                        "created_at": _get_document_timestamp(item),
                    }
                )

            documents.sort(key=lambda d: d.get("created_at") or "", reverse=True)
            documents = documents[:limit]
            return {"documents": documents, "count": len(documents)}

        elif tool_name == "argus_get_document":
            document_id = arguments.get("document_id")
            if not document_id:
                return {"error": "document_id is required"}

            if not data_container:
                return {"error": "Data container not available"}

            items = list(
                data_container.query_items(
                    query="SELECT * FROM c WHERE c.id = @id",
                    parameters=[{"name": "@id", "value": document_id}],
                    enable_cross_partition_query=True,
                )
            )

            if not items:
                return {"error": "Document not found"}

            doc = items[0]
            return {
                "id": doc.get("id"),
                "filename": doc.get("file_name") or doc.get("filename"),
                "dataset": doc.get("dataset", "default-dataset"),
                "status": _get_document_status(doc),
                "ocr_text": doc.get("extracted_data", {}).get("ocr_output", "")[:2000],
                "extraction": doc.get("extracted_data", {}).get("gpt_extraction_output"),
                "summary": doc.get("extracted_data", {}).get("gpt_summary_output"),
                "evaluation": doc.get("extracted_data", {}).get("gpt_extraction_output_with_evaluation"),
            }

        elif tool_name == "argus_chat_with_document":
            document_id = arguments.get("document_id")
            question = arguments.get("question")

            if not document_id or not question:
                return {"error": "document_id and question are required"}

            # Use the existing chat endpoint logic
            class MockRequest:
                async def json(self):
                    return {"document_id": document_id, "message": question, "chat_history": []}

            result = await chat_with_document(MockRequest())
            return {"answer": result.get("response")}

        elif tool_name == "argus_list_datasets":
            if not conf_container:
                return {"error": "Configuration container not available"}

            datasets = set()
            try:
                config_item = conf_container.read_item(item="configuration", partition_key="configuration")
                datasets.update((config_item.get("datasets") or {}).keys())
            except Exception as e:
                logger.warning(f"Could not read configuration datasets: {e}")

            # Also check blob storage for datasets
            if blob_service_client:
                container_name = os.getenv("STORAGE_CONTAINER_NAME", "datasets")
                container_client = blob_service_client.get_container_client(container_name)
                blobs = container_client.list_blobs()
                blob_datasets = set()
                for blob in blobs:
                    parts = blob.name.split("/")
                    if len(parts) > 1:
                        blob_datasets.add(parts[0])
                datasets = datasets | blob_datasets

            return {"datasets": sorted(datasets)}

        elif tool_name == "argus_get_dataset_config":
            dataset_name = arguments.get("dataset_name")
            if not dataset_name:
                return {"error": "dataset_name is required"}

            prompt, schema = fetch_model_prompt_and_schema(dataset_name)
            return {
                "dataset": dataset_name,
                "system_prompt": prompt[:1000] if prompt else None,
                "output_schema": schema,
            }

        elif tool_name == "argus_search_documents":
            query_text = arguments.get("query", "")
            dataset = arguments.get("dataset")
            limit = arguments.get("limit", 20)

            if not data_container:
                return {"error": "Data container not available"}

            # Search by filename (Cosmos DB doesn't support full-text search easily)
            cosmos_query = (
                "SELECT c.id, c.file_name, c.dataset, c.state FROM c "
                f"WHERE CONTAINS(LOWER(c.file_name), LOWER('{query_text}'))"
            )
            if dataset:
                cosmos_query += f" AND c.dataset = '{dataset}'"
            cosmos_query += f" OFFSET 0 LIMIT {limit}"

            items = list(data_container.query_items(query=cosmos_query, enable_cross_partition_query=True))
            results = [
                {
                    "id": item.get("id"),
                    "filename": item.get("file_name"),
                    "dataset": item.get("dataset", "default-dataset"),
                    "status": _get_document_status(item),
                }
                for item in items
            ]
            return {"results": results, "count": len(results), "query": query_text}

        elif tool_name == "argus_get_extraction":
            document_id = arguments.get("document_id")
            if not document_id:
                return {"error": "document_id is required"}

            if not data_container:
                return {"error": "Data container not available"}

            items = list(
                data_container.query_items(
                    query="SELECT c.extracted_data.gpt_extraction_output FROM c WHERE c.id = @id",
                    parameters=[{"name": "@id", "value": document_id}],
                    enable_cross_partition_query=True,
                )
            )

            if not items:
                return {"error": "Document not found"}

            return {"extraction": items[0].get("gpt_extraction_output")}

        elif tool_name == "argus_process_document_url":
            blob_url = arguments.get("blob_url")
            dataset = arguments.get("dataset", "default-dataset")

            if not blob_url:
                return {"error": "blob_url is required"}

            # Queue for processing
            event_data = {
                "url": blob_url,
                "processing_options": {
                    "run_ocr": True,
                    "run_gpt_vision": True,
                    "run_summary": True,
                    "run_evaluation": True,
                },
            }
            asyncio.create_task(process_blob_event(blob_url, event_data))

            return {"status": "queued", "blob_url": blob_url, "dataset": dataset}

        elif tool_name == "argus_get_upload_url":
            filename = arguments.get("filename")
            dataset = arguments.get("dataset", "default-dataset")

            if not filename:
                return {"error": "filename is required"}

            result = await get_upload_url(filename, dataset)
            return result

        elif tool_name == "argus_create_dataset":
            dataset_name = arguments.get("dataset_name")
            system_prompt = arguments.get("system_prompt")
            output_schema = arguments.get("output_schema")
            max_pages = arguments.get("max_pages_per_chunk", 10)

            if not dataset_name or not system_prompt or output_schema is None:
                return {"error": "dataset_name, system_prompt, and output_schema are required"}

            # Validate dataset name (alphanumeric and hyphens only)
            import re

            if not re.match(r"^[a-zA-Z0-9-]+$", dataset_name):
                return {"error": "dataset_name must contain only alphanumeric characters and hyphens"}

            result = await create_dataset(dataset_name, system_prompt, output_schema, max_pages)
            return result

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        logger.error(f"Error executing MCP tool {tool_name}: {e}")
        return {"error": str(e)}


async def submit_correction(document_id: str, request: Request):
    """
    Submit a human correction for a document's extraction.
    Stores the correction alongside the original extraction for audit trail.
    """
    try:
        data = await request.json()
        corrected_data = data.get("corrected_data")
        correction_notes = data.get("notes", "")
        corrector_id = data.get("corrector_id", "anonymous")

        if corrected_data is None:
            raise HTTPException(status_code=400, detail="corrected_data is required")

        # Get the document from Cosmos DB
        data_container = get_data_container()
        if not data_container:
            raise HTTPException(status_code=503, detail="Data container not available")

        try:
            # Fetch the document using a parameterized query (prevents NoSQL injection)
            items = list(
                data_container.query_items(
                    query="SELECT * FROM c WHERE c.id = @id",
                    parameters=[{"name": "@id", "value": document_id}],
                    enable_cross_partition_query=True,
                )
            )

            if not items:
                raise HTTPException(status_code=404, detail="Document not found")

            document = items[0]
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error fetching document {document_id}: {e}")
            raise HTTPException(status_code=404, detail="Document not found")

        # Get the original GPT extraction
        extracted_data = document.get("extracted_data", {})
        original_extraction = extracted_data.get("gpt_extraction_output")

        # Initialize corrections history if not present
        if "corrections" not in document:
            document["corrections"] = []

        # Create correction record
        correction_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "corrector_id": corrector_id,
            "notes": correction_notes,
            "original_data": copy.deepcopy(original_extraction) if original_extraction else None,
            "corrected_data": corrected_data,
            "correction_number": len(document["corrections"]) + 1,
        }

        # Append to corrections history
        document["corrections"].append(correction_record)

        # Update the current extraction with the corrected data
        if "extracted_data" not in document:
            document["extracted_data"] = {}
        document["extracted_data"]["gpt_extraction_output"] = corrected_data

        # Mark that this document has been human-corrected
        document["human_corrected"] = True
        document["last_correction_timestamp"] = datetime.utcnow().isoformat()

        # Upsert the document back to Cosmos DB
        data_container.upsert_item(document)

        logger.info(f"Correction submitted for document {document_id} by {corrector_id}")

        return {
            "status": "success",
            "message": "Correction submitted successfully",
            "document_id": document_id,
            "correction_number": correction_record["correction_number"],
            "timestamp": correction_record["timestamp"],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting correction for document {document_id}: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to submit correction: {str(e)}")


async def get_correction_history(document_id: str):
    """
    Get the correction history for a document.
    Returns all corrections made to the document along with the original extraction.
    """
    try:
        # Get the document from Cosmos DB
        data_container = get_data_container()
        if not data_container:
            raise HTTPException(status_code=503, detail="Data container not available")

        try:
            # Fetch the document using a parameterized query (prevents NoSQL injection)
            items = list(
                data_container.query_items(
                    query="SELECT * FROM c WHERE c.id = @id",
                    parameters=[{"name": "@id", "value": document_id}],
                    enable_cross_partition_query=True,
                )
            )

            if not items:
                raise HTTPException(status_code=404, detail="Document not found")

            document = items[0]
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error fetching document {document_id}: {e}")
            raise HTTPException(status_code=404, detail="Document not found")

        # Get current extraction and corrections
        extracted_data = document.get("extracted_data", {})
        current_extraction = extracted_data.get("gpt_extraction_output")
        corrections = document.get("corrections", [])

        return {
            "document_id": document_id,
            "human_corrected": document.get("human_corrected", False),
            "last_correction_timestamp": document.get("last_correction_timestamp"),
            "current_extraction": current_extraction,
            "corrections_count": len(corrections),
            "corrections": corrections,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting correction history for document {document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get correction history: {str(e)}")


async def get_concurrency_diagnostics():
    """Get diagnostic information about Logic App Manager setup"""
    try:
        logic_app_manager = get_logic_app_manager()

        diagnostics = {
            "timestamp": datetime.utcnow().isoformat(),
            "logic_app_manager_initialized": logic_app_manager is not None,
            "environment_variables": {
                "AZURE_SUBSCRIPTION_ID": bool(os.getenv("AZURE_SUBSCRIPTION_ID")),
                "AZURE_RESOURCE_GROUP_NAME": bool(os.getenv("AZURE_RESOURCE_GROUP_NAME")),
                "LOGIC_APP_NAME": bool(os.getenv("LOGIC_APP_NAME")),
            },
            "environment_values": {
                "AZURE_SUBSCRIPTION_ID": os.getenv("AZURE_SUBSCRIPTION_ID", "NOT_SET")[:8] + "..."
                if os.getenv("AZURE_SUBSCRIPTION_ID")
                else "NOT_SET",
                "AZURE_RESOURCE_GROUP_NAME": os.getenv("AZURE_RESOURCE_GROUP_NAME", "NOT_SET"),
                "LOGIC_APP_NAME": os.getenv("LOGIC_APP_NAME", "NOT_SET"),
            },
        }

        if logic_app_manager:
            diagnostics["logic_app_manager_enabled"] = logic_app_manager.enabled
            diagnostics["subscription_id_configured"] = bool(logic_app_manager.subscription_id)
            diagnostics["resource_group_configured"] = bool(logic_app_manager.resource_group_name)
            diagnostics["logic_app_name_configured"] = bool(logic_app_manager.logic_app_name)

            # Try to test Azure credentials
            try:
                diagnostics["azure_credentials_test"] = "Testing..."
                # Simple credential test
                DefaultAzureCredential()
                # This will fail if credentials are not working, but won't actually call Azure
                diagnostics["azure_credentials_available"] = True
            except Exception as e:
                diagnostics["azure_credentials_test"] = f"Failed: {str(e)}"
                diagnostics["azure_credentials_available"] = False
        else:
            diagnostics["logic_app_manager_enabled"] = False
            diagnostics["reason"] = "LogicAppManager not initialized"

        return diagnostics

    except Exception as e:
        logger.error(f"Error getting concurrency diagnostics: {e}")
        return {"error": str(e), "timestamp": datetime.utcnow().isoformat(), "logic_app_manager_initialized": False}


# ============================================================================
# Document Management Endpoints
# ============================================================================


def _document_list_item(item: dict, *, include_details: bool) -> dict:
    """Transform a stored document without changing the list endpoint contract."""
    extracted_data = item.get("extracted_data", {}) if include_details else {}
    stored_properties = item.get("properties") if isinstance(item.get("properties"), dict) else {}
    if include_details:
        properties = stored_properties
    else:
        properties = {
            "blob_size": item.get("property_blob_size", stored_properties.get("blob_size")),
            "request_timestamp": item.get(
                "property_request_timestamp",
                stored_properties.get("request_timestamp"),
            ),
            "num_pages": item.get("property_num_pages", stored_properties.get("num_pages")),
            "total_time_seconds": item.get(
                "property_total_time_seconds",
                stored_properties.get("total_time_seconds"),
            ),
            "cost": item.get("property_cost", stored_properties.get("cost")),
            "flag": item.get("property_flag", stored_properties.get("flag")),
            "tier": item.get("property_tier", stored_properties.get("tier")),
        }
        properties = {key: value for key, value in properties.items() if value is not None}

    return {
        "id": item.get("id"),
        "filename": item.get("file_name") or item.get("filename") or item.get("id", "").split("/")[-1],
        "dataset": item.get("dataset", "default-dataset"),
        "status": _get_document_status(item),
        "created_at": item.get("created_at") or properties.get("request_timestamp"),
        "updated_at": item.get("updated_at") or item.get("created_at") or properties.get("request_timestamp"),
        "processing_time": item.get("processing_time") or item.get("processing_times", {}).get("total"),
        "model": item.get("model"),
        "ocr_text": item.get("ocr_response") or item.get("ocr_text") if include_details else None,
        "gpt_extraction": extracted_data.get("gpt_extraction_output"),
        "evaluation": item.get("evaluation_results") or item.get("evaluation") if include_details else None,
        "summary": item.get("summary") if include_details else None,
        "errors": item.get("errors"),
        "num_pages": item.get("num_pages") or properties.get("num_pages"),
        "properties": properties,
        "state": item.get("state", {}),
        "extracted_data": extracted_data,
        "processing_options": item.get("processing_options", {}),
    }


async def list_documents(
    dataset: str = None,
    limit: int = 500,
    continuation: str | None = None,
    lightweight: bool = False,
):
    """List documents, preserving the legacy response unless lightweight paging is requested."""
    try:
        data_container = get_data_container()
        if not data_container:
            raise HTTPException(status_code=503, detail="Data container not available")

        parameters = [{"name": "@dataset", "value": dataset}] if dataset else []
        where_clause = " WHERE c.dataset = @dataset AND NOT IS_DEFINED(c.kind)" if dataset else " WHERE NOT IS_DEFINED(c.kind)"

        if not lightweight:
            items = list(
                data_container.query_items(
                    query=f"SELECT * FROM c{where_clause}",
                    parameters=parameters,
                    enable_cross_partition_query=True,
                )
            )
            documents = [_document_list_item(item, include_details=True) for item in items]
            return {"documents": documents, "count": len(documents), "continuation": None}

        try:
            page_size = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            page_size = 500

        continuation = continuation or None

        projection = (
            "SELECT c.id, c.dataset, c.file_name, c.filename, c.state, "
            "c.errors, c.model, c.created_at, c.updated_at, c.processing_time, "
            "c.processing_times, c.processing_options, c.num_pages, "
            "c.properties.blob_size AS property_blob_size, "
            "c.properties.request_timestamp AS property_request_timestamp, "
            "c.properties.num_pages AS property_num_pages, "
            "c.properties.total_time_seconds AS property_total_time_seconds, "
            "c.properties.cost AS property_cost, "
            "c.properties.flag AS property_flag, "
            "c.properties.tier AS property_tier FROM c"
        )
        query = f"{projection}{where_clause} ORDER BY c._ts DESC"
        iterator = data_container.query_items(
            query=query,
            parameters=parameters,
            max_item_count=page_size,
            enable_cross_partition_query=True,
        )
        page_iter = iterator.by_page(continuation_token=continuation)
        page = next(page_iter, [])
        items = list(page)
        next_continuation = getattr(page_iter, "continuation_token", None)

        total_items = list(
            data_container.query_items(
                query=f"SELECT VALUE COUNT(1) FROM c{where_clause}",
                parameters=parameters,
                enable_cross_partition_query=True,
            )
        )
        total_count = int(total_items[0]) if total_items else 0
        documents = [_document_list_item(item, include_details=False) for item in items]
        return {"documents": documents, "count": total_count, "continuation": next_continuation}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")


async def list_flagged_documents():
    """List lightweight document records where properties.flag.flagged is true."""
    try:
        data_container = get_data_container()
        if not data_container:
            raise HTTPException(status_code=503, detail="Data container not available")

        query = """
            SELECT c.id, c.dataset, c.file_name, c.filename, c.properties
            FROM c
            WHERE IS_DEFINED(c.properties.flag.flagged) AND c.properties.flag.flagged = true
        """
        items = list(data_container.query_items(query=query, enable_cross_partition_query=True))

        flagged_documents = []
        for item in items:
            properties = item.get("properties") or {}
            flag = properties.get("flag") or {}
            image_quality_summary = _summarize_image_quality(properties.get("image_quality"))
            email = flag.get("email") if isinstance(flag.get("email"), dict) else None

            flagged_item = {
                "id": item.get("id"),
                "dataset": _get_document_dataset(item),
                "filename": _get_document_filename(item),
                "reasons": _as_reason_list(flag.get("reasons")),
                "stage": flag.get("stage"),
                "flagged_at": flag.get("flagged_at"),
                "email_sent": bool(email and email.get("sent_mock")),
            }
            if image_quality_summary:
                flagged_item["image_quality_summary"] = image_quality_summary
            flagged_documents.append(flagged_item)

        return flagged_documents

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error listing flagged documents: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to list flagged documents: {str(e)}")


def _cost_region() -> str:
    """Region used for cost pricing lookups (mirrors blob_processing._cost_region)."""
    return os.getenv("AZURE_LOCATION") or os.getenv("AZURE_REGION") or "eastus2"


def _price_chat_usage(result, container=None) -> dict:
    """Turn a run_chat ChatResult's token usage into a priced usage object.

    Returns input/output/total tokens, the model used, per-side and total USD, the
    pricing source (azure_retail|fallback) and region. USD fields are None when token
    counts are unavailable so callers can distinguish "no usage" from "$0".
    """
    input_tokens = getattr(result, "input_tokens", None)
    output_tokens = getattr(result, "output_tokens", None)
    total_tokens = getattr(result, "total_tokens", None)
    model = getattr(result, "model", None) or os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME", "")
    region = _cost_region()

    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens
        if total_tokens is not None
        else (
            (input_tokens or 0) + (output_tokens or 0)
            if (input_tokens is not None or output_tokens is not None)
            else None
        ),
        "model": model,
        "region": region,
        "input_usd": None,
        "output_usd": None,
        "total_usd": None,
        "pricing_source": None,
    }

    if input_tokens is None and output_tokens is None:
        return usage

    try:
        pricing = get_pricing(model, region, container)
        input_usd = (input_tokens or 0) / 1000.0 * pricing.input_per_1k
        output_usd = (output_tokens or 0) / 1000.0 * pricing.output_per_1k
        usage["input_usd"] = round(input_usd, 6)
        usage["output_usd"] = round(output_usd, 6)
        usage["total_usd"] = round(input_usd + output_usd, 6)
        usage["pricing_source"] = pricing.source
    except Exception as e:  # pricing must never break email generation
        logger.warning("Could not price email token usage: %s", e)

    return usage


def _flag_email_model() -> str | None:
    """Deployment used for flagged-document email drafting.

    Emails are short, templated drafts that don't need the premium reasoning model,
    so default to the cheaper summary/mini deployment. Precedence:
    FLAG_EMAIL_MODEL_DEPLOYMENT_NAME > SUMMARY_MODEL_DEPLOYMENT_NAME > default (None
    lets run_chat fall back to AZURE_OPENAI_MODEL_DEPLOYMENT_NAME).
    """
    return os.getenv("FLAG_EMAIL_MODEL_DEPLOYMENT_NAME") or os.getenv("SUMMARY_MODEL_DEPLOYMENT_NAME") or None


async def generate_flag_email(document_id: str, request: Request):
    """Generate a flagged-document email draft using the configured prompt template."""
    try:
        data_container = get_data_container()
        if not data_container:
            raise HTTPException(status_code=503, detail="Data container not available")

        try:
            request_body = await request.json()
        except Exception:
            request_body = {}

        if request_body is None:
            request_body = {}
        if not isinstance(request_body, dict):
            raise HTTPException(status_code=400, detail="Request body must be an object")

        prompt_template = request_body.get("prompt_template")
        if prompt_template is not None and not isinstance(prompt_template, str):
            raise HTTPException(status_code=400, detail="prompt_template must be a string")

        document = _get_document_by_id(data_container, document_id)
        if not _document_is_flagged(document):
            raise HTTPException(status_code=400, detail="Document is not flagged")

        flag_email_config = _get_flag_email_config()
        template = (
            prompt_template or flag_email_config.get("prompt_template") or DEFAULT_FLAG_EMAIL_PROMPT_TEMPLATE
        ).strip()
        instructions = (
            f"{_render_flag_email_template(template, document)}\n\n"
            'Return only JSON with string fields "subject" and "body".'
        )

        result = await run_chat(
            [user_message([text_content("Draft the flagged-document email now.")])],
            instructions=instructions,
            temperature=0.2,
            max_tokens=900,
            model=_flag_email_model(),
        )

        draft = _parse_email_draft(result.text, document)
        draft["to"] = (
            _get_uploader_email(document) or flag_email_config.get("to_fallback") or DEFAULT_FLAG_EMAIL_TO_FALLBACK
        )
        draft["from"] = flag_email_config.get("from") or DEFAULT_FLAG_EMAIL_FROM
        draft["usage"] = _price_chat_usage(result, data_container)
        return draft

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error generating flag email for document %s: %s", document_id, e)
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to generate flag email: {str(e)}")


async def send_flag_email(request: Request):
    """Mock-send a flagged-document email and persist the send metadata."""
    try:
        data_container = get_data_container()
        if not data_container:
            raise HTTPException(status_code=503, detail="Data container not available")

        try:
            request_body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Request body must be valid JSON")

        if not isinstance(request_body, dict):
            raise HTTPException(status_code=400, detail="Request body must be an object")

        document_id = str(request_body.get("document_id") or "").strip()
        subject = str(request_body.get("subject") or "").strip()
        body = str(request_body.get("body") or "").strip()
        missing_fields = [
            field for field, value in (("document_id", document_id), ("subject", subject), ("body", body)) if not value
        ]
        if missing_fields:
            raise HTTPException(status_code=400, detail=f"Missing required fields: {', '.join(missing_fields)}")

        document = _get_document_by_id(data_container, document_id)
        if not _document_is_flagged(document):
            raise HTTPException(status_code=400, detail="Document is not flagged")

        flag_email_config = _get_flag_email_config()
        to_address = str(request_body.get("to") or "").strip()
        if not to_address:
            to_address = (
                _get_uploader_email(document) or flag_email_config.get("to_fallback") or DEFAULT_FLAG_EMAIL_TO_FALLBACK
            )

        message_id = f"mock-{uuid.uuid4()}"
        sent_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "[MOCK EMAIL] would send from %s to %s for document %s with subject %r (message_id=%s)",
            flag_email_config.get("from") or DEFAULT_FLAG_EMAIL_FROM,
            to_address,
            document_id,
            subject,
            message_id,
        )

        # MOCK EMAIL: replace this block with a real provider integration when email delivery is enabled.
        properties = document.setdefault("properties", {})
        flag = properties.setdefault("flag", {})
        flag["email"] = {
            "sent_mock": True,
            "to": to_address,
            "subject": subject,
            "body": body,
            "sent_at": sent_at,
            "message_id": message_id,
        }
        data_container.upsert_item(document)

        return {"success": True, "message_id": message_id, "mock": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error mock-sending flag email: %s", e)
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to mock-send flag email: {str(e)}")


def _get_document_status(item: dict) -> str:
    """Determine document status from state"""
    state = item.get("state", {})
    if state.get("error") or item.get("errors"):
        return "failed"
    if state.get("processing_completed") or state.get("gpt_summary_completed") or state.get("gpt_evaluation_completed"):
        return "completed"
    if state.get("ocr_completed") or state.get("gpt_extraction_completed"):
        return "processing"
    return "pending"


def _get_document_timestamp(item: dict) -> str | None:
    """Best-effort creation timestamp; documents store it at properties.request_timestamp."""
    return (
        item.get("request_timestamp") or item.get("properties", {}).get("request_timestamp") or item.get("created_at")
    )


def _merge_field_confidence(properties: dict) -> dict:
    """Flatten Content Understanding per-chunk field confidence into a single
    ``{field_path: score}`` map, keeping the lowest score on collision so the UI
    surfaces the most conservative confidence per field."""
    merged: dict[str, float] = {}
    cu_conf = (properties or {}).get("content_understanding_confidence") or {}
    if isinstance(cu_conf, dict):
        for chunk in cu_conf.values():
            if not isinstance(chunk, dict):
                continue
            for path, score in chunk.items():
                if isinstance(score, (int, float)) and not isinstance(score, bool):
                    score = float(score)
                    merged[path] = min(merged[path], score) if path in merged else score
    return merged


def _summarize_ocr_confidence(properties: dict) -> dict | None:
    """Aggregate per-chunk OCR word-confidence stats into a document-level summary."""
    persisted = (properties or {}).get("ocr_confidence")
    if isinstance(persisted, dict) and int(persisted.get("n_words") or 0) > 0:
        return {
            "n_words": int(persisted.get("n_words") or 0),
            "mean": persisted.get("mean"),
            "frac_low": persisted.get("frac_low"),
            "min": persisted.get("min"),
            "word_min": persisted.get("word_min"),
        }

    chunks = (properties or {}).get("_ocr_conf_chunks") or []
    if not isinstance(chunks, list) or not chunks:
        return None
    total_words = 0
    weighted_mean = 0.0
    weighted_low = 0.0
    min_conf = None
    word_min = None
    for stat in chunks:
        if not isinstance(stat, dict):
            continue
        n = int(stat.get("n_words") or 0)
        if n <= 0:
            continue
        total_words += n
        weighted_mean += float(stat.get("mean") or 0.0) * n
        weighted_low += float(stat.get("frac_low") or 0.0) * n
        cmin = stat.get("min")
        if isinstance(cmin, (int, float)):
            min_conf = cmin if min_conf is None else min(min_conf, cmin)
        word_min = stat.get("word_min", word_min)
    if total_words <= 0:
        return None
    return {
        "n_words": total_words,
        "mean": round(weighted_mean / total_words, 4),
        "frac_low": round(weighted_low / total_words, 4),
        "min": round(min_conf, 4) if isinstance(min_conf, (int, float)) else None,
        "word_min": word_min,
    }


async def get_document(document_id: str):
    """Get a specific document by ID"""
    try:
        data_container = get_data_container()
        if not data_container:
            raise HTTPException(status_code=503, detail="Data container not available")

        # Fetch the document using a parameterized query (prevents NoSQL injection)
        items = list(
            data_container.query_items(
                query="SELECT * FROM c WHERE c.id = @id",
                parameters=[{"name": "@id", "value": document_id}],
                enable_cross_partition_query=True,
            )
        )

        if not items:
            raise HTTPException(status_code=404, detail="Document not found")

        item = items[0]

        # Transform to expected format
        doc = {
            "id": item.get("id"),
            "filename": item.get("file_name") or item.get("filename") or item.get("id", "").split("/")[-1],
            "dataset": item.get("dataset", "default-dataset"),
            "status": _get_document_status(item),
            "created_at": _get_document_timestamp(item),
            "updated_at": item.get("updated_at") or _get_document_timestamp(item),
            "processing_time": item.get("processing_time") or item.get("processing_times", {}).get("total"),
            "model": item.get("model"),
            "ocr_text": item.get("ocr_response") or item.get("ocr_text"),
            "gpt_extraction": item.get("extracted_data", {}).get("gpt_extraction_output"),
            "evaluation": item.get("evaluation_results") or item.get("evaluation"),
            "summary": item.get("summary"),
            "errors": item.get("errors"),
            "num_pages": item.get("num_pages") or item.get("properties", {}).get("num_pages"),
            "properties": item.get("properties", {}),
            "state": item.get("state", {}),
            "extracted_data": item.get("extracted_data", {}),
            "model_input": item.get("model_input", {}),
            "processing_options": item.get("processing_options", {}),
            "blob_url": item.get("blob_url"),
            "human_corrected": item.get("human_corrected", False),
            "corrections": item.get("corrections", []),
            "field_confidence": _merge_field_confidence(item.get("properties", {})),
            "ocr_confidence": _summarize_ocr_confidence(item.get("properties", {})),
        }

        return doc

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document {document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get document: {str(e)}")


async def delete_document(document_id: str):
    """Delete a document by ID"""
    try:
        data_container = get_data_container()
        if not data_container:
            raise HTTPException(status_code=503, detail="Data container not available")

        # First find the document to get its info (parameterized to prevent injection)
        items = list(
            data_container.query_items(
                query="SELECT * FROM c WHERE c.id = @id",
                parameters=[{"name": "@id", "value": document_id}],
                enable_cross_partition_query=True,
            )
        )

        if not items:
            raise HTTPException(status_code=404, detail="Document not found")

        # Delete using the document's actual partition key (dataset for migrated
        # documents, or the legacy "undefined" partition for older ones).
        item = items[0]
        partition_key = item.get("partitionKey")
        data_container.delete_item(item=document_id, partition_key=partition_key if partition_key is not None else {})

        # Also try to delete from blob storage
        blob_name = item.get("properties", {}).get("blob_name") or item.get("file_name")
        if blob_name:
            try:
                blob_service_client = get_blob_service_client()
                if blob_service_client:
                    container_name = os.getenv("STORAGE_CONTAINER_NAME", "datasets")
                    container_client = blob_service_client.get_container_client(container_name)
                    blob_client = container_client.get_blob_client(blob_name)
                    if blob_client.exists():
                        blob_client.delete_blob()
                        logger.info(f"Deleted blob: {blob_name}")
            except Exception as blob_error:
                logger.warning(f"Could not delete blob {blob_name}: {blob_error}")

        return {"status": "success", "message": f"Document {document_id} deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting document {document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {str(e)}")


async def reprocess_document(document_id: str, background_tasks: BackgroundTasks):
    """Reprocess a document by ID"""
    try:
        data_container = get_data_container()
        blob_service_client = get_blob_service_client()

        if not data_container:
            raise HTTPException(status_code=503, detail="Data container not available")

        # Find the document (parameterized to prevent injection)
        items = list(
            data_container.query_items(
                query="SELECT * FROM c WHERE c.id = @id",
                parameters=[{"name": "@id", "value": document_id}],
                enable_cross_partition_query=True,
            )
        )

        if not items:
            raise HTTPException(status_code=404, detail="Document not found")

        item = items[0]

        # Get blob name from document properties
        blob_name = item.get("properties", {}).get("blob_name") or item.get("file_name")

        if not blob_name:
            raise HTTPException(status_code=400, detail="Document does not have a blob reference for reprocessing")

        # Construct the blob URL
        if blob_service_client:
            storage_account = os.getenv("STORAGE_ACCOUNT_NAME", "")
            container_name = os.getenv("STORAGE_CONTAINER_NAME", "datasets")
            blob_url = f"https://{storage_account}.blob.core.windows.net/{container_name}/{blob_name}"
        else:
            raise HTTPException(status_code=503, detail="Blob storage not available for reprocessing")

        # Reset document state
        item["state"] = {
            "file_landed": True,
            "ocr_completed": False,
            "gpt_extraction": False,
            "gpt_extraction_completed": False,
            "gpt_evaluation": False,
            "gpt_evaluation_completed": False,
            "gpt_summary": False,
            "gpt_summary_completed": False,
            "processing_completed": False,
            "finished": False,
            "error": False,
        }
        item["errors"] = []
        data_container.upsert_item(item)

        # Queue for reprocessing
        background_tasks.add_task(process_blob_event, blob_url, {"url": blob_url})

        return {"status": "success", "message": f"Document {document_id} queued for reprocessing"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reprocessing document {document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reprocess document: {str(e)}")


# ============================================================================
# Dataset Management Endpoints
# ============================================================================


async def list_datasets():
    """List all available datasets"""
    try:
        conf_container = get_conf_container()
        blob_service_client = get_blob_service_client()

        datasets = []

        # Get datasets from configuration
        if conf_container:
            try:
                config_item = conf_container.read_item(item="configuration", partition_key="configuration")
                config_datasets = config_item.get("datasets", {})
                for name, config in config_datasets.items():
                    datasets.append(
                        {
                            "name": name,
                            "has_system_prompt": bool(config.get("system_prompt")),
                            "has_output_schema": bool(config.get("output_schema")),
                            "has_ground_truth": bool(config.get("ground_truth")),
                            "description": config.get("description", ""),
                        }
                    )
            except Exception as e:
                logger.warning(f"Could not read configuration: {e}")

        # Also check blob storage for dataset folders
        if blob_service_client:
            try:
                container_name = os.getenv("STORAGE_CONTAINER_NAME", "datasets")
                container_client = blob_service_client.get_container_client(container_name)

                # List blobs to find dataset folders - the structure is {dataset-name}/{file.pdf}
                blob_list = container_client.list_blobs()
                seen_datasets = set()
                for blob in blob_list:
                    # Extract dataset name from path like "dataset-name/file.pdf"
                    parts = blob.name.split("/")
                    if len(parts) >= 2:
                        dataset_name = parts[0]
                        if dataset_name and dataset_name not in seen_datasets:
                            seen_datasets.add(dataset_name)
                            # Add if not already in list from config
                            if not any(d["name"] == dataset_name for d in datasets):
                                datasets.append(
                                    {
                                        "name": dataset_name,
                                        "has_system_prompt": False,
                                        "has_output_schema": False,
                                        "has_ground_truth": False,
                                        "description": "",
                                    }
                                )
            except Exception as e:
                logger.warning(f"Could not list blob datasets: {e}")

        # Add default dataset if no datasets found
        if not datasets:
            datasets.append(
                {
                    "name": "default-dataset",
                    "has_system_prompt": False,
                    "has_output_schema": False,
                    "has_ground_truth": False,
                    "description": "Default dataset",
                }
            )

        return {"datasets": datasets}

    except Exception as e:
        logger.error(f"Error listing datasets: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list datasets: {str(e)}")


async def get_dataset_documents(dataset_name: str):
    """Get all documents for a specific dataset"""
    return await list_documents(dataset=dataset_name)


async def upload_file(dataset_name: str, request: Request, background_tasks: BackgroundTasks):
    """Upload a file to a dataset"""
    try:
        blob_service_client = get_blob_service_client()
        if not blob_service_client:
            raise HTTPException(status_code=503, detail="Blob storage not available")

        # Get form data
        form = await request.form()
        file = form.get("file")

        if not file:
            raise HTTPException(status_code=400, detail="No file provided")

        # Get processing options from query params
        run_ocr = request.query_params.get("run_ocr", "true").lower() == "true"
        run_gpt_vision = request.query_params.get("run_gpt_vision", "true").lower() == "true"
        run_summary = request.query_params.get("run_summary", "true").lower() == "true"
        run_evaluation = request.query_params.get("run_evaluation", "true").lower() == "true"

        # Read file content
        content = await file.read()
        filename = file.filename

        # Upload to blob storage - use 'datasets' container which is the actual container name
        container_name = os.getenv("STORAGE_CONTAINER_NAME", "datasets")
        blob_path = f"{dataset_name}/{filename}"

        container_client = blob_service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_path)

        blob_client.upload_blob(content, overwrite=True)

        # Get the blob URL
        blob_url = blob_client.url

        # Generate the document ID (same logic as blob_processing.py)
        document_id = blob_path.replace("/", "__")

        # Queue for processing if any processing options are enabled
        if run_ocr or run_gpt_vision or run_summary or run_evaluation:
            background_tasks.add_task(
                process_blob_event,
                blob_url,
                {
                    "url": blob_url,
                    "processing_options": {
                        "run_ocr": run_ocr,
                        "run_gpt_vision": run_gpt_vision,
                        "run_summary": run_summary,
                        "run_evaluation": run_evaluation,
                    },
                },
            )

        return {
            "message": "File uploaded successfully",
            "filename": filename,
            "blob_url": blob_url,
            "document_id": document_id,
            "dataset": dataset_name,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")


async def get_upload_url(filename: str, dataset_name: str = "default-dataset"):
    """Generate a SAS URL for direct blob upload"""
    from datetime import timedelta

    from azure.storage.blob import BlobSasPermissions, generate_blob_sas

    blob_service_client = get_blob_service_client()
    if not blob_service_client:
        raise HTTPException(status_code=503, detail="Blob storage not available")

    try:
        container_name = os.getenv("STORAGE_CONTAINER_NAME", "datasets")
        blob_path = f"{dataset_name}/{filename}"

        # Get account info
        account_name = blob_service_client.account_name

        # Get container client and blob client
        container_client = blob_service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_path)

        # Generate SAS token with write permission (valid for 1 hour)
        # Use user delegation key for SAS (more secure with managed identity)
        user_delegation_key = blob_service_client.get_user_delegation_key(
            key_start_time=datetime.utcnow(), key_expiry_time=datetime.utcnow() + timedelta(hours=1)
        )

        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_path,
            user_delegation_key=user_delegation_key,
            permission=BlobSasPermissions(write=True, create=True),
            expiry=datetime.utcnow() + timedelta(hours=1),
        )

        # Construct the full URL with SAS token
        upload_url = f"{blob_client.url}?{sas_token}"

        # Determine content type hint
        ext = filename.lower().split(".")[-1] if "." in filename else ""
        content_type_hints = {
            "pdf": "application/pdf",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "tiff": "image/tiff",
            "tif": "image/tiff",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        content_type = content_type_hints.get(ext, "application/octet-stream")

        return {
            "upload_url": upload_url,
            "method": "PUT",
            "headers": {"x-ms-blob-type": "BlockBlob", "Content-Type": content_type},
            "filename": filename,
            "dataset": dataset_name,
            "blob_path": blob_path,
            "expires_in": "1 hour",
            "instructions": [
                "Upload your file using HTTP PUT to the upload_url",
                "Set header 'x-ms-blob-type: BlockBlob'",
                f"Set header 'Content-Type: {content_type}'",
                "The file body should be the raw file content (not base64)",
                "After upload, ARGUS will automatically process the document",
            ],
            "curl_example": f"curl -X PUT -H 'x-ms-blob-type: BlockBlob' -H 'Content-Type: {content_type}' --data-binary @{filename} '<upload_url>'",
        }

    except Exception as e:
        logger.error(f"Error generating upload URL: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate upload URL: {str(e)}")


async def get_document_file(document_id: str):
    """Get the file/blob for a document to serve as preview"""
    import io

    from fastapi.responses import StreamingResponse

    try:
        data_container = get_data_container()
        blob_service_client = get_blob_service_client()

        if not data_container:
            raise HTTPException(status_code=503, detail="Data container not available")

        # Find the document (parameterized to prevent injection)
        items = list(
            data_container.query_items(
                query="SELECT * FROM c WHERE c.id = @id",
                parameters=[{"name": "@id", "value": document_id}],
                enable_cross_partition_query=True,
            )
        )

        if not items:
            raise HTTPException(status_code=404, detail="Document not found")

        item = items[0]

        # Get blob name from document properties
        blob_name = item.get("properties", {}).get("blob_name") or item.get("file_name")

        if not blob_name:
            raise HTTPException(status_code=404, detail="Document does not have a blob reference")

        if not blob_service_client:
            raise HTTPException(status_code=503, detail="Blob storage not available")

        # Get the blob content - use 'datasets' container
        container_name = os.getenv("STORAGE_CONTAINER_NAME", "datasets")
        container_client = blob_service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_name)

        if not blob_client.exists():
            raise HTTPException(status_code=404, detail="File not found in storage")

        # Download blob content
        blob_data = blob_client.download_blob().readall()

        # Determine content type
        filename = blob_name.split("/")[-1].lower()
        if filename.endswith(".pdf"):
            content_type = "application/pdf"
        elif filename.endswith(".png"):
            content_type = "image/png"
        elif filename.endswith((".jpg", ".jpeg")):
            content_type = "image/jpeg"
        elif filename.endswith(".tiff"):
            content_type = "image/tiff"
        elif filename.endswith(".docx"):
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif filename.endswith(".xlsx"):
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif filename.endswith(".pptx"):
            content_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        else:
            content_type = "application/octet-stream"

        return StreamingResponse(
            io.BytesIO(blob_data),
            media_type=content_type,
            headers={"Content-Disposition": f'inline; filename="{filename}"', "Cache-Control": "public, max-age=3600"},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document file {document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get document file: {str(e)}")


async def create_dataset(
    dataset_name: str, system_prompt: str, output_schema: dict, max_pages_per_chunk: int = 10
) -> Dict[str, Any]:
    """
    Create a new dataset configuration in ARGUS.

    Args:
        dataset_name: Name of the dataset (alphanumeric and hyphens only)
        system_prompt: The system prompt for document extraction
        output_schema: JSON schema defining the expected output structure
        max_pages_per_chunk: Maximum pages per processing chunk (default: 10)

    Returns:
        Dictionary with created dataset information
    """
    import re

    # Validate dataset name
    if not re.match(r"^[a-zA-Z0-9-]+$", dataset_name):
        raise ValueError("Dataset name must contain only alphanumeric characters and hyphens")

    if len(dataset_name) < 2 or len(dataset_name) > 50:
        raise ValueError("Dataset name must be between 2 and 50 characters")

    # Validate system_prompt
    if not system_prompt or len(system_prompt.strip()) < 10:
        raise ValueError("System prompt must be at least 10 characters")

    # Validate output_schema is a valid dictionary
    if not isinstance(output_schema, dict):
        raise ValueError("Output schema must be a valid JSON object")

    try:
        conf_container = get_conf_container()

        # Get existing configuration
        try:
            config_item = conf_container.read_item(item="configuration", partition_key="configuration")
        except Exception:
            # Create new configuration if it doesn't exist
            config_item = {"id": "configuration", "partitionKey": "configuration", "datasets": {}}

        # Ensure datasets key exists
        if "datasets" not in config_item:
            config_item["datasets"] = {}

        # Check if dataset already exists
        if dataset_name in config_item["datasets"]:
            raise ValueError(f"Dataset '{dataset_name}' already exists. Use update_dataset to modify it.")

        # Add new dataset configuration
        config_item["datasets"][dataset_name] = {
            "model_prompt": system_prompt.strip(),
            "example_schema": output_schema,
            "max_pages_per_chunk": max_pages_per_chunk,
        }

        # Upsert the configuration
        conf_container.upsert_item(body=config_item)

        logger.info(f"Created dataset '{dataset_name}' successfully")

        return {
            "success": True,
            "dataset_name": dataset_name,
            "message": f"Dataset '{dataset_name}' created successfully",
            "configuration": {
                "system_prompt_length": len(system_prompt),
                "output_schema_fields": list(output_schema.keys()) if output_schema else [],
                "max_pages_per_chunk": max_pages_per_chunk,
            },
        }

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Error creating dataset '{dataset_name}': {e}")
        raise Exception(f"Failed to create dataset: {str(e)}")


async def create_dataset_endpoint(request: Request):
    """
    REST API endpoint to create a new dataset.

    Request body:
    {
        "dataset_name": "my-dataset",
        "system_prompt": "Extract all data from the document...",
        "output_schema": {"field1": "...", "field2": "..."},
        "max_pages_per_chunk": 10  // optional, defaults to 10
    }
    """
    import re

    try:
        body = await request.json()

        # Extract and validate required fields
        dataset_name = body.get("dataset_name")
        system_prompt = body.get("system_prompt")
        output_schema = body.get("output_schema")
        max_pages_per_chunk = body.get("max_pages_per_chunk", 10)

        if not dataset_name:
            raise HTTPException(status_code=400, detail="dataset_name is required")
        if not system_prompt:
            raise HTTPException(status_code=400, detail="system_prompt is required")
        if output_schema is None:
            raise HTTPException(status_code=400, detail="output_schema is required")

        # Validate dataset name format
        if not re.match(r"^[a-zA-Z0-9-]+$", dataset_name):
            raise HTTPException(
                status_code=400, detail="Dataset name must contain only alphanumeric characters and hyphens"
            )

        result = await create_dataset(
            dataset_name=dataset_name,
            system_prompt=system_prompt,
            output_schema=output_schema,
            max_pages_per_chunk=max_pages_per_chunk,
        )

        return result

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in create_dataset_endpoint: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create dataset: {str(e)}")
