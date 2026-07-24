"""Vendor-neutral evaluation adapter contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ai_ocr.azure.content_understanding import (
    describe_cu_analyzer,
    get_cu_extraction,
    prepare_cu_analyzer,
)


@dataclass(frozen=True)
class AdapterConfiguration:
    vendor: str
    configuration_id: str
    configuration_hash: str
    definition: dict[str, Any]


class EvaluationAdapter(Protocol):
    name: str

    def describe(
        self,
        dataset: str,
        example_schema: dict[str, Any],
        options: dict[str, Any],
    ) -> AdapterConfiguration: ...

    def prepare(
        self,
        dataset: str,
        example_schema: dict[str, Any],
        options: dict[str, Any],
    ) -> str: ...

    def run(
        self,
        document_path: Path,
        dataset: str,
        example_schema: dict[str, Any],
        options: dict[str, Any],
    ) -> dict[str, Any]: ...


class AzureContentUnderstandingAdapter:
    name = "azure-content-understanding"

    def describe(
        self,
        dataset: str,
        example_schema: dict[str, Any],
        options: dict[str, Any],
    ) -> AdapterConfiguration:
        description = describe_cu_analyzer(example_schema, dataset, analyzer_options=options)
        return AdapterConfiguration(
            vendor=self.name,
            configuration_id=description["analyzer_id"],
            configuration_hash=description["configuration_hash"],
            definition=description["definition"],
        )

    def prepare(
        self,
        dataset: str,
        example_schema: dict[str, Any],
        options: dict[str, Any],
    ) -> str:
        return prepare_cu_analyzer(example_schema, dataset, analyzer_options=options)

    def run(
        self,
        document_path: Path,
        dataset: str,
        example_schema: dict[str, Any],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        return get_cu_extraction(
            str(document_path),
            example_schema,
            dataset,
            analyzer_options=options,
        )


def get_adapter(name: str) -> EvaluationAdapter:
    if name == AzureContentUnderstandingAdapter.name:
        return AzureContentUnderstandingAdapter()
    raise ValueError(f"Unsupported evaluation vendor: {name}")
