from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field, fields

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class QualityReport:
    """Serializable image quality telemetry for OCR page-routing decisions."""

    path: str = ""
    blur_score: float = 0.0
    brightness: float = 0.0
    contrast: float = 0.0
    is_blank: bool = False
    width: int = 0
    height: int = 0
    effective_dpi: float | None = None
    skew_angle: float = 0.0
    is_low_quality: bool = False
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation suitable for Cosmos document properties."""
        return {
            "path": self.path,
            "blur_score": self.blur_score,
            "brightness": self.brightness,
            "contrast": self.contrast,
            "is_blank": self.is_blank,
            "width": self.width,
            "height": self.height,
            "effective_dpi": self.effective_dpi,
            "skew_angle": self.skew_angle,
            "is_low_quality": self.is_low_quality,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }


@dataclass
class QualityThresholds:
    """Tunable quality gates for document images.

    blur_min: minimum Laplacian variance; lower values are considered blurry.
    brightness_min / brightness_max: acceptable mean luminance range on a 0-255 scale.
    contrast_min: minimum luminance standard deviation for readable contrast.
    blank_std_max: maximum luminance standard deviation for near-uniform blank pages.
    min_width / min_height: minimum page dimensions before flagging low resolution.
    skew_flag_deg: skew angle worth correcting during enhancement.
    """

    blur_min: float = 100.0
    brightness_min: float = 40.0
    brightness_max: float = 225.0
    contrast_min: float = 25.0
    blank_std_max: float = 8.0
    min_width: int = 800
    min_height: int = 800
    skew_flag_deg: float = 2.0

    @classmethod
    def from_dict(cls, d: dict | None) -> QualityThresholds:
        """Build thresholds from optional overrides, ignoring unknown keys."""
        if not d:
            return cls()

        known_fields = {item.name for item in fields(cls)}
        overrides = {key: value for key, value in d.items() if key in known_fields}
        return cls(**overrides)


def assess_image_quality(path: str, thresholds: QualityThresholds | None = None) -> QualityReport:
    """Assess page-image quality without raising; failures are returned as low-quality reports."""
    quality_thresholds = thresholds or QualityThresholds()

    try:
        gray = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            logger.warning("Failed to load image for quality assessment: %s", path)
            return QualityReport(
                path=path,
                is_low_quality=True,
                reasons=["failed to load image"],
                metrics={"error": "failed to load image"},
            )

        height, width = gray.shape[:2]
        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(np.mean(gray))
        contrast = float(np.std(gray))
        white_pixel_ratio = float(np.mean(gray >= 245))
        black_pixel_ratio = float(np.mean(gray <= 10))
        is_blank = contrast < quality_thresholds.blank_std_max or white_pixel_ratio > 0.995 or black_pixel_ratio > 0.995
        skew_angle = _detect_skew(gray)
        effective_dpi = _estimate_effective_dpi(width, height)

        reasons: list[str] = []
        if blur_score < quality_thresholds.blur_min:
            reasons.append(f"blurry (laplacian={blur_score:.1f} < {quality_thresholds.blur_min:.1f})")
        if brightness < quality_thresholds.brightness_min:
            reasons.append(f"too dark (brightness={brightness:.1f} < {quality_thresholds.brightness_min:.1f})")
        # A high mean brightness alone is normal for documents (mostly-white pages);
        # only treat it as washed out when contrast is also poor.
        if brightness > quality_thresholds.brightness_max and contrast < quality_thresholds.contrast_min:
            reasons.append(
                f"too bright/washed out (brightness={brightness:.1f} > {quality_thresholds.brightness_max:.1f}, "
                f"contrast={contrast:.1f} < {quality_thresholds.contrast_min:.1f})"
            )
        if contrast < quality_thresholds.contrast_min:
            reasons.append(f"low contrast (std={contrast:.1f} < {quality_thresholds.contrast_min:.1f})")
        if is_blank:
            reasons.append("blank page")
        if width < quality_thresholds.min_width or height < quality_thresholds.min_height:
            reasons.append(
                f"low resolution ({width}x{height} < {quality_thresholds.min_width}x{quality_thresholds.min_height})"
            )

        metrics: dict[str, object] = {
            "blur_score": blur_score,
            "brightness": brightness,
            "contrast": contrast,
            "effective_dpi": effective_dpi,
            "height": height,
            "width": width,
            "is_blank": is_blank,
            "skew_angle": skew_angle,
            "white_pixel_ratio": white_pixel_ratio,
            "black_pixel_ratio": black_pixel_ratio,
            "thresholds": quality_thresholds.__dict__.copy(),
        }

        return QualityReport(
            path=path,
            blur_score=blur_score,
            brightness=brightness,
            contrast=contrast,
            is_blank=is_blank,
            width=width,
            height=height,
            effective_dpi=effective_dpi,
            skew_angle=skew_angle,
            is_low_quality=bool(reasons),
            reasons=reasons,
            metrics=metrics,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Quality assessment failed for %s: %s", path, exc)
        return QualityReport(
            path=path,
            is_low_quality=True,
            reasons=["quality assessment failed"],
            metrics={"error": str(exc)},
        )


def _detect_skew(gray: np.ndarray) -> float:
    """Estimate document skew in degrees using foreground pixels; return 0.0 if undetectable."""
    try:
        if gray.size == 0:
            return 0.0

        working = gray
        if working.ndim == 3:
            working = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)

        _, binary = cv2.threshold(working, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        foreground_ratio = float(np.mean(binary > 0))
        if foreground_ratio < 0.0005 or foreground_ratio > 0.90:
            return 0.0

        y_coords, x_coords = np.where(binary > 0)
        min_points = max(100, int(working.size * 0.0005))
        if len(x_coords) < min_points:
            return 0.0

        coords = np.column_stack((x_coords, y_coords)).astype(np.float32)
        angle = float(cv2.minAreaRect(coords)[-1])
        return _normalize_angle(angle)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Skew detection failed: %s", exc)
        return 0.0


def enhance_image(
    path: str,
    output_path: str | None = None,
    thresholds: QualityThresholds | None = None,
    binarize: bool = False,
) -> str:
    """Enhance an image for OCR/vision extraction and return the enhanced path, or the original on failure."""
    quality_thresholds = thresholds or QualityThresholds()

    try:
        image = cv2.imread(path, cv2.IMREAD_COLOR)
        if image is None:
            logger.warning("Failed to load image for enhancement: %s", path)
            return path

        gray_for_skew = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        skew_angle = _detect_skew(gray_for_skew)
        if abs(skew_angle) > quality_thresholds.skew_flag_deg:
            logger.info("Deskewing image %s by %.2f degrees", path, -skew_angle)
            image = _rotate_image(image, -skew_angle)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        denoised = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(denoised)
        enhanced = _upscale_if_needed(enhanced, quality_thresholds)

        if binarize:
            enhanced = cv2.adaptiveThreshold(
                enhanced,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                35,
                11,
            )

        target_path = output_path or _default_enhanced_path(path)
        target_dir = os.path.dirname(os.path.abspath(target_path))
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)

        if not cv2.imwrite(target_path, enhanced):
            logger.warning("Failed to write enhanced image: %s", target_path)
            return path

        logger.info("Enhanced image written to %s", target_path)
        return target_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("Image enhancement failed for %s: %s", path, exc)
        return path


def assess_and_enhance(
    path: str,
    thresholds: QualityThresholds | None = None,
    enable_enhancement: bool = True,
) -> tuple[str, QualityReport, QualityReport | None]:
    """Assess an image, optionally enhance low-quality inputs, and return the best path plus before/after reports."""
    try:
        report_before = assess_image_quality(path, thresholds)
        if not report_before.is_low_quality or not enable_enhancement:
            return path, report_before, None

        enhanced_path = enhance_image(path, thresholds=thresholds)
        report_after = assess_image_quality(enhanced_path, thresholds)
        use_enhanced = enhanced_path != path and (
            not report_after.is_low_quality or report_after.blur_score > report_before.blur_score
        )
        return (enhanced_path if use_enhanced else path), report_before, report_after
    except Exception as exc:  # noqa: BLE001
        logger.warning("Assess-and-enhance flow failed for %s: %s", path, exc)
        return (
            path,
            QualityReport(
                path=path,
                is_low_quality=True,
                reasons=["quality assessment failed"],
                metrics={"error": str(exc)},
            ),
            None,
        )


def _default_enhanced_path(path: str) -> str:
    base_path, _extension = os.path.splitext(path)
    return f"{base_path}.enhanced.png"


def _estimate_effective_dpi(width: int, height: int) -> float | None:
    """Estimate DPI from dimensions using a US Letter page assumption when embedded DPI is unavailable."""
    if width <= 0 or height <= 0:
        return None

    if height >= width:
        dpi_x = width / 8.5
        dpi_y = height / 11.0
    else:
        dpi_x = width / 11.0
        dpi_y = height / 8.5

    estimate = (dpi_x + dpi_y) / 2.0
    if not math.isfinite(estimate) or estimate <= 0:
        return None
    return float(estimate)


def _normalize_angle(angle: float) -> float:
    normalized = angle
    while normalized <= -45.0:
        normalized += 90.0
    while normalized > 45.0:
        normalized -= 90.0
    return 45.0 if math.isclose(normalized, -45.0) else float(normalized)


def _rotate_image(image: np.ndarray, angle: float) -> np.ndarray:
    height, width = image.shape[:2]
    center = (width / 2.0, height / 2.0)
    rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos_value = abs(rotation_matrix[0, 0])
    sin_value = abs(rotation_matrix[0, 1])
    new_width = int((height * sin_value) + (width * cos_value))
    new_height = int((height * cos_value) + (width * sin_value))

    rotation_matrix[0, 2] += (new_width / 2.0) - center[0]
    rotation_matrix[1, 2] += (new_height / 2.0) - center[1]

    border_value: int | tuple[int, int, int] = 255
    if image.ndim == 3:
        border_value = (255, 255, 255)

    return cv2.warpAffine(
        image,
        rotation_matrix,
        (new_width, new_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def _upscale_if_needed(image: np.ndarray, thresholds: QualityThresholds) -> np.ndarray:
    height, width = image.shape[:2]
    if width <= 0 or height <= 0 or (width >= thresholds.min_width and height >= thresholds.min_height):
        return image

    required_scale = max(thresholds.min_width / width, thresholds.min_height / height)
    scale = min(required_scale, 2.0)
    long_edge = max(width, height)
    if long_edge * scale > 3000:
        scale = 3000 / long_edge

    if scale <= 1.0:
        return image

    new_size = (int(width * scale), int(height * scale))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_CUBIC)
