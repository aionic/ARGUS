"""Conduent POC: per-document-type pricing run.

Runs each of Ryan's three sample buckets as its own separate round and reports a
discrete per-image cost for each, replacing the single blended $0.01285 rate that
Judi pushed back on.

Only the samples supplied in Ryan's folders are used. Cost is the actual metered
cost reported by Content Understanding for each document (content-extraction meter
tier, contextualization tokens, and generative LLM tokens), collected from the live
ARGUS deployment.

Usage
-----
    # Inventory and validate the sample set without spending anything
    python scripts/conduent_pricing_run.py --inventory-only

    # Run all three buckets against the live deployment
    $env:ARGUS_BACKEND = (azd env get-value BACKEND_URL)
    $env:ARGUS_API_KEY = (az containerapp secret show -g rg-argus-dev -n ca-argus `
        --secret-name api-key --query value -o tsv)
    python scripts/conduent_pricing_run.py
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conduent_pricing_schemas import example_schema, model_prompt, schema_source  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "_working"

IMAGE_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".pdf"}
# Ryan's manifest lists the black-and-white CMS-1500 set as truth-only with no
# usable images, so .oci files are inventoried but never priced.
TRUTH_ONLY_EXTENSIONS = {".oci"}


@dataclass(frozen=True)
class Bucket:
    key: str
    label: str
    directory: str
    tier: str
    annual_volume: int
    current_low: float
    current_high: float
    quality_mix: str


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    bucket: str
    subdirectory: str | None
    schema_key: str
    expected_images: int
    has_truth: bool
    truth_source: str | None = None


@dataclass(frozen=True)
class Sample:
    group: Group
    path: Path

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class DocumentResult:
    sample: Sample
    status: str
    total_usd: float = 0.0
    list_total_usd: float = 0.0
    discount_pct: float = 0.0
    pages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tier: str | None = None
    per_stage: list[dict[str, Any]] = field(default_factory=list)
    flag_reasons: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def usd_per_image(self) -> float:
        return self.total_usd


BUCKETS: dict[str, Bucket] = {
    "structured": Bucket(
        key="structured",
        label="Structured",
        directory="1 - Structured",
        tier="economy",
        annual_volume=50_000_000,
        current_low=0.002,
        current_high=0.004,
        quality_mix="80% good / 10% medium / 10% bad",
    ),
    "semi-structured": Bucket(
        key="semi-structured",
        label="Semi-Structured",
        directory="2 - Semi-Structured",
        tier="standard",
        annual_volume=10_000_000,
        current_low=0.004,
        current_high=0.005,
        quality_mix="70% good / 10% medium / 20% bad",
    ),
    "handwritten": Bucket(
        key="handwritten",
        label="Handwritten",
        directory="3 - Handwritten",
        tier="premium",
        annual_volume=3_000_000,
        current_low=0.010,
        current_high=0.015,
        quality_mix="70% good / 20% medium / 10% bad",
    ),
}

GROUPS: list[Group] = [
    Group(
        "cms1500-red-drop", "CMS-1500 Red-Drop", "structured", "CMS-1500 Red-Drop", "cms1500", 16, True, "Red Truth.csv"
    ),
    Group("ub04-cms1450", "UB-04 / CMS-1450", "structured", "UB-04", "ub04", 4, False),
    Group("irs-w9", "IRS W-9", "structured", "IRS W-9", "w9", 1, False),
    Group(
        "commercial-invoices",
        "Commercial / customs invoices",
        "semi-structured",
        "Invoices",
        "commercial-document",
        7,
        True,
        "MS_Truth_Data.xlsx :: Comm_Invoices",
    ),
    Group("eob-remittance", "EOB & remittance advice", "semi-structured", "EOB and Remittance", "eob", 4, False),
    Group(
        "enrollments",
        "Humana enrollment / employee change forms",
        "handwritten",
        None,
        "enrollment",
        4,
        True,
        "MS_Truth_Data.xlsx :: Enrollments",
    ),
]

# Judi pays roughly this much per image on top of vendor fees for on-prem infrastructure.
ON_PREM_INFRA_PER_IMAGE = 0.01
PRIOR_BLENDED_RATE = 0.01285


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #
def _match_directory(parent: Path, needle: str) -> Path | None:
    normalized = needle.casefold()
    for child in sorted(parent.iterdir()):
        if child.is_dir() and normalized in child.name.casefold():
            return child
    return None


def build_inventory(source: Path) -> tuple[list[Sample], list[dict[str, Any]]]:
    """Enumerate priceable images from Ryan's folder layout."""
    if not source.is_dir():
        raise FileNotFoundError(f"Sample source folder not found: {source}")

    samples: list[Sample] = []
    notes: list[dict[str, Any]] = []

    for group in GROUPS:
        bucket = BUCKETS[group.bucket]
        bucket_dir = _match_directory(source, bucket.directory)
        if bucket_dir is None:
            raise FileNotFoundError(f"Bucket folder '{bucket.directory}' not found under {source}")

        if group.subdirectory is None:
            group_dir = bucket_dir
            candidates = [path for path in sorted(group_dir.iterdir()) if path.is_file()]
        else:
            group_dir = _match_directory(bucket_dir, group.subdirectory)
            if group_dir is None:
                raise FileNotFoundError(f"Group folder '{group.subdirectory}' not found under {bucket_dir}")
            candidates = [path for path in sorted(group_dir.rglob("*")) if path.is_file()]

        images = [path for path in candidates if path.suffix.casefold() in IMAGE_EXTENSIONS]
        samples.extend(Sample(group, path) for path in images)

        if len(images) != group.expected_images:
            notes.append(
                {
                    "type": "count_mismatch",
                    "group": group.key,
                    "expected": group.expected_images,
                    "found": len(images),
                    "folder": str(group_dir),
                }
            )

    for bucket in BUCKETS.values():
        bucket_dir = _match_directory(source, bucket.directory)
        if bucket_dir is None:
            continue
        for path in sorted(bucket_dir.rglob("*")):
            if path.is_file() and path.suffix.casefold() in TRUTH_ONLY_EXTENSIONS:
                notes.append(
                    {
                        "type": "excluded_truth_only",
                        "bucket": bucket.key,
                        "file": path.name,
                        "reason": "Listed as truth-only with no usable image in the supplied manifest",
                    }
                )

    return samples, notes


