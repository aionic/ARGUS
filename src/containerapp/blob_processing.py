"""
Blob processing functionality for ARGUS Container App
"""

import asyncio
import copy
import json
import logging
import os
import shutil

# Import processing functions
import sys
import threading
import traceback
from datetime import datetime
from typing import Any, Dict

from dependencies import (
    get_blob_service_client,
    get_data_container,
    get_global_executor,
    get_global_processing_semaphore,
)
from models import BlobInputStream

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "functionapp"))
from ai_ocr.azure.content_understanding import get_cu_extraction
from ai_ocr.azure.doc_intelligence import get_read_confidence
from ai_ocr.cost import CostTracker, di_page_price
from ai_ocr.model import Config
from ai_ocr.paddle_gate import (
    assess_paddle_confidence,
    paddle_pregate_enabled,
    paddle_pregate_mode,
    paddle_verdict,
)
from ai_ocr.process import (
    fetch_model_prompt_and_schema,
    initialize_document,
    prepare_images,
    run_gpt_evaluation,
    run_gpt_extraction,
    run_gpt_summary,
    run_ocr_processing,
    split_pdf_into_subsets,
    update_state,
    write_blob_to_temp_file,
)
from ai_ocr.rules import apply_routing, reduce_schema, run_extractors
from ai_ocr.tiers import resolve_effective_config

logger = logging.getLogger(__name__)

CONTENT_UNDERSTANDING_FALLBACK_USD_PER_PAGE = 0.01


def _cost_region() -> str:
    return os.getenv("AZURE_LOCATION") or os.getenv("AZURE_REGION") or "eastus2"


