"""Deterministic document preprocessing for controlled OCR experiments."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, ImageSequence


def preprocess_document(
    source: Path,
    output_root: Path,
    document_id: str,
    configuration: dict[str, Any] | None,
) -> tuple[Path, dict[str, Any]]:
    configuration = configuration or {"mode": "none"}
    mode = str(configuration.get("mode") or "none")
    started = time.perf_counter()
    if mode == "none":
        return source, {
            "mode": "none",
            "applied": False,
            "latency_seconds": 0.0,
            "input_sha256": _sha256(source),
            "output_sha256": _sha256(source),
        }
    if source.suffix.lower() == ".pdf":
        return source, {
            "mode": mode,
            "applied": False,
            "reason": "PDF preprocessing is not enabled",
            "latency_seconds": 0.0,
            "input_sha256": _sha256(source),
            "output_sha256": _sha256(source),
        }
    if mode not in {"autocontrast", "handwriting"}:
        raise ValueError(f"Unsupported preprocessing mode: {mode}")

    canonical = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    destination = output_root / f"{document_id}-{config_hash}.tiff"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        scale = float(configuration.get("scale") or (1.5 if mode == "handwriting" else 1.0))
        cutoff = float(configuration.get("cutoff_percent") or 0.5)
        with Image.open(source) as image:
            frames = []
            for frame in ImageSequence.Iterator(image):
                processed = ImageOps.autocontrast(frame.convert("L"), cutoff=cutoff)
                if scale != 1.0:
                    processed = processed.resize(
                        (round(processed.width * scale), round(processed.height * scale)),
                        Image.Resampling.LANCZOS,
                    )
                frames.append(processed)
            frames[0].save(
                destination,
                save_all=len(frames) > 1,
                append_images=frames[1:],
                compression="tiff_lzw",
                dpi=image.info.get("dpi", (300, 300)),
            )
    return destination, {
        "mode": mode,
        "applied": True,
        "configuration": configuration,
        "latency_seconds": time.perf_counter() - started,
        "input_sha256": _sha256(source),
        "output_sha256": _sha256(destination),
        "output_path": str(destination),
    }


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
