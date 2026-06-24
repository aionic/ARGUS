from __future__ import annotations

import hashlib
import logging
import threading
from collections import Counter
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_ocr.tiers import ALLOWED_TIERS

logger = logging.getLogger(__name__)

DEFAULT_DATASET = "default-dataset"
DEFAULT_TIERS = ["economy", "standard", "premium"]
DOCUMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
_PROCESSING_ENTRYPOINT_LOCK = threading.Lock()


@dataclass(frozen=True)
class ProfilingSource:
    label: str
    file_name: str
    path: Path | None = None
    blob_url: str | None = None


class LocalBlobInputStream:
    def __init__(self, blob_name: str, content: bytes):
        self.name = blob_name
        self.length = len(content)
        self._content = content

    def read(self, size: int = -1) -> bytes:
        if size == -1:
            return self._content
        return self._content[:size]


def run_cost_profile(
    dataset: str = DEFAULT_DATASET,
    files: Sequence[str] | None = None,
    tiers: Sequence[str] | None = None,
    *,
    data_container: Any | None = None,
    persist: bool = True,
    demo_root: str | Path | None = None,
    report_id: str | None = None,
) -> dict[str, Any]:
    """Run demo documents through the extraction pipeline for each requested tier."""
    selected_dataset = dataset or DEFAULT_DATASET
    selected_tiers = normalize_tiers(tiers)
    sources = resolve_profile_sources(selected_dataset, files, demo_root)
    container = _resolve_data_container(data_container)
    generated_at = datetime.now(timezone.utc).isoformat()
    batch_id = _batch_id(selected_dataset, selected_tiers, sources, generated_at)

    runs: list[dict[str, Any]] = []
    for tier in selected_tiers:
        for source in sources:
            runs.append(_run_single_profile(selected_dataset, tier, source, container, batch_id))

    report = build_profile_report(runs, selected_tiers, generated_at=generated_at)
    if persist:
        persist_profile_report(
            report,
            container,
            report_id=report_id or make_report_id(selected_dataset, selected_tiers, sources),
        )
    return report


def build_profile_report(
    runs: Sequence[dict[str, Any]],
    tiers: Sequence[str] | None = None,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    selected_tiers = list(tiers or sorted({str(run.get("tier")) for run in runs if run.get("tier")}))
    per_tier: dict[str, dict[str, Any]] = {}

    for tier in selected_tiers:
        tier_runs = [run for run in runs if run.get("tier") == tier]
        successful_runs = [run for run in tier_runs if run.get("success") is True]
        failure_types = Counter(
            str(run.get("failure_type") or "unknown") for run in tier_runs if run.get("success") is not True
        )
        quality_dist = Counter(str(run.get("quality_bucket") or "not_recorded") for run in tier_runs)

        cost_values = [_as_float(run.get("usd_per_page")) for run in successful_runs]
        token_values = [_as_int(run.get("total_tokens")) for run in successful_runs]
        per_tier[tier] = {
            "avg_usd_per_page": _average(cost_values),
            "avg_tokens": _average(token_values),
            "success_rate": (len(successful_runs) / len(tier_runs)) if tier_runs else 0.0,
            "failure_types": dict(sorted(failure_types.items())),
            "quality_dist": dict(sorted(quality_dist.items())),
        }

    return {
        "per_tier": per_tier,
        "runs": list(runs),
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
    }


def persist_profile_report(report: dict[str, Any], data_container: Any, *, report_id: str) -> str:
    document = {
        "id": report_id,
        "type": "profiling_report",
        "partitionKey": "profiling_report",
        **report,
    }
    data_container.upsert_item(document)
    logger.info("Persisted profiling report %s", report_id)
    return report_id


def make_report_id(dataset: str, tiers: Sequence[str], sources: Sequence[ProfilingSource]) -> str:
    digest_input = "|".join([dataset, ",".join(tiers), ",".join(source.label for source in sources)])
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:12]
    return f"profiling_report_{_safe_id_part(dataset)}_{digest}"