def _usage_value(usage: dict | None, key: str) -> int:
    if not usage:
        return 0
    try:
        return int(usage.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _record_token_cost(tracker: CostTracker, stage: str, fallback_model: str, usage: dict | None) -> None:
    model = str((usage or {}).get("model") or fallback_model)
    tracker.record(stage, model, _usage_value(usage, "input_tokens"), _usage_value(usage, "output_tokens"))


def _schema_to_dict(schema: Any) -> dict:
    if isinstance(schema, dict):
        return schema
    try:
        parsed = json.loads(schema)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _has_pinned_tier(processing_options: dict) -> bool:
    return bool(processing_options.get("tier") or processing_options.get("extraction_tier"))


def _chunk_page_count(index: int, file_paths: list[str], max_pages_per_chunk: int, num_pages: int | None) -> int:
    if not num_pages:
        return 1
    if len(file_paths) <= 1:
        return int(num_pages)
    return max(min(max_pages_per_chunk, int(num_pages) - (index * max_pages_per_chunk)), 0)


def _ocr_cost_model() -> str:
    provider = os.getenv("OCR_PROVIDER", "azure").lower()
    return "document-intelligence" if provider == "azure" else provider


def _cu_cost_model() -> str:
    return os.getenv("CONTENT_UNDERSTANDING_ANALYZER_ID") or "content-understanding"


def _cu_page_price() -> float:
    raw_value = os.getenv("CONTENT_UNDERSTANDING_PAGE_PRICE_USD")
    if raw_value:
        try:
            return float(raw_value)
        except ValueError:
            logger.warning("Invalid CONTENT_UNDERSTANDING_PAGE_PRICE_USD=%s; using fallback", raw_value)
    # Fallback until Azure Retail exposes a reliable Content Understanding page meter.
    return CONTENT_UNDERSTANDING_FALLBACK_USD_PER_PAGE


def _quality_flagging_enabled() -> bool:
    """Whether OpenCV image-quality metrics should *route/flag* documents.

    Default OFF. Our experiments showed the cv2 pixel metrics are an unreliable
    legibility gate — they missed the genuinely-bad scan (bad.png) yet false-flagged
    ~89% of legitimate sparse B&W claim forms before per-dataset tuning. The PaddleOCR
    pre-gate and the Document Intelligence word-confidence backstop now own the block
    decision (recognition confidence, not pixel statistics). cv2 metrics are still
    computed and stored as advisory hints (``image_quality`` / ``image_quality_warning``,
    useful for the rescan-email screen) but no longer set a hard quality flag unless
    explicitly re-enabled via ``ENABLE_QUALITY_FLAGGING``.
    """
    return os.getenv("ENABLE_QUALITY_FLAGGING", "false").lower() not in ("0", "false", "no", "")


def _quality_flag_reasons(image_quality_reports: list[dict]) -> list[str]:
    reasons: list[str] = []
    for report in image_quality_reports:
        if not report.get("flagged_low_quality"):
            continue
        page = report.get("page") or "page"
        report_reasons = report.get("reasons") or ["low_quality"]
        reasons.extend(f"{page}:{reason}" for reason in report_reasons)
    return reasons


def _set_flag(document: dict, reasons: list[str], stage: str) -> None:
    if not reasons:
        return
    existing = document["properties"].get("flag") or {}
    merged_reasons = list(existing.get("reasons") or [])
    for reason in reasons:
        if reason not in merged_reasons:
            merged_reasons.append(reason)
    document["properties"]["flag"] = {
        "flagged": True,
        "reasons": merged_reasons,
        "stage": existing.get("stage", stage),
        "flagged_at": existing.get("flagged_at", datetime.now().isoformat()),
    }


def _pricing_settings() -> dict:
    """Solution-wide pricing knobs (agreement discount + consumption flag).

    Read from the Cosmos ``configuration`` document (``pricing`` key, editable from
    the Settings UI), falling back to env vars and then sane defaults. Failures are
    non-fatal — pricing display simply defaults to full Azure list price.
    """
    discount = 0.0
    consumption = os.getenv("PRICING_CONSUMPTION_AVAILABLE", "false").lower() in ("1", "true", "yes")
    env_discount = os.getenv("PRICING_DISCOUNT_PCT")
    if env_discount:
        try:
            discount = float(env_discount)
        except ValueError:
            discount = 0.0
    try:
        from dependencies import get_conf_container

        conf_container = get_conf_container()
        config_item = conf_container.read_item(item="configuration", partition_key="configuration")
        pricing = config_item.get("pricing") or {}
        if "discount_pct" in pricing:
            discount = float(pricing.get("discount_pct") or 0.0)
        if "consumption_available" in pricing:
            consumption = bool(pricing.get("consumption_available"))
    except Exception as exc:  # noqa: BLE001 - pricing config is best-effort
        logger.debug("Pricing settings unavailable, using defaults: %s", exc)
    return {
        "discount_pct": max(0.0, min(discount, 100.0)),
        "consumption_available": consumption,
    }


def _collect_confidences(value: Any, out: list[float]) -> None:
    """Recursively collect numeric confidence values in [0, 1] from a CU result."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 0.0 <= float(value) <= 1.0:
            out.append(float(value))
    elif isinstance(value, dict):
        for key, sub in value.items():
            if key in ("confidence", "score") and isinstance(sub, (int, float)):
                _collect_confidences(sub, out)
            else:
                _collect_confidences(sub, out)
    elif isinstance(value, (list, tuple)):
        for sub in value:
            _collect_confidences(sub, out)


def _cu_mean_confidence(cu_confidence: dict) -> float | None:
    """Mean of numeric CU confidence values across chunks, or None when absent."""
    values: list[float] = []
    _collect_confidences(cu_confidence, values)
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _cu_fallback_enabled(processing_options: dict) -> bool:
    """Whether a low-quality CU result should silently fall back to the GPT path."""
    if "cu_gpt_fallback" in processing_options:
        return bool(processing_options.get("cu_gpt_fallback"))
    return os.getenv("CU_GPT_FALLBACK", "true").lower() not in ("0", "false", "no")


def _cu_confidence_threshold(processing_options: dict) -> float | None:
    """Per-dataset estimated CU confidence floor below which we fall back to GPT.

    Returns ``None`` when no explicit threshold is configured (the common case) so
    that fallback is driven purely by the reliable DI word-confidence / preflight
    quality flag rather than CU's own field confidence (which is inverted/unreliable
    for sparse structured forms).
    """
    raw = processing_options.get("cu_confidence_threshold")
    if raw is None:
        raw = os.getenv("CU_CONFIDENCE_THRESHOLD")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None



def _ocr_confidence_thresholds() -> tuple[float, float, float]:
    """(word_min, mean_min, low_frac_max) for the OCR word-confidence gate (env-tunable)."""

    def _f(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    return (
        _f("OCR_CONFIDENCE_WORD_MIN", 0.70),
        _f("OCR_CONFIDENCE_MEAN_MIN", 0.80),
        _f("OCR_CONFIDENCE_LOW_FRAC_MAX", 0.25),
    )


def _aggregate_ocr_confidence(per_chunk: list[dict], document: dict) -> list[str]:
    """Aggregate per-chunk word-confidence stats, store them, and return flag reasons."""
    if not per_chunk:
        return []
    word_min, mean_min, low_frac_max = _ocr_confidence_thresholds()
    total_words = sum(s.get("n_words", 0) for s in per_chunk) or 1
    agg = {
        "n_words": total_words,
        # token-weighted mean across chunks
        "mean": round(sum(s["mean"] * s["n_words"] for s in per_chunk) / total_words, 4),
        "frac_low": round(sum(s["frac_low"] * s["n_words"] for s in per_chunk) / total_words, 4),
        "min": round(min(s["min"] for s in per_chunk), 4),
        "word_min": word_min,
        "per_chunk": per_chunk,
    }
    document["properties"]["ocr_confidence"] = agg
    reasons: list[str] = []
    if agg["mean"] < mean_min:
        reasons.append(f"low_ocr_confidence (mean {agg['mean']:.2f} < {mean_min:.2f})")
    if agg["frac_low"] > low_frac_max:
        reasons.append(f"high_low_confidence_word_fraction ({agg['frac_low']:.0%} of words < {word_min:.2f})")
    return reasons


def _ocr_confidence_preflight(file_paths: list[str], document: dict) -> list[str]:
    """Backend-agnostic legibility gate using Document Intelligence word confidence.

    Runs a lightweight ``prebuilt-read`` pass over each chunk, aggregates per-word
    recognition confidence, stores it on ``properties.ocr_confidence``, and returns
    flag reasons when the document reads as low-confidence (faint/garbled scan).
    This is the only signal that reliably separates genuinely illegible scans from
    legitimate sparse/structured forms (pixel- and text-statistics signals do not).
    """
    if os.getenv("ENABLE_OCR_CONFIDENCE_PREFLIGHT", "true").lower() in ("0", "false", "no"):
        return []
    word_min, _, _ = _ocr_confidence_thresholds()
    per_chunk: list[dict] = []
    for file_path in file_paths:
        stats = get_read_confidence(file_path, None, word_min=word_min)
        if stats:
            per_chunk.append(stats)
    return _aggregate_ocr_confidence(per_chunk, document)


def _paddle_pregate(file_paths: list[str], document: dict, processing_options: dict) -> list[str]:
    """Cheap PaddleOCR legibility pre-gate, run BEFORE the paid DI/CU extraction call.

    Renders page images, probes the internal PaddleOCR service for aggregate per-line
    recognition confidence, stores the stats on ``properties.paddle_pregate``, and
    returns flag reasons when the scan reads as too low-quality to extract. Returns
    ``[]`` when disabled, unreachable, or the scan passes — so the pipeline proceeds to
    the normal backend (where the DI word-confidence gate still runs as the backstop).
    """
    if not paddle_pregate_enabled(processing_options):
        return []
    stats = assess_paddle_confidence(file_paths)
    if stats is None:
        logger.info("PaddleOCR pre-gate skipped (service unreachable or no page assessed)")
        return []
    document["properties"]["paddle_pregate"] = stats
    reasons = paddle_verdict(stats)
    logger.info(
        "PaddleOCR pre-gate: pages=%s lines=%s mean=%s frac_low=%s -> %s",
        stats.get("n_pages"),
        stats.get("n_lines"),
        stats.get("mean"),
        stats.get("frac_low"),
        "BAD" if reasons else "OK",
    )
    return reasons


def create_blob_input_stream(blob_url: str) -> BlobInputStream:
    """Create a BlobInputStream from a blob URL"""
    try:
        # Parse blob URL to get container and blob name
        # Format: https://accountname.blob.core.windows.net/container/blob
        # URL path segments are percent-encoded (e.g. spaces -> %20); decode the
        # blob name so it matches the actual stored blob (and the upload-side
        # document id, which is built from the raw, un-encoded path).
        from urllib.parse import unquote

        url_parts = blob_url.replace("https://", "").split("/")
        container_name = unquote(url_parts[1])
        blob_name = unquote("/".join(url_parts[2:]))

        # Get blob client
        blob_service_client = get_blob_service_client()
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)

        # Get blob properties
        blob_properties = blob_client.get_blob_properties()
        blob_size = blob_properties.size

        return BlobInputStream(blob_name, blob_size, blob_client)

    except Exception as e:
        logger.error(f"Error creating blob input stream: {e}")
        raise


def process_blob_async(blob_input_stream: BlobInputStream, data_container):
    """Process blob asynchronously - same logic as original function"""
    thread_id = threading.current_thread().ident

    try:
        logger.info(f"[Thread-{thread_id}] Starting blob processing: {blob_input_stream.name}")

        start_time = datetime.now()
        process_blob(blob_input_stream, data_container)
        end_time = datetime.now()

        logger.info(
            f"[Thread-{thread_id}] Successfully processed blob: {blob_input_stream.name} in {(end_time - start_time).total_seconds():.2f}s"
        )

    except Exception as e:
        logger.error(f"[Thread-{thread_id}] Error processing blob {blob_input_stream.name}: {e}")
        logger.error(traceback.format_exc())
        raise


def handle_timeout_error_async(blob_input_stream: BlobInputStream, data_container):
    """Handle timeout error - same logic as original function"""
    document_id = blob_input_stream.name.replace("/", "__")
    try:
        data_container.read_item(item=document_id, partition_key={})
        logger.warning(f"Timeout occurred for document: {document_id}")
    except Exception as e:
        logger.error(f"Error handling timeout for document {document_id}: {e}")


async def process_blob_event(blob_url: str, event_data: Dict[str, Any]):
    """Process a single blob event in the background with concurrency control"""
    try:
        # Create blob input stream
        blob_input_stream = create_blob_input_stream(blob_url)

        logger.info(f"Processing blob event for: {blob_input_stream.name}")

        # Use semaphore to control concurrency
        global_processing_semaphore = get_global_processing_semaphore()
        global_executor = get_global_executor()
        data_container = get_data_container()

        if global_processing_semaphore:
            async with global_processing_semaphore:
                logger.info(f"Acquired semaphore for processing: {blob_input_stream.name}")

                # Use global ThreadPoolExecutor for processing
                if global_executor:
                    # Run in executor but await the result to maintain semaphore control
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(global_executor, process_blob_async, blob_input_stream, data_container)
                    logger.info(f"Completed processing for: {blob_input_stream.name}")
                else:
                    logger.error("Global executor not available")
        else:
            logger.error("Global processing semaphore not available")

    except Exception as e:
        logger.error(f"Error in background blob processing: {e}")
        logger.error(traceback.format_exc())


def initialize_document_data(blob_name: str, temp_file_path: str, num_pages: int, file_size: int, data_container):
    """Initialize document data for processing"""
    timer_start = datetime.now()

    # Determine dataset type from blob name
    logger.info(f"Processing blob with name: {blob_name}")

    # Handle blob path parsing
    blob_parts = blob_name.split("/")
    if len(blob_parts) < 2:
        # If no folder structure, default to 'default-dataset'
        logger.warning(f"Blob name {blob_name} doesn't contain folder structure, defaulting to 'default-dataset'")
        dataset_type = "default-dataset"
    else:
        dataset_type = blob_parts[0]  # Take the first part as dataset type

    logger.info(f"Using dataset type: {dataset_type}")

    prompt, json_schema, max_pages_per_chunk, processing_options = fetch_model_prompt_and_schema(dataset_type)
    if prompt is None or json_schema is None:
        raise ValueError("Failed to fetch model prompt and schema from configuration.")

    document = initialize_document(
        blob_name,
        file_size,
        num_pages,
        prompt,
        json_schema,
        timer_start,
        dataset_type,
        max_pages_per_chunk,
        processing_options,
    )
    update_state(document, data_container, "file_landed", True, (datetime.now() - timer_start).total_seconds())
    return document


def merge_extracted_data(gpt_responses):
    """
    Merges extracted data from multiple GPT responses into a single result.

    This function properly handles different data types:
    - Lists: concatenated together
    - Strings: joined with spaces and cleaned up
    - Numbers: summed together
    - Dicts: recursively merged
    """
    if not gpt_responses:
        return {}

    # Start with the first response as base
    merged_data = copy.deepcopy(gpt_responses[0]) if gpt_responses else {}

    # Merge remaining responses
    for response in gpt_responses[1:]:
        merged_data = _deep_merge_data(merged_data, response)

    return merged_data


def _deep_merge_data(base_data, new_data):
    """
    Deep merge two data dictionaries with intelligent type handling.
    """
    if not isinstance(base_data, dict) or not isinstance(new_data, dict):
        return new_data if new_data else base_data

    result = copy.deepcopy(base_data)

    for key, value in new_data.items():
        if key not in result:
            result[key] = copy.deepcopy(value)
        else:
            existing_value = result[key]

            # Handle different data types appropriately
            if isinstance(existing_value, list) and isinstance(value, list):
                # Concatenate lists
                result[key] = existing_value + value
            elif isinstance(existing_value, str) and isinstance(value, str):
                # Join strings with space, clean up multiple spaces
                combined = f"{existing_value} {value}".strip()
                result[key] = " ".join(combined.split())  # Clean up multiple spaces
            elif isinstance(existing_value, (int, float)) and isinstance(value, (int, float)):
                # Sum numbers
                result[key] = existing_value + value
            elif isinstance(existing_value, dict) and isinstance(value, dict):
                # Recursively merge dictionaries
                result[key] = _deep_merge_data(existing_value, value)
            else:
                # For other types or type mismatches, prefer non-empty values
                if value:  # Use new value if it's truthy
                    result[key] = value
                # Otherwise keep existing value

    return result


def update_final_document(document, gpt_response, ocr_response, evaluation_result, processing_times, data_container):
    """Update the final document with all processing results"""
    timer_stop = datetime.now()
    document["properties"]["total_time_seconds"] = (
        timer_stop - datetime.fromisoformat(document["properties"]["request_timestamp"])
    ).total_seconds()

    document["extracted_data"].update(
        {
            "gpt_extraction_output_with_evaluation": evaluation_result,
            "gpt_extraction_output": gpt_response,
            "ocr_output": "\n".join(str(result) for result in ocr_response),
        }
    )

    document["state"]["processing_completed"] = True
    update_state(document, data_container, "processing_completed", True)


def cleanup_temp_resources(temp_dirs, file_paths, temp_file_path):
    """
    Clean up temporary directories and files created during processing.
    Ensures proper resource cleanup even if processing fails.
    """

    # Clean up temporary directories
    for temp_dir in temp_dirs:
        try:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temporary directory: {temp_dir}")
        except Exception as e:
            logger.warning(f"Failed to clean up temp directory {temp_dir}: {e}")

    # Clean up split PDF files (but not the original temp file)
    for file_path in file_paths:
        try:
            if file_path and file_path != temp_file_path and os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up split file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to clean up split file {file_path}: {e}")

    # Clean up the main temporary file
    try:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
            logger.info(f"Cleaned up main temp file: {temp_file_path}")
    except Exception as e:
        logger.warning(f"Failed to clean up main temp file {temp_file_path}: {e}")


def process_blob(blob_input_stream: BlobInputStream, data_container):
    """Process a blob for OCR and data extraction (adapted for container app)"""
    overall_start_time = datetime.now()
    temp_file_path, num_pages, file_size = write_blob_to_temp_file(blob_input_stream)
    logger.info("processing blob")
    document = initialize_document_data(blob_input_stream.name, temp_file_path, num_pages, file_size, data_container)

    processing_times = {}
    file_paths = []
    temp_dirs = []
    summary_time = 0
    processing_options = {}
    region = _cost_region()
    tracker = CostTracker(region=region)
    cost_stages: set[str] = set()

    def record_page_cost(stage: str, model: str, pages: int, usd: float) -> None:
        tracker.record_pages(stage, model, pages, usd)
        cost_stages.add(stage)

    def record_token_cost(stage: str, fallback_model: str, usage: dict | None) -> None:
        _record_token_cost(tracker, stage, fallback_model, usage)
        cost_stages.add(stage)

    try:
        # Get processing options from document
        processing_options = document.get(
            "processing_options",
            {"include_ocr": True, "include_images": True, "enable_summary": True, "enable_evaluation": True},
        )
        tier_pinned = _has_pinned_tier(processing_options)
        eff = resolve_effective_config(processing_options.get("tier"), processing_options, os.environ)
        document["properties"]["tier"] = eff.tier
        pricing = _pricing_settings()

        logger.info(
            f"Processing options: tier={eff.tier}, OCR={eff.enable_ocr}, "
            f"Images={eff.enable_images}, Summary={eff.enable_summary}, "
            f"Evaluation={eff.enable_evaluation}, Rules={eff.use_rules_engine}"
        )

        max_pages_per_chunk = document["model_input"].get("max_pages_per_chunk", 10)

        # Validate chunk size to prevent system overload
        if max_pages_per_chunk < 1:
            logger.warning(f"Invalid max_pages_per_chunk: {max_pages_per_chunk}, using default of 10")
            max_pages_per_chunk = 10
        elif max_pages_per_chunk > 50:  # Reasonable upper limit
            logger.warning(
                f"Large max_pages_per_chunk: {max_pages_per_chunk}, consider reducing for better performance"
            )

        if num_pages and num_pages > max_pages_per_chunk:
            file_paths = split_pdf_into_subsets(temp_file_path, max_pages_per_subset=max_pages_per_chunk)
            logger.info(
                f"Split {num_pages} pages into {len(file_paths)} chunks of max {max_pages_per_chunk} pages each"
            )
        else:
            file_paths = [temp_file_path]
            logger.info(f"Processing single file with {num_pages} pages (no chunking needed)")

        # Determine extraction backend: per-dataset override, else solution default.
        # Content Understanding is the strict default; the GPT path is reached either by
        # explicit override or as a silent fallback when CU output reads low-quality.
        extraction_backend = (
            processing_options.get("extraction_backend") or os.getenv("EXTRACTION_BACKEND", "content_understanding")
        ).lower()

        enable_evaluation = eff.enable_evaluation

        # Per-dataset image quality preprocessing options (OpenCV enhance_retry).
        quality_options = {
            "enable_preprocessing": eff.enable_preprocessing,
            "enable_enhancement": processing_options.get("enable_enhancement", True),
            "skip_if_still_bad": processing_options.get("skip_if_still_bad", False),
            "thresholds": processing_options.get("quality_thresholds"),
        }

        extracted_data_list = []
        image_cache = {}
        image_quality_reports = []
        cu_fallback = False  # set when a low-quality CU result silently falls back to GPT

        # ── PaddleOCR pre-gate (cheap legibility probe BEFORE paid DI/CU) ──────
        # Opt-in. On a bad verdict in `block` mode we short-circuit: skip the paid
        # extraction call entirely, flag the document, record the avoided cost, mark
        # it complete, and route to review. In `advisory` mode we only flag and
        # proceed. The DI word-confidence gate still runs as the backstop downstream.
        paddle_reasons = _paddle_pregate(file_paths, document, processing_options)
        if paddle_reasons and paddle_pregate_mode() == "block":
            logger.info(
                "PaddleOCR pre-gate BLOCKED %s; skipping %s extraction", blob_input_stream.name, extraction_backend
            )
            _set_flag(document, paddle_reasons, "paddle_pregate")
            document["properties"]["num_pages"] = num_pages or len(file_paths) or 1
            document["properties"]["extraction_backend_used"] = "skipped_paddle_pregate"
            document["extracted_data"]["ocr_output"] = ""
            document["extracted_data"]["gpt_extraction_output"] = {}
            document["extracted_data"]["gpt_extraction_output_with_evaluation"] = {}
            document["extracted_data"]["classification"] = ""
            document["extracted_data"]["gpt_summary_output"] = ""

            # Cost telemetry: the paid extraction call was avoided (record it at $0 so
            # the savings are visible); Paddle compute is scale-to-zero ~free.
            if extraction_backend == "content_understanding":
                record_page_cost("content_understanding", _cu_cost_model(), 0, 0)
            else:
                record_page_cost("ocr", _ocr_cost_model(), 0, 0)
                record_token_cost("extraction", eff.extraction_model, None)
            record_token_cost("evaluation", eff.extraction_model, None)
            record_token_cost("summary", eff.summary_model, None)
            record_page_cost("paddle_pregate", "paddleocr", document["properties"]["num_pages"], 0)
            document["properties"]["cost"] = tracker.aggregate(
                document["properties"]["num_pages"],
                discount_pct=pricing["discount_pct"],
                consumption_available=pricing["consumption_available"],
            )
            document["properties"]["tier"] = eff.tier

            update_state(document, data_container, "paddle_pregate_blocked", True, 0)
            document["state"]["processing_completed"] = True
            update_state(document, data_container, "processing_completed", True)
            data_container.upsert_item(document)
            return document
        if paddle_reasons:
            _set_flag(document, paddle_reasons, "paddle_pregate")

        if extraction_backend == "content_understanding":
            # Content Understanding full-analyzer: OCR + field extraction in one call.
            logger.info(f"Using Content Understanding extraction backend for {len(file_paths)} chunks")
            ocr_results = []
            total_ocr_time = 0
            total_extraction_time = 0
            cu_confidence = {}
            cu_usable_images = False
            example_schema = document["model_input"]["example_schema"]
            dataset_name = document.get("dataset", "default")
            try:
                schema_obj = example_schema if isinstance(example_schema, dict) else json.loads(example_schema)
            except (TypeError, ValueError):
                schema_obj = {}

            for i, file_path in enumerate(file_paths):
                logger.info(f"Content Understanding analysis for chunk {i + 1}/{len(file_paths)}")
                cu_start = datetime.now()
                cu_result = get_cu_extraction(file_path, schema_obj, dataset_name, None)
                cu_time = (datetime.now() - cu_start).total_seconds()
                chunk_pages = _chunk_page_count(i, file_paths, max_pages_per_chunk, num_pages)
                record_page_cost(
                    "content_understanding",
                    _cu_cost_model(),
                    chunk_pages,
                    chunk_pages * _cu_page_price(),
                )

                ocr_results.append(cu_result.get("ocr_output", ""))
                extracted_data_list.append(cu_result.get("extracted_data", {}))
                if cu_result.get("confidence"):
                    cu_confidence[f"chunk_{i + 1}"] = cu_result["confidence"]
                total_ocr_time += cu_time
                total_extraction_time += cu_time

                # Always prepare page images so the quality preflight can run on
                # the CU path; keep the base64 images only when evaluation (vision)
                # will actually consume them, otherwise discard to save memory.
                temp_dir, imgs, reports = prepare_images(file_path, Config(), quality_options)
                temp_dirs.append(temp_dir)
                image_quality_reports.extend(reports)
                if imgs:
                    cu_usable_images = True
                image_cache[i] = imgs if (enable_evaluation and eff.enable_images) else []

            processing_times["ocr_processing_time"] = total_ocr_time
            processing_times["gpt_extraction_time"] = total_extraction_time
            document["extracted_data"]["ocr_output"] = "\n".join(str(r) for r in ocr_results)
            document["properties"]["extraction_backend_used"] = "content_understanding"
            if cu_confidence:
                document["properties"]["content_understanding_confidence"] = cu_confidence

            # Backend-agnostic preflight for Content Understanding: the GPT branch
            # flags BEFORE extraction, but CU extracts in one call, so here we
            # post-flag for human review (tier switch cannot re-run CU cheaply).
            combined_cu_text = "\n".join(str(r) for r in ocr_results).strip()
            document["properties"]["num_pages"] = num_pages or len(file_paths) or 1
            document["properties"]["ocr_text_length"] = len(combined_cu_text)
            if image_quality_reports:
                document["properties"]["image_quality"] = image_quality_reports
                if any(r.get("flagged_low_quality") for r in image_quality_reports):
                    document["properties"]["image_quality_warning"] = True
                    if _quality_flagging_enabled():
                        _set_flag(document, _quality_flag_reasons(image_quality_reports), "quality")
            if len(combined_cu_text) < 20 and not cu_usable_images:
                _set_flag(
                    document,
                    ["ocr_text_unreadable_or_empty_and_no_usable_images"],
                    "preflight",
                )
            # Primary legibility gate: DI word confidence (CU exposes no word-level
            # confidence and its field confidence is inverted for sparse forms).
            ocr_conf_reasons = _ocr_confidence_preflight(file_paths, document)
            if ocr_conf_reasons:
                _set_flag(document, ocr_conf_reasons, "quality")

            update_state(document, data_container, "ocr_completed", True, total_ocr_time)

            # ── Silent CU -> GPT fallback decision ────────────────────────────
            # If CU output reads low-quality (a reliable DI word-confidence / preflight
            # flag was raised, or — when an explicit per-dataset threshold is set — CU's
            # own mean field confidence is below it), silently re-run extraction on the
            # richer GPT path. We estimate the quality bar per-dataset; recovery is
            # silent so the user is not asked to review a doc we could recover.
            cu_flag = document["properties"].get("flag") or {}
            cu_mean_conf = _cu_mean_confidence(cu_confidence)
            cu_threshold = _cu_confidence_threshold(processing_options)
            low_cu_confidence = (
                cu_mean_conf is not None and cu_threshold is not None and cu_mean_conf < cu_threshold
            )
            cu_fallback = _cu_fallback_enabled(processing_options) and (
                bool(cu_flag.get("flagged")) or low_cu_confidence
            )
            if cu_fallback:
                fallback_reasons = list(cu_flag.get("reasons") or [])
                if low_cu_confidence:
                    fallback_reasons.append(f"cu_mean_confidence {cu_mean_conf:.2f} < {cu_threshold:.2f}")
                document["properties"]["cu_fallback"] = {
                    "triggered": True,
                    "reasons": fallback_reasons,
                    "cu_mean_confidence": cu_mean_conf,
                    "cu_confidence_threshold": cu_threshold,
                    "pre_fallback_flag_stage": cu_flag.get("stage"),
                    "triggered_at": datetime.now().isoformat(),
                }
                logger.info(
                    "CU output low-quality for %s -> silent GPT fallback (reasons=%s)",
                    blob_input_stream.name,
                    fallback_reasons,
                )
                # Clear the CU review flag so recovery is silent, and reset the per-chunk
                # accumulators so the GPT path rebuilds them cleanly. The GPT path re-runs
                # the DI word-confidence preflight and only re-flags if the document is
                # still genuinely illegible after the richer extraction.
                document["properties"]["flag"] = None
                extracted_data_list = []
                image_cache = {}
                image_quality_reports = []

            data_container.upsert_item(document)

        if extraction_backend != "content_understanding" or cu_fallback:
            # ── GPT extraction backend (explicit default or silent CU fallback) ──
            document["properties"]["extraction_backend_used"] = (
                "content_understanding+gpt" if cu_fallback else "gpt"
            )

            # Step 1: Run OCR for all files (conditional - only if OCR text will be used)
            ocr_results = []
            total_ocr_time = 0

            if eff.enable_ocr:
                logger.info(f"Starting OCR processing for {len(file_paths)} chunks")
                for i, file_path in enumerate(file_paths):
                    logger.info(f"Processing OCR for chunk {i + 1}/{len(file_paths)}")
                    ocr_result, ocr_time, ocr_pages = run_ocr_processing(
                        file_path, document, data_container, None, update_state=False
                    )
                    ocr_results.append(ocr_result)
                    total_ocr_time += ocr_time
                    record_page_cost(
                        "ocr",
                        _ocr_cost_model(),
                        ocr_pages,
                        di_page_price(region) * ocr_pages,
                    )

                processing_times["ocr_processing_time"] = total_ocr_time
                document["extracted_data"]["ocr_output"] = "\n".join(str(result) for result in ocr_results)
                update_state(document, data_container, "ocr_completed", True, total_ocr_time)
                data_container.upsert_item(document)
                logger.info(f"Completed OCR processing for all chunks in {total_ocr_time:.2f}s")
            else:
                logger.info("Skipping OCR processing (OCR text not needed for GPT extraction)")
                ocr_results = [""] * len(file_paths)
                processing_times["ocr_processing_time"] = 0
                document["extracted_data"]["ocr_output"] = ""
                record_page_cost("ocr", _ocr_cost_model(), 0, 0)
                update_state(document, data_container, "ocr_skipped", True, 0)
                data_container.upsert_item(document)

            # Prepare images before routing/extraction so quality can pre-flag review work.
            for i, file_path in enumerate(file_paths):
                if eff.enable_images:
                    temp_dir, imgs, reports = prepare_images(file_path, Config(), quality_options)
                    temp_dirs.append(temp_dir)
                    image_cache[i] = imgs
                    image_quality_reports.extend(reports)
                else:
                    image_cache[i] = []

            if image_quality_reports:
                document["properties"]["image_quality"] = image_quality_reports
                if any(r.get("flagged_low_quality") for r in image_quality_reports):
                    document["properties"]["image_quality_warning"] = True
                    if _quality_flagging_enabled():
                        _set_flag(document, _quality_flag_reasons(image_quality_reports), "quality")
            elif document["properties"].get("image_quality_warning"):
                if _quality_flagging_enabled():
                    _set_flag(document, ["image_quality_warning"], "quality")

            combined_ocr_text = "\n".join(str(result) for result in ocr_results)
            document["properties"]["num_pages"] = num_pages or len(file_paths) or 1
            document["properties"]["ocr_text_length"] = len(combined_ocr_text.strip())

            if len(combined_ocr_text.strip()) < 20 and not any(image_cache.values()):
                _set_flag(
                    document,
                    ["ocr_text_unreadable_or_empty_and_no_usable_images"],
                    "preflight",
                )

            # Primary legibility gate: DI word confidence. Reuse the per-chunk stats
            # captured during the azure-layout OCR call (no extra DI request); fall
            # back to a dedicated read pass if none were captured (e.g., OCR skipped).
            captured_conf = document["properties"].pop("_ocr_conf_chunks", [])
            if captured_conf:
                ocr_conf_reasons = _aggregate_ocr_confidence(captured_conf, document)
            else:
                ocr_conf_reasons = _ocr_confidence_preflight(file_paths, document)
            if ocr_conf_reasons:
                _set_flag(document, ocr_conf_reasons, "quality")

            example_schema = document["model_input"]["example_schema"]
            schema_obj = _schema_to_dict(example_schema)
            rules_result = None
            extraction_schema: Any = example_schema
            if eff.use_rules_engine and processing_options.get("rules"):
                rules_result = run_extractors(combined_ocr_text, schema_obj, processing_options["rules"])
                document["properties"]["rules_all_required_filled"] = rules_result.all_required_filled
                document["properties"]["rules_engine_result"] = rules_result.to_dict()
                if rules_result.all_required_filled:
                    extraction_schema = schema_obj
                else:
                    extraction_schema = reduce_schema(schema_obj, rules_result.remaining_fields)

            routing_options = dict(processing_options)
            routing_options.update(
                {
                    "enable_ocr": eff.enable_ocr,
                    "num_pages": document["properties"]["num_pages"],
                    "ocr_text_length": document["properties"]["ocr_text_length"],
                }
            )
            routing_decision = apply_routing(document["properties"], routing_options, os.environ)
            document["properties"]["routing_decision"] = routing_decision.to_dict()
            if routing_decision.route_to_review:
                low_quality = any("low_quality" in reason for reason in routing_decision.reasons)
                _set_flag(document, routing_decision.reasons, "quality" if low_quality else "preflight")

            if not tier_pinned and routing_decision.tier != eff.tier:
                eff = resolve_effective_config(routing_decision.tier, processing_options, os.environ)
                document["properties"]["tier"] = eff.tier
                enable_evaluation = eff.enable_evaluation
                if not eff.enable_images:
                    image_cache = {i: [] for i in range(len(file_paths))}
                logger.info("Routing selected tier=%s", eff.tier)

            data_container.upsert_item(document)

            # Step 2: GPT extraction
            logger.info(f"Starting GPT extraction for {len(file_paths)} chunks")
            total_extraction_time = 0

            if routing_decision.skip_extraction:
                logger.info("Skipping GPT extraction due to routing decision: %s", routing_decision.reasons)
                fallback_data = rules_result.filled if rules_result and rules_result.all_required_filled else {}
                extracted_data_list = [copy.deepcopy(fallback_data) for _ in file_paths]
                record_token_cost("extraction", eff.extraction_model, None)
            elif rules_result and rules_result.all_required_filled:
                logger.info("Skipping GPT extraction because rules filled all required fields")
                extracted_data_list = [copy.deepcopy(rules_result.filled) for _ in file_paths]
                record_token_cost("extraction", eff.extraction_model, None)
            else:
                for i, file_path in enumerate(file_paths):
                    logger.info(f"Processing GPT extraction for chunk {i + 1}/{len(file_paths)}")

                    imgs = image_cache.get(i, [])
                    ocr_text_for_extraction = ocr_results[i] if eff.enable_ocr else ""

                    if not ocr_text_for_extraction and not imgs:
                        logger.error("No input provided to GPT extraction - both OCR text and images are empty!")
                        raise ValueError("Cannot perform GPT extraction without either OCR text or images")

                    extracted_data, extraction_time, usage = run_gpt_extraction(
                        ocr_text_for_extraction,
                        document["model_input"]["model_prompt"],
                        extraction_schema,
                        imgs,
                        document,
                        data_container,
                        None,
                        update_state=False,
                        model=eff.extraction_model,
                    )
                    if rules_result and rules_result.filled and isinstance(extracted_data, dict):
                        extracted_data = _deep_merge_data(extracted_data, rules_result.filled)
                    extracted_data_list.append(extracted_data)
                    total_extraction_time += extraction_time
                    record_token_cost("extraction", eff.extraction_model, usage)

            processing_times["gpt_extraction_time"] = total_extraction_time

        # Store any per-page image-quality metrics gathered during image prep.
        if image_quality_reports:
            document["properties"]["image_quality"] = image_quality_reports
            if any(r.get("flagged_low_quality") for r in image_quality_reports):
                document["properties"]["image_quality_warning"] = True

        # Create page range structure instead of merging
        if len(extracted_data_list) > 1:
            structured_extraction = create_page_range_structure(extracted_data_list, file_paths, max_pages_per_chunk)
        else:
            structured_extraction = extracted_data_list[0] if extracted_data_list else {}

        document["extracted_data"]["gpt_extraction_output"] = structured_extraction
        update_state(document, data_container, "gpt_extraction_completed", True, total_extraction_time)
        data_container.upsert_item(document)

        # Step 3: GPT evaluation (conditional)
        total_evaluation_time = 0
        if enable_evaluation:
            logger.info(f"Starting GPT evaluation for {len(file_paths)} chunks")
            evaluation_results = []
            for i, file_path in enumerate(file_paths):
                imgs = image_cache.get(i, [])

                enriched_data, evaluation_time, usage = run_gpt_evaluation(
                    imgs,
                    extracted_data_list[i],
                    document["model_input"]["example_schema"],
                    document,
                    data_container,
                    None,
                    update_state=False,
                    model=eff.extraction_model,
                )
                evaluation_results.append(enriched_data)
                total_evaluation_time += evaluation_time
                record_token_cost("evaluation", eff.extraction_model, usage)

            processing_times["gpt_evaluation_time"] = total_evaluation_time

            if len(evaluation_results) > 1:
                structured_evaluation = create_page_range_evaluations(
                    evaluation_results, file_paths, max_pages_per_chunk
                )
            else:
                structured_evaluation = evaluation_results[0] if evaluation_results else {}

            document["extracted_data"]["gpt_extraction_output_with_evaluation"] = structured_evaluation
            update_state(document, data_container, "gpt_evaluation_completed", True, total_evaluation_time)
        else:
            structured_evaluation = {}
            document["extracted_data"]["gpt_extraction_output_with_evaluation"] = structured_evaluation
            update_state(document, data_container, "gpt_evaluation_skipped", True, 0)
            processing_times["gpt_evaluation_time"] = 0
            record_token_cost("evaluation", eff.extraction_model, None)

        # Step 4: Summary (conditional)
        if eff.enable_summary:
            logger.info("Starting GPT summary processing")
            combined_ocr_text = "\n".join(str(result) for result in ocr_results)
            summary_data, summary_time, usage = run_gpt_summary(
                combined_ocr_text,
                document,
                data_container,
                None,
                update_state=False,
                model=eff.summary_model,
            )
            record_token_cost("summary", eff.summary_model, usage)

            document["extracted_data"]["classification"] = summary_data["classification"]
            document["extracted_data"]["gpt_summary_output"] = summary_data["gpt_summary_output"]
            update_state(document, data_container, "gpt_summary_completed", True, summary_time)
        else:
            document["extracted_data"]["classification"] = ""
            document["extracted_data"]["gpt_summary_output"] = ""
            update_state(document, data_container, "gpt_summary_skipped", True, 0)
            record_token_cost("summary", eff.summary_model, None)

        # Final update
        overall_end_time = datetime.now()
        total_processing_time = (overall_end_time - overall_start_time).total_seconds()

        logger.info(f"Processing completed for {blob_input_stream.name}")
        logger.info(
            f"Total time: {total_processing_time:.2f}s | OCR: {processing_times['ocr_processing_time']:.2f}s | "
            f"Extraction: {processing_times['gpt_extraction_time']:.2f}s | "
            f"Evaluation: {processing_times.get('gpt_evaluation_time', 0):.2f}s | Summary: {summary_time:.2f}s"
        )

        if extraction_backend == "content_understanding" and "content_understanding" not in cost_stages:
            record_page_cost("content_understanding", _cu_cost_model(), 0, 0)
        if extraction_backend != "content_understanding" and "ocr" not in cost_stages:
            record_page_cost("ocr", _ocr_cost_model(), 0, 0)
        if extraction_backend != "content_understanding" and "extraction" not in cost_stages:
            record_token_cost("extraction", eff.extraction_model, None)
        if "evaluation" not in cost_stages:
            record_token_cost("evaluation", eff.extraction_model, None)
        if "summary" not in cost_stages:
            record_token_cost("summary", eff.summary_model, None)
        document["properties"]["cost"] = tracker.aggregate(
            num_pages or document["properties"].get("num_pages", 0),
            discount_pct=pricing["discount_pct"],
            consumption_available=pricing["consumption_available"],
        )
        document["properties"]["tier"] = eff.tier

        update_final_document(
            document,
            document["extracted_data"]["gpt_extraction_output"],
            ocr_results,
            document["extracted_data"]["gpt_extraction_output_with_evaluation"],
            processing_times,
            data_container,
        )

        return document

    except Exception as e:
        logger.error(f"Processing error in process_blob: {str(e)}")
        document["errors"].append(f"Processing error: {str(e)}")
        document["state"]["processing_completed"] = False
        _pricing = locals().get("pricing") or {"discount_pct": 0.0, "consumption_available": False}
        document["properties"]["cost"] = tracker.aggregate(
            num_pages or document["properties"].get("num_pages", 0),
            discount_pct=_pricing["discount_pct"],
            consumption_available=_pricing["consumption_available"],
        )

        # Mark incomplete steps as failed
        if processing_options.get("include_ocr", True) and "ocr_processing_time" not in processing_times:
            update_state(document, data_container, "ocr_completed", False)
        if "gpt_extraction_time" not in processing_times:
            update_state(document, data_container, "gpt_extraction_completed", False)
        if processing_options.get("enable_evaluation", True) and "gpt_evaluation_time" not in processing_times:
            # Only flag evaluation as failed when it was actually expected to run.
            if locals().get("enable_evaluation", True):
                update_state(document, data_container, "gpt_evaluation_completed", False)
        if processing_options.get("enable_summary", True) and summary_time == 0:
            update_state(document, data_container, "gpt_summary_completed", False)

        data_container.upsert_item(document)
        raise e
    finally:
        cleanup_temp_resources(temp_dirs, file_paths, temp_file_path)


def create_page_range_structure(data_list, file_paths, max_pages_per_chunk):
    """
    Create a structured JSON with page ranges instead of merging chunks.

    Args:
        data_list: List of extracted data from each chunk
        file_paths: List of file paths for each chunk
        max_pages_per_chunk: Maximum pages per chunk setting

    Returns:
        Dict with page range keys like {"pages_1-10": {chunk_data}, "pages_11-20": {chunk_data}, ...}
    """
    if not data_list:
        return {}

    # If there's only one chunk, return it with a single page range
    if len(data_list) == 1:
        return {"pages_1-all": data_list[0]}

    # Multiple chunks - create page range structure
    structured_data = {}

    for i, (data, file_path) in enumerate(zip(data_list, file_paths)):
        # Parse page range from file_path if it contains subset information
        if "_subset_" in file_path:
            # Format: originalfile_subset_0_9.pdf -> pages_1-10
            parts = file_path.split("_subset_")
            if len(parts) == 2:
                page_part = parts[1].replace(".pdf", "")
                start_end = page_part.split("_")
                if len(start_end) == 2:
                    try:
                        start_page = int(start_end[0]) + 1  # Convert to 1-indexed
                        end_page = int(start_end[1]) + 1  # Convert to 1-indexed
                        page_key = f"pages_{start_page}-{end_page}"
                        structured_data[page_key] = data
                        continue
                    except ValueError:
                        pass

        # Fallback: calculate page range from chunk index and max_pages_per_chunk
        chunk_start = i * max_pages_per_chunk + 1
        chunk_end = (i + 1) * max_pages_per_chunk
        page_key = f"pages_{chunk_start}-{chunk_end}"
        structured_data[page_key] = data

    return structured_data


def create_page_range_evaluations(evaluation_list, file_paths, max_pages_per_chunk):
    """
    Create a structured JSON with page ranges for evaluations.
    Uses the same logic as create_page_range_structure but for evaluation data.

    Returns:
        Dict with page range keys like {"pages_1-10": {evaluation_data}, ...}
    """
    # Use the same logic as create_page_range_structure
    return create_page_range_structure(evaluation_list, file_paths, max_pages_per_chunk)
