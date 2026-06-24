"""PaddleOCR quality-probe microservice.

A small FastAPI service that runs PaddleOCR purely as a *legibility probe* for the
ARGUS preflight gate. It does NOT perform document extraction — it returns aggregate
per-line recognition confidence so the backend can decide, cheaply and before paying
for Document Intelligence / Content Understanding, whether a scan is too low-quality
to extract.

Endpoints
---------
* ``GET  /health``   liveness/readiness (model loaded?).
* ``POST /assess``   multipart ``file`` (a rendered page image) -> confidence stats.

Auth
----
If ``SERVICE_KEY`` is set, requests must send a matching ``X-Service-Key`` header.
The container app is deployed with internal-only ingress, so this is defence in depth.
"""

from __future__ import annotations

import io
import logging
import os
import time

import numpy as np
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("paddleocr-service")

LANG = os.getenv("PADDLE_LANG", "en")
USE_ANGLE_CLS = os.getenv("PADDLE_USE_ANGLE_CLS", "true").lower() not in ("0", "false", "no")
DEFAULT_WORD_MIN = float(os.getenv("PADDLE_WORD_MIN", "0.70"))
SERVICE_KEY = os.getenv("SERVICE_KEY", "")

app = FastAPI(title="ARGUS PaddleOCR quality probe", version="1.0.0")

# Loaded once at startup; PaddleOCR is not thread-safe for concurrent .ocr calls on
# the same instance, so we serialise access with a lock.
_ocr = None
_ocr_lock = None


def _get_ocr():
    global _ocr, _ocr_lock
    if _ocr is None:
        import threading

        from paddleocr import PaddleOCR

        logger.info("Loading PaddleOCR (lang=%s, use_angle_cls=%s) ...", LANG, USE_ANGLE_CLS)
        _ocr = PaddleOCR(use_angle_cls=USE_ANGLE_CLS, lang=LANG, show_log=False)
        _ocr_lock = threading.Lock()
        logger.info("PaddleOCR ready.")
    return _ocr, _ocr_lock


@app.on_event("startup")
def _warmup() -> None:
    # Trigger model load at startup so /health reflects readiness and the first real
    # request doesn't pay the load cost.
    try:
        ocr, lock = _get_ocr()
        with lock:
            ocr.ocr(np.zeros((32, 32, 3), dtype=np.uint8), cls=USE_ANGLE_CLS)
    except Exception as exc:  # noqa: BLE001 - warmup must not crash the service
        logger.warning("Warmup inference failed (will retry on first request): %s", exc)


def _require_key(x_service_key: str | None = Header(default=None)) -> None:
    if SERVICE_KEY and x_service_key != SERVICE_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-Service-Key")


@app.get("/health")
def health() -> dict:
    return {"status": "healthy", "model_loaded": _ocr is not None, "lang": LANG}


def _extract_scores(result) -> list[float]:
    """Pull per-line recognition scores out of a PaddleOCR result (2.x format)."""
    scores: list[float] = []
    # result is a list with one entry per image; each entry is a list of
    # [box, (text, score)] or None when nothing was detected.
    for page in result or []:
        for line in page or []:
            try:
                _box, (_text, score) = line
            except (ValueError, TypeError):
                continue
            if isinstance(score, (int, float)):
                scores.append(float(score))
    return scores


def _summarize(scores: list[float], word_min: float) -> dict:
    if not scores:
        # No text recognised at all — treat as maximally low-confidence so the gate
        # can decide (a blank/illegible page yields nothing).
        return {
            "n_lines": 0,
            "mean": 0.0,
            "min": 0.0,
            "p10": 0.0,
            "frac_low": 1.0,
            "word_min": word_min,
        }
    ordered = sorted(scores)
    n = len(ordered)
    low = sum(1 for s in ordered if s < word_min)
    return {
        "n_lines": n,
        "mean": round(sum(ordered) / n, 4),
        "min": round(ordered[0], 4),
        "p10": round(ordered[int(n * 0.1)], 4),
        "frac_low": round(low / n, 4),
        "word_min": word_min,
    }


@app.post("/assess", dependencies=[Depends(_require_key)])
async def assess(file: UploadFile = File(...), word_min: float | None = None) -> dict:
    """Run PaddleOCR on a single rendered page image and return confidence stats."""
    started = time.time()
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"unreadable image: {exc}") from exc

    arr = np.array(image)
    ocr, lock = _get_ocr()
    with lock:
        result = ocr.ocr(arr, cls=USE_ANGLE_CLS)

    scores = _extract_scores(result)
    stats = _summarize(scores, word_min if word_min is not None else DEFAULT_WORD_MIN)
    stats["elapsed_ms"] = int((time.time() - started) * 1000)
    logger.info(
        "assessed %s: n_lines=%s mean=%s frac_low=%s in %sms",
        file.filename,
        stats["n_lines"],
        stats["mean"],
        stats["frac_low"],
        stats["elapsed_ms"],
    )
    return stats
