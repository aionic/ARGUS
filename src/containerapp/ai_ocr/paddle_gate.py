"""PaddleOCR pre-gate client.

A cheap, self-hosted legibility probe that runs *before* the paid Document
Intelligence / Content Understanding call. The backend renders page images and POSTs
them to the internal ``ca-argus-paddleocr`` Container App (``PADDLE_OCR_URL``); the
service returns aggregate per-line recognition confidence. If a scan reads as too
low-quality to extract, the pipeline can short-circuit (``block`` mode) — skipping the
paid extraction call, flagging the document, and routing it to review — or merely flag
it (``advisory`` mode).

This mirrors the Document Intelligence word-confidence gate
(``blob_processing._ocr_confidence_preflight``), which stays as the backstop for docs
that pass Paddle.
"""

from __future__ import annotations

import logging
import os

import httpx

from ai_ocr.process import convert_pdf_into_image

logger = logging.getLogger(__name__)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def paddle_pregate_enabled(processing_options: dict | None = None) -> bool:
    """Per-dataset override (``enable_paddle_pregate``) else the env default.

    Requires ``PADDLE_OCR_URL`` to be set regardless — without it there is no service
    to call, so the gate is a no-op.
    """
    if not (os.getenv("PADDLE_OCR_URL") or "").strip():
        return False
    if processing_options and "enable_paddle_pregate" in processing_options:
        return bool(processing_options.get("enable_paddle_pregate"))
    return _bool_env("ENABLE_PADDLE_PREGATE", False)


def paddle_pregate_mode() -> str:
    """``block`` (skip extraction on a bad verdict) or ``advisory`` (flag only)."""
    mode = (os.getenv("PADDLE_PREGATE_MODE", "block") or "block").strip().lower()
    return mode if mode in ("block", "advisory") else "block"


def _thresholds() -> tuple[float, float, float]:
    """(word_min, mean_min, low_frac_max) for the Paddle confidence gate (env-tunable)."""
    return (
        _float_env("PADDLE_CONFIDENCE_WORD_MIN", 0.70),
        _float_env("PADDLE_CONFIDENCE_MEAN_MIN", 0.80),
        _float_env("PADDLE_CONFIDENCE_LOW_FRAC_MAX", 0.25),
    )


def _assess_page(client: httpx.Client, url: str, image_path: str, word_min: float) -> dict | None:
    headers = {}
    service_key = (os.getenv("PADDLE_SERVICE_KEY") or "").strip()
    if service_key:
        headers["X-Service-Key"] = service_key
    try:
        with open(image_path, "rb") as fh:
            files = {"file": (os.path.basename(image_path), fh, "image/png")}
            resp = client.post(
                f"{url.rstrip('/')}/assess",
                params={"word_min": word_min},
                files=files,
                headers=headers,
            )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - the gate must never break processing
        logger.warning("PaddleOCR /assess failed for %s: %s", image_path, exc)
        return None


def assess_paddle_confidence(file_paths: list[str]) -> dict | None:
    """Render page images for each chunk and probe PaddleOCR for legibility.

    Returns aggregate ``{n_lines, mean, frac_low, min, n_pages, word_min, per_page}``
    (line-weighted mean/frac_low across pages), or ``None`` when the service is
    unreachable or no page could be assessed (so the caller falls back to the DI gate).
    """
    url = (os.getenv("PADDLE_OCR_URL") or "").strip()
    if not url:
        return None
    word_min, _, _ = _thresholds()
    max_pages = max(1, _int_env("PADDLE_PREGATE_MAX_PAGES", 3))
    timeout = _float_env("PADDLE_PREGATE_TIMEOUT", 120.0)

    temp_dirs: list[str] = []
    per_page: list[dict] = []
    try:
        with httpx.Client(timeout=timeout) as client:
            for file_path in file_paths:
                if len(per_page) >= max_pages:
                    break
                try:
                    temp_dir = convert_pdf_into_image(file_path)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Could not render %s for Paddle pre-gate: %s", file_path, exc)
                    continue
                temp_dirs.append(temp_dir)
                page_files = sorted(
                    os.path.join(temp_dir, f) for f in os.listdir(temp_dir) if f.lower().endswith(".png")
                )
                for page_file in page_files:
                    if len(per_page) >= max_pages:
                        break
                    stats = _assess_page(client, url, page_file, word_min)
                    if stats is not None:
                        per_page.append(stats)
    finally:
        import shutil

        for temp_dir in temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if not per_page:
        return None

    total_lines = sum(int(p.get("n_lines", 0)) for p in per_page)
    if total_lines > 0:
        mean = sum(float(p.get("mean", 0.0)) * int(p.get("n_lines", 0)) for p in per_page) / total_lines
        frac_low = sum(float(p.get("frac_low", 0.0)) * int(p.get("n_lines", 0)) for p in per_page) / total_lines
    else:
        # No text recognised on any page -> maximally illegible.
        mean = 0.0
        frac_low = 1.0
    return {
        "n_pages": len(per_page),
        "n_lines": total_lines,
        "mean": round(mean, 4),
        "frac_low": round(frac_low, 4),
        "min": round(min(float(p.get("min", 0.0)) for p in per_page), 4),
        "word_min": word_min,
        "per_page": per_page,
    }


def paddle_verdict(stats: dict | None) -> list[str]:
    """Return flag reasons when the Paddle stats read as low-quality, else ``[]``."""
    if not stats:
        return []
    _, mean_min, low_frac_max = _thresholds()
    reasons: list[str] = []
    if stats["mean"] < mean_min:
        reasons.append(f"paddle_low_confidence (mean {stats['mean']:.2f} < {mean_min:.2f})")
    if stats["frac_low"] > low_frac_max:
        reasons.append(
            f"paddle_high_low_confidence_fraction ({stats['frac_low']:.0%} of lines < {stats['word_min']:.2f})"
        )
    return reasons