def normalize_tiers(tiers: Sequence[str] | str | None) -> list[str]:
    raw_tiers: Iterable[str]
    if tiers is None:
        raw_tiers = DEFAULT_TIERS
    elif isinstance(tiers, str):
        raw_tiers = tiers.split(",")
    else:
        raw_tiers = tiers

    normalized: list[str] = []
    for tier in raw_tiers:
        value = str(tier).strip().lower()
        if not value:
            continue
        if value not in ALLOWED_TIERS:
            allowed = ", ".join(sorted(ALLOWED_TIERS))
            raise ValueError(f"Unsupported tier '{tier}'. Allowed tiers: {allowed}")
        if value not in normalized:
            normalized.append(value)
    return normalized or list(DEFAULT_TIERS)


def resolve_profile_sources(
    dataset: str,
    files: Sequence[str] | None = None,
    demo_root: str | Path | None = None,
) -> list[ProfilingSource]:
    root = Path(demo_root) if demo_root else _repo_root() / "demo"
    dataset_dir = root / dataset
    if files:
        return [_resolve_source(dataset_dir, file_spec) for file_spec in files]

    if not dataset_dir.exists():
        raise FileNotFoundError(f"Demo dataset directory not found: {dataset_dir}")

    sources = [
        ProfilingSource(label=path.name, file_name=path.name, path=path)
        for path in sorted(dataset_dir.iterdir())
        if path.is_file() and path.suffix.lower() in DOCUMENT_EXTENSIONS
    ]
    if not sources:
        raise FileNotFoundError(f"No demo documents found for dataset '{dataset}' in {dataset_dir}")
    return sources


def _run_single_profile(
    dataset: str,
    tier: str,
    source: ProfilingSource,
    data_container: Any,
    batch_id: str,
) -> dict[str, Any]:
    blob_name = f"{dataset}/profiling/{batch_id}/{tier}/{source.file_name}"
    document_id = blob_name.replace("/", "__")
    base_result = {
        "dataset": dataset,
        "file": source.label,
        "document_id": document_id,
        "tier": tier,
        "success": False,
        "failure_type": None,
        "failure_category": None,
        "total_usd": 0.0,
        "usd_per_page": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tokens": 0,
        "flag": None,
        "image_quality": None,
        "quality_bucket": "not_recorded",
    }

    try:
        blob_input = LocalBlobInputStream(blob_name, _read_source(source))
        with _tier_override(tier):
            from blob_processing import process_blob

            document = process_blob(blob_input, data_container)
        return _run_result_from_document(base_result, document, success=True)
    except Exception as exc:  # noqa: BLE001 - each profile run must be isolated
        logger.exception("Profiling failed for %s at tier %s", source.label, tier)
        failed_document = _read_document(data_container, document_id)
        result = (
            _run_result_from_document(base_result, failed_document, success=False) if failed_document else base_result
        )
        result["success"] = False
        result["failure_type"] = type(exc).__name__
        result["failure_category"] = categorize_failure(exc)
        result["failure_message"] = str(exc)
        return result


def _run_result_from_document(
    base_result: dict[str, Any], document: dict[str, Any], *, success: bool
) -> dict[str, Any]:
    result = dict(base_result)
    properties = document.get("properties") or {}
    cost = properties.get("cost") or {}
    total_input_tokens = _as_int(cost.get("total_input_tokens"))
    total_output_tokens = _as_int(cost.get("total_output_tokens"))

    result.update(
        {
            "success": success,
            "total_usd": _as_float(cost.get("total_usd")),
            "usd_per_page": _as_float(cost.get("usd_per_page")),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
            "flag": properties.get("flag"),
            "image_quality": properties.get("image_quality"),
            "quality_bucket": quality_bucket(properties.get("flag"), properties.get("image_quality")),
        }
    )
    return result


