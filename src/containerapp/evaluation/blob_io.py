"""Managed-identity transfer of evaluation corpus and result artifacts."""

from __future__ import annotations

from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient


def download_prefix(
    account_url: str,
    container_name: str,
    prefix: str,
    destination: Path,
) -> Path:
    container = BlobServiceClient(
        account_url=account_url,
        credential=DefaultAzureCredential(),
    ).get_container_client(container_name)
    normalized_prefix = prefix.strip("/") + "/"
    downloaded = 0
    for blob in container.list_blobs(name_starts_with=normalized_prefix):
        relative = blob.name[len(normalized_prefix) :]
        if not relative:
            continue
        target = (destination / relative).resolve()
        try:
            target.relative_to(destination.resolve())
        except ValueError as exc:
            raise ValueError(f"Blob path escapes corpus destination: {blob.name}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            stream = container.download_blob(blob.name)
            stream.readinto(handle)
        downloaded += 1
    if not downloaded:
        raise FileNotFoundError(f"No corpus blobs found in {container_name}/{normalized_prefix}")
    return destination


def upload_directory(
    account_url: str,
    container_name: str,
    prefix: str,
    source: Path,
) -> None:
    container = BlobServiceClient(
        account_url=account_url,
        credential=DefaultAzureCredential(),
    ).get_container_client(container_name)
    normalized_prefix = prefix.strip("/")
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        relative = path.relative_to(source).as_posix()
        blob_name = f"{normalized_prefix}/{relative}" if normalized_prefix else relative
        with path.open("rb") as handle:
            container.upload_blob(blob_name, handle, overwrite=True)
