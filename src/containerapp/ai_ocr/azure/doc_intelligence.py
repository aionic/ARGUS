from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.identity import DefaultAzureCredential

from ai_ocr.azure.config import get_config


def get_document_intelligence_client(cosmos_config_container=None):
    """Create a new Document Intelligence client instance for each request to avoid connection pooling issues"""
    config = get_config(cosmos_config_container)
    return DocumentIntelligenceClient(
        endpoint=config["doc_intelligence_endpoint"],
        credential=DefaultAzureCredential(),
        headers={"solution": "ARGUS-1.0"},
    )


def get_ocr_results(file_path: str, cosmos_config_container=None):
    import logging
    import threading

    thread_id = threading.current_thread().ident
    logger = logging.getLogger(__name__)

    logger.info(f"[Thread-{thread_id}] Starting Document Intelligence OCR for: {file_path}")

    # Create a new client instance for this request to ensure parallel processing
    client = get_document_intelligence_client(cosmos_config_container)

    with open(file_path, "rb") as f:
        logger.info(f"[Thread-{thread_id}] Submitting document to Document Intelligence API")
        poller = client.begin_analyze_document("prebuilt-layout", body=f)

    logger.info(f"[Thread-{thread_id}] Waiting for Document Intelligence results...")
    ocr_result = poller.result().content
    logger.info(f"[Thread-{thread_id}] Document Intelligence OCR completed, {len(ocr_result)} characters")

    return ocr_result


def get_ocr_results_with_confidence(file_path: str, cosmos_config_container=None, word_min: float = 0.70):
    """Like :func:`get_ocr_results` but also returns aggregated word-confidence stats.

    Reuses the single ``prebuilt-layout`` call (no extra cost) so the GPT/azure
    path gets the legibility signal for free. Returns ``(content, stats|None)``.
    """
    import logging

    logger = logging.getLogger(__name__)
    client = get_document_intelligence_client(cosmos_config_container)
    with open(file_path, "rb") as f:
        poller = client.begin_analyze_document("prebuilt-layout", body=f)
    result = poller.result()
    content = result.content
    logger.info(f"Document Intelligence OCR completed, {len(content)} characters")
    return content, summarize_word_confidence(result, word_min=word_min)


def summarize_word_confidence(result, word_min: float = 0.70) -> dict | None:
    """Aggregate per-word OCR recognition confidence from a Document Intelligence result.

    This is the principled legibility signal for preflight: recognition confidence
    measures how certain the OCR engine was of each character, so faint/garbled
    scans score low even when their text content looks structurally normal. Returns
    ``None`` when no word confidence is available (e.g., empty page).
    """
    confidences: list[float] = []
    for page in getattr(result, "pages", None) or []:
        for word in getattr(page, "words", None) or []:
            conf = getattr(word, "confidence", None)
            if isinstance(conf, (int, float)) and not isinstance(conf, bool):
                confidences.append(float(conf))
    if not confidences:
        return None
    confidences.sort()
    n = len(confidences)
    low = sum(1 for c in confidences if c < word_min)
    return {
        "n_words": n,
        "mean": round(sum(confidences) / n, 4),
        "p10": round(confidences[int(n * 0.1)], 4),
        "min": round(confidences[0], 4),
        "frac_low": round(low / n, 4),
        "word_min": word_min,
    }


def get_read_confidence(
    file_path: str,
    cosmos_config_container=None,
    word_min: float = 0.70,
    *,
    raise_on_error: bool = False,
) -> dict | None:
    """Run the lightweight ``prebuilt-read`` model purely to capture OCR word confidence.

    Used by the preflight gate on any extraction backend (including Content
    Understanding, which does not expose word-level confidence). Cheaper than
    ``prebuilt-layout``. Failures are non-fatal and return ``None``.
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        client = get_document_intelligence_client(cosmos_config_container)
        with open(file_path, "rb") as f:
            poller = client.begin_analyze_document("prebuilt-read", body=f)
        return summarize_word_confidence(poller.result(), word_min=word_min)
    except Exception as exc:  # noqa: BLE001 - preflight probe must never break processing
        logger.warning(f"OCR read-confidence probe failed for {file_path}: {exc}")
        if raise_on_error:
            raise
        return None