def quality_bucket(flag: Any, image_quality: Any) -> str:
    if isinstance(flag, dict) and flag.get("flagged") is True:
        return "flagged"
    if isinstance(image_quality, list):
        return (
            "flagged"
            if any(report.get("flagged_low_quality") for report in image_quality if isinstance(report, dict))
            else "ok"
        )
    if isinstance(image_quality, dict):
        if image_quality.get("flagged") or image_quality.get("flagged_low_quality"):
            return "flagged"
        status = image_quality.get("status") or image_quality.get("overall")
        return str(status).lower() if status else "ok"
    return "not_recorded"


def categorize_failure(exc: Exception) -> str:
    message = str(exc).lower()
    if isinstance(exc, FileNotFoundError):
        return "input"
    if isinstance(exc, ValueError):
        return "configuration"
    if any(term in message for term in ("credential", "authentication", "unauthorized", "forbidden")):
        return "auth"
    if any(term in message for term in ("timeout", "connection", "network", "dns")):
        return "network"
    return "processing"


@contextmanager
def _tier_override(tier: str):
    with _PROCESSING_ENTRYPOINT_LOCK:
        import blob_processing

        original_fetch = blob_processing.fetch_model_prompt_and_schema

        def fetch_with_tier(dataset_type: str, force_refresh: bool = False):
            prompt, schema, max_pages, processing_options = original_fetch(dataset_type, force_refresh)
            options = dict(processing_options or {})
            options["tier"] = tier
            return prompt, schema, max_pages, options

        blob_processing.fetch_model_prompt_and_schema = fetch_with_tier
        try:
            yield
        finally:
            blob_processing.fetch_model_prompt_and_schema = original_fetch


def _read_source(source: ProfilingSource) -> bytes:
    if source.path:
        return source.path.read_bytes()
    if source.blob_url:
        from blob_processing import create_blob_input_stream

        return create_blob_input_stream(source.blob_url).read()
    raise ValueError(f"Source has no path or blob URL: {source.label}")


def _resolve_source(dataset_dir: Path, file_spec: str) -> ProfilingSource:
    spec = str(file_spec).strip()
    if not spec:
        raise ValueError("File names must not be empty")
    if spec.startswith(("http://", "https://")):
        return ProfilingSource(
            label=spec, file_name=Path(spec.split("?", 1)[0]).name or "uploaded-document", blob_url=spec
        )

    path = Path(spec)
    if not path.is_absolute():
        path = dataset_dir / path
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Profiling document not found: {path}")
    if path.suffix.lower() not in DOCUMENT_EXTENSIONS:
        raise ValueError(f"Unsupported profiling document type: {path}")
    return ProfilingSource(label=path.name, file_name=path.name, path=path)


def _resolve_data_container(data_container: Any | None) -> Any:
    if data_container is not None:
        return data_container

    from dependencies import get_data_container

    container = get_data_container()
    if container is not None:
        return container

    from ai_ocr.process import connect_to_cosmos

    docs_container, _ = connect_to_cosmos()
    return docs_container


def _read_document(data_container: Any, document_id: str) -> dict[str, Any] | None:
    try:
        return data_container.read_item(item=document_id, partition_key={})
    except Exception:
        try:
            items = list(
                data_container.query_items(
                    query="SELECT * FROM c WHERE c.id = @document_id",
                    parameters=[{"name": "@document_id", "value": document_id}],
                    enable_cross_partition_query=True,
                )
            )
            return items[0] if items else None
        except Exception:
            return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _batch_id(dataset: str, tiers: Sequence[str], sources: Sequence[ProfilingSource], generated_at: str) -> str:
    digest_input = "|".join([dataset, ",".join(tiers), ",".join(source.label for source in sources), generated_at])
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:12]


def _safe_id_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value) or "dataset"


def _average(values: Sequence[float | int]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
