"""Canonical Conduent corpus loading, validation, and split handling."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

GOLDEN_DATASETS = ("cms1500", "commercial-documents", "enrollments", "invoice-demo")


@dataclass(frozen=True)
class CorpusCase:
    dataset: str
    document_id: str
    document_path: Path
    original_filename: str
    schema_id: str
    schema: dict[str, Any]
    truth: dict[str, Any] | None
    manifest: dict[str, Any]
    split: str


def discover_corpus_root(explicit: str | Path | None = None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.getenv("CONDUENT_CORPUS_ROOT"):
        candidates.append(Path(os.environ["CONDUENT_CORPUS_ROOT"]))
    module_path = Path(__file__).resolve()
    if len(module_path.parents) > 3:
        candidates.append(module_path.parents[3] / "demo" / "conduent-datasets")
    candidates.extend((Path("/data/conduent-datasets"), Path("/app/conduent-datasets")))
    for candidate in candidates:
        if (candidate / "catalog.json").exists() and (candidate / "datasets").is_dir():
            return candidate.resolve()
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"Conduent corpus not found. Searched: {searched}")


class ConduentCorpus:
    def __init__(self, root: str | Path | None = None):
        self.root = discover_corpus_root(root)
        self.catalog = _read_json(self.root / "catalog.json")
        self.splits = self._load_splits()
        self.schemas = self._load_schemas()

    def dataset_names(self) -> list[str]:
        return sorted(path.name for path in (self.root / "datasets").iterdir() if (path / "manifest.jsonl").exists())

    def load_manifest(self, dataset: str) -> list[dict[str, Any]]:
        return _read_jsonl(self.root / "datasets" / dataset / "manifest.jsonl")

    def load_truth(self, dataset: str) -> dict[str, dict[str, Any]]:
        path = self.root / "datasets" / dataset / "truth" / "records.jsonl"
        if not path.exists():
            return {}
        return {record["truth_record_id"]: record for record in _read_jsonl(path)}

    def iter_cases(
        self,
        datasets: Iterable[str] | None = None,
        splits: set[str] | None = None,
        *,
        golden_only: bool = True,
        limit_per_dataset: int | None = None,
    ) -> list[CorpusCase]:
        selected = list(datasets or GOLDEN_DATASETS)
        cases: list[CorpusCase] = []
        for dataset in selected:
            truth_records = self.load_truth(dataset)
            count = 0
            for manifest in self.load_manifest(dataset):
                if golden_only and manifest.get("truth_status") != "golden":
                    continue
                split = self.split_for(manifest)
                if splits and split not in splits:
                    continue
                truth_record = truth_records.get(manifest.get("truth_record_id"))
                schema_id = str(manifest["schema_id"])
                document_path = self.resolve_path(manifest["document_path"])
                cases.append(
                    CorpusCase(
                        dataset=dataset,
                        document_id=str(manifest["document_id"]),
                        document_path=document_path,
                        original_filename=str(manifest["original_filename"]),
                        schema_id=schema_id,
                        schema=self.schemas[schema_id],
                        truth=(truth_record or {}).get("values"),
                        manifest=manifest,
                        split=split,
                    )
                )
                count += 1
                if limit_per_dataset is not None and count >= limit_per_dataset:
                    break
        return cases

    def split_for(self, manifest: dict[str, Any]) -> str:
        document_id = str(manifest["document_id"])
        return str((self.splits.get("documents") or {}).get(document_id) or manifest.get("split") or "unassigned")

    def resolve_path(self, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/")
        path = (self.root / normalized).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Corpus path escapes root: {relative_path}") from exc
        return path

    def corpus_hash(self) -> str:
        hasher = hashlib.sha256()
        included = [self.root / "catalog.json"]
        if (self.root / "evaluation" / "splits.json").exists():
            included.append(self.root / "evaluation" / "splits.json")
        included.extend(sorted((self.root / "schemas").glob("*.json")))
        for dataset in self.dataset_names():
            included.append(self.root / "datasets" / dataset / "manifest.jsonl")
            truth_path = self.root / "datasets" / dataset / "truth" / "records.jsonl"
            if truth_path.exists():
                included.append(truth_path)
        for path in included:
            hasher.update(path.relative_to(self.root).as_posix().encode("utf-8"))
            hasher.update(path.read_bytes())
        return hasher.hexdigest()

    def validate(self) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        dataset_reports: dict[str, Any] = {}
        all_document_ids: set[str] = set()
        assigned_document_ids = set((self.splits.get("documents") or {}).keys())
        allowed_splits = {"tuning", "calibration", "holdout", "development"}

        for dataset in self.dataset_names():
            manifests = self.load_manifest(dataset)
            truth_records = self.load_truth(dataset)
            dataset_errors: list[str] = []
            dataset_warnings: list[str] = []
            seen_ids: set[str] = set()
            split_counts: Counter[str] = Counter()
            golden = 0
            for manifest in manifests:
                document_id = str(manifest.get("document_id") or "")
                if not document_id:
                    dataset_errors.append("manifest record missing document_id")
                    continue
                if document_id in seen_ids or document_id in all_document_ids:
                    dataset_errors.append(f"duplicate document_id: {document_id}")
                seen_ids.add(document_id)
                all_document_ids.add(document_id)
                split = self.split_for(manifest)
                split_counts[split] += 1
                if document_id not in assigned_document_ids:
                    dataset_errors.append(f"missing frozen split assignment: {document_id}")
                if split not in allowed_splits:
                    dataset_errors.append(f"unsupported split '{split}': {document_id}")

                path = self.resolve_path(str(manifest.get("document_path") or ""))
                if not path.is_file():
                    dataset_errors.append(f"missing document: {path}")
                elif manifest.get("content_sha256") != _sha256_file(path):
                    dataset_errors.append(f"content hash mismatch: {document_id}")

                schema_id = str(manifest.get("schema_id") or "")
                if schema_id not in self.schemas:
                    dataset_errors.append(f"missing schema {schema_id}: {document_id}")

                if manifest.get("truth_status") == "golden":
                    golden += 1
                    truth_id = manifest.get("truth_record_id")
                    truth = truth_records.get(truth_id)
                    if not truth:
                        dataset_errors.append(f"missing truth {truth_id}: {document_id}")
                    elif truth.get("document_id") != document_id or truth.get("schema_id") != schema_id:
                        dataset_errors.append(f"truth linkage mismatch: {document_id}")

                if manifest.get("deidentification_status") == "unknown":
                    dataset_warnings.append(f"unknown deidentification status: {document_id}")

            dataset_reports[dataset] = {
                "documents": len(manifests),
                "golden_documents": golden,
                "split_counts": dict(sorted(split_counts.items())),
                "errors": dataset_errors,
                "warnings": dataset_warnings,
            }
            errors.extend(f"{dataset}: {message}" for message in dataset_errors)
            warnings.extend(f"{dataset}: {message}" for message in dataset_warnings)

        expected = (self.catalog.get("active_summary") or {}).get("document_count")
        actual = sum(report["documents"] for report in dataset_reports.values())
        if isinstance(expected, int) and expected != actual:
            warnings.append(
                f"catalog active document count is {expected}, but active dataset directories contain {actual}"
            )
        unknown_split_ids = assigned_document_ids - all_document_ids
        errors.extend(
            f"split assignment references unknown document: {document_id}" for document_id in unknown_split_ids
        )
        for dataset, expected_counts in (self.splits.get("expected_counts") or {}).items():
            actual_counts = (dataset_reports.get(dataset) or {}).get("split_counts")
            if actual_counts is not None and actual_counts != expected_counts:
                errors.append(f"{dataset}: split counts {actual_counts} do not match frozen policy {expected_counts}")

        return {
            "valid": not errors,
            "corpus_root": str(self.root),
            "corpus_hash": self.corpus_hash(),
            "documents": actual,
            "datasets": dataset_reports,
            "errors": errors,
            "warnings": warnings,
        }

    def _load_splits(self) -> dict[str, Any]:
        path = self.root / "evaluation" / "splits.json"
        return _read_json(path) if path.exists() else {"documents": {}}

    def _load_schemas(self) -> dict[str, dict[str, Any]]:
        schemas: dict[str, dict[str, Any]] = {}
        for path in sorted((self.root / "schemas").glob("*.json")):
            schema = _read_json(path)
            schema_id = schema.get("schema_id")
            if not schema_id:
                schema_id = Path(str(schema.get("$id") or path.stem)).stem
            schemas[str(schema_id)] = schema
        return schemas


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object at {path}:{line_number}")
        records.append(value)
    return records


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