# --------------------------------------------------------------------------- #
# Deployment configuration
# --------------------------------------------------------------------------- #
class ArgusClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})

    def get_configuration(self) -> dict[str, Any]:
        response = self.session.get(f"{self.base_url}/api/configuration", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def update_configuration(self, configuration: dict[str, Any]) -> None:
        response = self.session.post(f"{self.base_url}/api/configuration", json=configuration, timeout=self.timeout)
        response.raise_for_status()

    def upload(self, dataset: str, path: Path) -> str:
        with path.open("rb") as handle:
            response = self.session.post(
                f"{self.base_url}/api/datasets/{dataset}/upload",
                files={"file": (path.name, handle)},
                params={"run_summary": "false", "run_evaluation": "false"},
                timeout=self.timeout,
            )
        response.raise_for_status()
        return response.json()["document_id"]

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        response = self.session.get(f"{self.base_url}/api/documents/{document_id}", timeout=self.timeout)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        return payload.get("document", payload) if isinstance(payload, dict) else None


def dataset_name(prefix: str, group: Group) -> str:
    return f"{prefix}-{group.key}"


def ensure_datasets(client: ArgusClient, prefix: str, groups: list[Group]) -> dict[str, str]:
    """Register one dataset per form type, pinned to its bucket's tier."""
    configuration = client.get_configuration()
    datasets = configuration.setdefault("datasets", {})
    mapping: dict[str, str] = {}

    for group in groups:
        name = dataset_name(prefix, group)
        mapping[group.key] = name
        bucket = BUCKETS[group.bucket]
        datasets[name] = {
            "model_prompt": model_prompt(group.schema_key),
            "example_schema": example_schema(group.schema_key),
            "max_pages_per_chunk": 10,
            "processing_options": {
                "extraction_backend": "content_understanding",
                "tier": bucket.tier,
                "enable_summary": False,
                "enable_evaluation": False,
            },
        }

    client.update_configuration(configuration)
    return mapping


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def _is_complete(document: dict[str, Any]) -> bool:
    state = document.get("state") or {}
    return bool(state.get("processing_completed"))


def _failure_reason(document: dict[str, Any]) -> str | None:
    errors = document.get("errors")
    if isinstance(errors, list) and errors:
        return str(errors[-1])
    if isinstance(errors, str) and errors.strip():
        return errors
    state = document.get("state") or {}
    if state.get("processing_failed"):
        return "processing_failed"
    return None


def run_sample(client: ArgusClient, sample: Sample, dataset: str, poll_timeout: int) -> DocumentResult:
    try:
        document_id = client.upload(dataset, sample.path)
    except Exception as exc:  # noqa: BLE001 - one bad document must not end the round
        return DocumentResult(sample, "upload_failed", error=f"{type(exc).__name__}: {exc}")

    deadline = time.monotonic() + poll_timeout
    document: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        time.sleep(5)
        try:
            document = client.get_document(document_id)
        except Exception:  # noqa: BLE001 - transient read errors are retried until timeout
            continue
        if not document:
            continue
        reason = _failure_reason(document)
        if reason:
            return DocumentResult(sample, "failed", error=reason)
        if _is_complete(document):
            break
    else:
        return DocumentResult(sample, "timeout", error=f"Not completed within {poll_timeout}s")

    if not document or not _is_complete(document):
        return DocumentResult(sample, "timeout", error=f"Not completed within {poll_timeout}s")

    properties = document.get("properties") or {}
    cost = properties.get("cost") or {}
    flag = properties.get("flag") or {}
    reasons = [str(reason) for reason in (flag.get("reasons") or [])] if isinstance(flag, dict) else []
    total_usd = float(cost.get("total_usd") or 0.0)
    # The quality pregate rejects unreadable scans before any paid extraction runs, so the
    # document legitimately costs nothing. Averaging those zeros into a rate would understate it.
    gated = bool(isinstance(flag, dict) and flag.get("flagged")) and total_usd == 0.0
    return DocumentResult(
        sample=sample,
        status="quality_gated" if gated else "ok",
        total_usd=total_usd,
        list_total_usd=float(cost.get("list_total_usd") or total_usd),
        discount_pct=float(cost.get("discount_pct") or 0.0),
        pages=int(properties.get("num_pages") or 0),
        input_tokens=int(cost.get("total_input_tokens") or 0),
        output_tokens=int(cost.get("total_output_tokens") or 0),
        tier=properties.get("tier"),
        per_stage=list(cost.get("per_stage") or []),
        flag_reasons=reasons,
    )


def run_round(
    client: ArgusClient,
    bucket: Bucket,
    samples: list[Sample],
    datasets: dict[str, str],
    poll_timeout: int,
    workers: int,
) -> list[DocumentResult]:
    print(f"\n=== Round: {bucket.label} ({len(samples)} images, tier={bucket.tier}) ===")
    results: list[DocumentResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(run_sample, client, sample, datasets[sample.group.key], poll_timeout) for sample in samples
        ]
        for future in futures:
            result = future.result()
            results.append(result)
            marker = "ok" if result.status == "ok" else result.status.upper()
            if result.status == "ok":
                detail = f"${result.usd_per_image:.6f}"
            elif result.status == "quality_gated":
                detail = "rejected before paid extraction: " + "; ".join(result.flag_reasons)
            else:
                detail = result.error or ""
            print(f"  [{marker:>13}] {result.sample.group.key}/{result.sample.name}: {detail}")
    return results


# --------------------------------------------------------------------------- #
# Aggregation and reporting
# --------------------------------------------------------------------------- #
def _stage_totals(results: list[DocumentResult]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for result in results:
        for stage in result.per_stage:
            name = str(stage.get("stage") or "unknown")
            totals[name] = totals.get(name, 0.0) + float(stage.get("usd") or 0.0)
    return dict(sorted(totals.items(), key=lambda item: -item[1]))


def summarize(results: list[DocumentResult]) -> dict[str, Any]:
    priced = [result for result in results if result.status == "ok"]
    gated = [result for result in results if result.status == "quality_gated"]
    costs = [result.usd_per_image for result in priced]
    list_costs = [result.list_total_usd for result in priced]
    discounts = {result.discount_pct for result in priced}
    return {
        "images_attempted": len(results),
        "images_priced": len(priced),
        "images_quality_gated": len(gated),
        "quality_gated": [{"file": result.sample.name, "reasons": result.flag_reasons} for result in gated],
        "failures": [
            {"file": result.sample.name, "status": result.status, "error": result.error}
            for result in results
            if result.status not in ("ok", "quality_gated")
        ],
        "discount_pct": max(discounts) if discounts else 0.0,
        "usd_per_image_mean": statistics.fmean(costs) if costs else None,
        "usd_per_image_median": statistics.median(costs) if costs else None,
        "usd_per_image_min": min(costs) if costs else None,
        "usd_per_image_max": max(costs) if costs else None,
        "list_usd_per_image_mean": statistics.fmean(list_costs) if list_costs else None,
        "total_usd": sum(costs),
        "total_pages": sum(result.pages for result in priced),
        "total_input_tokens": sum(result.input_tokens for result in priced),
        "total_output_tokens": sum(result.output_tokens for result in priced),
        "cost_by_stage_usd": _stage_totals(priced),
    }


def build_report(
    results_by_bucket: dict[str, list[DocumentResult]],
    inventory_notes: list[dict[str, Any]],
    source: Path,
) -> dict[str, Any]:
    buckets: dict[str, Any] = {}

    for bucket_key, results in results_by_bucket.items():
        bucket = BUCKETS[bucket_key]
        summary = summarize(results)
        rate = summary["usd_per_image_mean"]

        groups: dict[str, Any] = {}
        for group in [item for item in GROUPS if item.bucket == bucket_key]:
            group_results = [result for result in results if result.sample.group.key == group.key]
            if not group_results:
                continue
            group_summary = summarize(group_results)
            groups[group.key] = {
                "label": group.label,
                "schema_source": schema_source(group.schema_key),
                "has_truth_data": group.has_truth,
                "truth_source": group.truth_source,
                "accuracy_reportable": group.has_truth,
                **group_summary,
            }

        projection = None
        if rate:
            annual = rate * bucket.annual_volume
            projection = {
                "annual_volume": bucket.annual_volume,
                "argus_annual_usd": annual,
                "customer_current_low_usd": bucket.current_low * bucket.annual_volume,
                "customer_current_high_usd": bucket.current_high * bucket.annual_volume,
                "customer_rate_low": bucket.current_low,
                "customer_rate_high": bucket.current_high,
                "delta_vs_current_high_usd": annual - bucket.current_high * bucket.annual_volume,
                "meets_current_pricing": rate <= bucket.current_high,
                "prior_blended_annual_usd": PRIOR_BLENDED_RATE * bucket.annual_volume,
            }

        buckets[bucket_key] = {
            "label": bucket.label,
            "tier": bucket.tier,
            "usd_per_image": rate,
            "list_usd_per_image": summary["list_usd_per_image_mean"],
            "customer_quality_mix": bucket.quality_mix,
            "quality_labels_available": False,
            "projection": projection,
            "groups": groups,
            **summary,
        }

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "sample_source": str(source),
        "prior_blended_rate": PRIOR_BLENDED_RATE,
        "on_prem_infra_per_image": ON_PREM_INFRA_PER_IMAGE,
        "buckets": buckets,
        "inventory_notes": inventory_notes,
        "caveats": _caveats(buckets),
    }


def _caveats(buckets: dict[str, Any]) -> list[str]:
    caveats = [
        "Sample images are NOT quality-labeled. None of the supplied samples carry a good / medium / bad "
        "grading, so each rate is a blended average across whatever quality mix these particular images "
        "happen to represent. These rates cannot be projected onto the stated volume quality mix "
        "(80/10/10 structured, 70/10/20 semi-structured, 70/20/10 handwritten) without a labeled sample set.",
    ]

    sizes = ", ".join(f"{payload['label']} n={payload['images_priced']}" for payload in buckets.values())
    caveats.append(
        f"Small sample size: rates are derived from a very small corpus ({sizes}). These are directional "
        "figures for business-case shaping, not contractual rates. A larger, quality-stratified sample is "
        "required before committing to per-image pricing at production volume."
    )

    no_truth = [
        f"{group['label']} (n={group['images_priced']})"
        for payload in buckets.values()
        for group in payload["groups"].values()
        if not group["has_truth_data"]
    ]
    if no_truth:
        caveats.append(
            "Accuracy cannot be displayed for document types with no supplied truth data: "
            + "; ".join(no_truth)
            + ". Cost for these types is measured and valid, but no extraction-accuracy claim is made."
        )

    gated = [
        f"{payload['label']} ({payload['images_quality_gated']} of {payload['images_attempted']})"
        for payload in buckets.values()
        if payload.get("images_quality_gated")
    ]
    if gated:
        caveats.append(
            "Quality-gated images are excluded from the rates: "
            + "; ".join(gated)
            + ". These scans were rejected by the pre-extraction quality gate, so no paid extraction ran and "
            "they genuinely cost $0. They are excluded rather than averaged in, because counting them as $0 "
            "would understate the true per-image rate. In production this gate is a cost saver on bad scans, "
            "but those documents still require a downstream path (rescan or manual handling)."
        )

    discounts = {payload.get("discount_pct") or 0.0 for payload in buckets.values()}
    applied = max(discounts) if discounts else 0.0
    if applied:
        caveats.append(
            f"Rates are NET of a {applied:.0f}% agreement discount configured on this deployment. Azure list "
            "price is shown alongside each net rate. Confirm the discount that will actually apply to Conduent "
            "before quoting the net figure externally."
        )

    caveats.append(
        "Document Intelligence OCR preflight cost is immaterial at these volumes (order of tens of dollars "
        "across millions of documents) and is included in the reported figures."
    )
    return caveats


def _usd(value: float | None, places: int = 6) -> str:
    return f"${value:,.{places}f}" if isinstance(value, (int, float)) else "n/a"


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Conduent POC - Per-Document-Type Pricing")
    lines.append("")
    lines.append(f"Generated: {report['generated_at_utc']}")
    lines.append("")
    lines.append("## Per-image rate by document type")
    lines.append("")
    lines.append(
        "| Document type | Tier | Images priced | Gated | ARGUS $/image (net) | ARGUS $/image (list) | "
        "Customer pays today | Meets target |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---|:--:|")
    for payload in report["buckets"].values():
        projection = payload.get("projection") or {}
        current = (
            f"{_usd(projection.get('customer_rate_low'), 4)} - {_usd(projection.get('customer_rate_high'), 4)}"
            if projection
            else "n/a"
        )
        meets = "yes" if projection.get("meets_current_pricing") else "no"
        lines.append(
            f"| {payload['label']} | {payload['tier']} | {payload['images_priced']} | "
            f"{payload['images_quality_gated']} | {_usd(payload['usd_per_image'])} | "
            f"{_usd(payload['list_usd_per_image'])} | {current} | {meets} |"
        )

    lines.append("")
    lines.append("## Annual projection")
    lines.append("")
    lines.append("| Document type | Annual volume | ARGUS annual | Customer vendor spend today | Delta vs high end |")
    lines.append("|---|---:|---:|---:|---:|")
    argus_total = 0.0
    current_low_total = 0.0
    current_high_total = 0.0
    for payload in report["buckets"].values():
        projection = payload.get("projection")
        if not projection:
            continue
        argus_total += projection["argus_annual_usd"]
        current_low_total += projection["customer_current_low_usd"]
        current_high_total += projection["customer_current_high_usd"]
        lines.append(
            f"| {payload['label']} | {projection['annual_volume']:,} | "
            f"{_usd(projection['argus_annual_usd'], 0)} | "
            f"{_usd(projection['customer_current_low_usd'], 0)} - {_usd(projection['customer_current_high_usd'], 0)} | "
            f"{_usd(projection['delta_vs_current_high_usd'], 0)} |"
        )
    lines.append(
        f"| **Total** | | **{_usd(argus_total, 0)}** | "
        f"**{_usd(current_low_total, 0)} - {_usd(current_high_total, 0)}** | "
        f"**{_usd(argus_total - current_high_total, 0)}** |"
    )

    lines.append("")
    lines.append("## Cost composition by document type")
    lines.append("")
    for payload in report["buckets"].values():
        lines.append(f"### {payload['label']}")
        lines.append("")
        lines.append("| Stage | Total USD across sample | Share |")
        lines.append("|---|---:|---:|")
        total = sum(payload["cost_by_stage_usd"].values()) or 1.0
        for stage, usd in payload["cost_by_stage_usd"].items():
            lines.append(f"| {stage} | {_usd(usd)} | {usd / total:.1%} |")
        lines.append("")
        lines.append("| Form type | Images | $/image | Truth data | Accuracy reportable |")
        lines.append("|---|---:|---:|---|---|")
        for group in payload["groups"].values():
            lines.append(
                f"| {group['label']} | {group['images_priced']} | {_usd(group['usd_per_image_mean'])} | "
                f"{group['truth_source'] or 'none supplied'} | "
                f"{'yes' if group['accuracy_reportable'] else 'NO - cost only'} |"
            )
        lines.append("")

    lines.append("## Caveats")
    lines.append("")
    for caveat in report["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")

    if report["inventory_notes"]:
        lines.append("## Inventory notes")
        lines.append("")
        for note in report["inventory_notes"]:
            lines.append(f"- `{note['type']}` " + json.dumps({k: v for k, v in note.items() if k != "type"}))
        lines.append("")

    return "\n".join(lines)


def write_documents_csv(path: Path, results_by_bucket: dict[str, list[DocumentResult]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "bucket",
                "form_type",
                "file",
                "status",
                "tier",
                "pages",
                "usd_per_image_net",
                "usd_per_image_list",
                "discount_pct",
                "input_tokens",
                "output_tokens",
                "has_truth_data",
                "quality_gate_reasons",
                "error",
            ]
        )
        for bucket_key, results in results_by_bucket.items():
            for result in results:
                writer.writerow(
                    [
                        bucket_key,
                        result.sample.group.key,
                        result.sample.name,
                        result.status,
                        result.tier or "",
                        result.pages,
                        f"{result.total_usd:.8f}" if result.status == "ok" else "",
                        f"{result.list_total_usd:.8f}" if result.status == "ok" else "",
                        result.discount_pct,
                        result.input_tokens,
                        result.output_tokens,
                        result.sample.group.has_truth,
                        "; ".join(result.flag_reasons),
                        result.error or "",
                    ]
                )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Folder holding Ryan's sample buckets.")
    parser.add_argument("--backend", default=os.getenv("ARGUS_BACKEND", ""), help="ARGUS backend base URL.")
    parser.add_argument("--api-key", default=os.getenv("ARGUS_API_KEY", ""), help="ARGUS API key.")
    parser.add_argument("--buckets", default="", help="Comma-separated bucket keys. Defaults to all three.")
    parser.add_argument("--dataset-prefix", default="pricing", help="Prefix for the datasets created per form type.")
    parser.add_argument("--out", type=Path, default=None, help="Output directory for the rate card.")
    parser.add_argument("--poll-timeout", type=int, default=900, help="Seconds to wait for one document.")
    parser.add_argument("--workers", type=int, default=3, help="Concurrent documents in flight.")
    parser.add_argument("--inventory-only", action="store_true", help="Validate the sample set and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    samples, notes = build_inventory(source)

    selected = [key.strip() for key in args.buckets.split(",") if key.strip()] or list(BUCKETS)
    unknown = [key for key in selected if key not in BUCKETS]
    if unknown:
        raise SystemExit(f"Unknown bucket(s): {', '.join(unknown)}. Valid: {', '.join(BUCKETS)}")

    print(f"Sample source: {source}")
    for bucket_key in selected:
        bucket = BUCKETS[bucket_key]
        bucket_samples = [sample for sample in samples if sample.group.bucket == bucket_key]
        print(f"  {bucket.label:<16} {len(bucket_samples):>3} images  tier={bucket.tier}")
        for group in [item for item in GROUPS if item.bucket == bucket_key]:
            count = len([sample for sample in bucket_samples if sample.group.key == group.key])
            truth = group.truth_source or "NO TRUTH - cost only"
            print(f"      {group.label:<44} {count:>3}  {truth}")
    print(f"  {'TOTAL':<16} {len([s for s in samples if s.group.bucket in selected]):>3} images")
    for note in notes:
        print(f"  note: {note}")

    if args.inventory_only:
        return 0

    if not args.backend or not args.api_key:
        raise SystemExit("Set --backend/--api-key or ARGUS_BACKEND/ARGUS_API_KEY.")

    client = ArgusClient(args.backend, args.api_key)
    groups = [group for group in GROUPS if group.bucket in selected]
    print("\nRegistering pricing datasets...")
    datasets = ensure_datasets(client, args.dataset_prefix, groups)
    for group_key, name in datasets.items():
        print(f"  {group_key} -> {name}")

    results_by_bucket: dict[str, list[DocumentResult]] = {}
    for bucket_key in selected:
        bucket_samples = [sample for sample in samples if sample.group.bucket == bucket_key]
        results_by_bucket[bucket_key] = run_round(
            client, BUCKETS[bucket_key], bucket_samples, datasets, args.poll_timeout, args.workers
        )

    report = build_report(results_by_bucket, notes, source)
    out_dir = args.out or (source / "pricing-runs" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rate_card.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = render_markdown(report)
    (out_dir / "rate_card.md").write_text(markdown, encoding="utf-8")
    write_documents_csv(out_dir / "documents.csv", results_by_bucket)

    print("\n" + markdown)
    print(f"\nWrote rate card to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
