#!/usr/bin/env python
"""Backfill a dataset-based ``partitionKey`` on existing ARGUS documents.

Background
----------
The Cosmos ``documents`` container declares its partition key path as
``/partitionKey``. Historically ARGUS documents were written **without** that
field, so every document landed in the single "undefined" logical partition
(effectively an unpartitioned container). New documents now set
``partitionKey = <dataset>`` (see ``ai_ocr.process.initialize_document``), but a
document's partition key value is **immutable** — existing documents cannot be
repartitioned by an in-place update. This one-time, idempotent migration copies
each legacy document into its dataset partition and removes the legacy copy.

Safety
------
* Only documents **missing** ``partitionKey`` are touched. Configuration,
  profiling-report, and pricing-cache documents already set their own partition
  key and are skipped automatically.
* "Upsert new, then conditionally delete old" ordering avoids data loss: the
  document exists in the target partition before the legacy copy is removed, and
  an ETag guard prevents deleting a source document changed during migration.
* Run ``--dry-run`` first. Actual changes require ``--yes``.

Usage
-----
    # Preview only
    python scripts/migrate_cosmos_partition_key.py --dry-run

    # Apply (requires the same COSMOS_* env vars the backend uses, plus an
    # identity with Cosmos data-plane access reachable to the account)
    python scripts/migrate_cosmos_partition_key.py --yes

Environment: ``COSMOS_URL``, ``COSMOS_DB_NAME``, ``COSMOS_DOCUMENTS_CONTAINER_NAME``.
Because the Cosmos account uses a private endpoint, run this from inside the VNet
(e.g. an exec session in the backend container app) or over an approved route.
"""

from __future__ import annotations

import argparse
import os
import sys

from azure.core import MatchConditions
from azure.cosmos import CosmosClient, exceptions
from azure.identity import DefaultAzureCredential


def _dataset_for(doc: dict) -> str:
    """Derive the dataset (partition key value) for a legacy document."""
    dataset = doc.get("dataset") or (doc.get("properties") or {}).get("dataset")
    if dataset:
        return str(dataset)
    doc_id = doc.get("id", "")
    if "__" in doc_id:
        return doc_id.split("__", 1)[0]
    return "default-dataset"


def _strip_system_fields(doc: dict) -> dict:
    """Return a copy without Cosmos-managed system fields (``_rid``/``_etag``/…)."""
    return {key: value for key, value in doc.items() if not key.startswith("_")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="List what would change without modifying data.")
    parser.add_argument("--yes", action="store_true", help="Apply the migration (required to make changes).")
    args = parser.parse_args()

    try:
        url = os.environ["COSMOS_URL"]
        db_name = os.environ["COSMOS_DB_NAME"]
        container_name = os.environ["COSMOS_DOCUMENTS_CONTAINER_NAME"]
    except KeyError as missing:
        print(f"Missing required environment variable: {missing}", file=sys.stderr)
        return 2

    client = CosmosClient(url, DefaultAzureCredential())
    container = client.get_database_client(db_name).get_container_client(container_name)

    # Only extraction documents lack partitionKey; everything else sets its own.
    all_legacy = list(
        container.query_items(
            query="SELECT * FROM c WHERE NOT IS_DEFINED(c.partitionKey)",
            enable_cross_partition_query=True,
        )
    )
    # Only repartition extraction documents (id "dataset__filename"). Cache records
    # such as pricing entries ("aoai-price:...") use a different partition-key
    # convention and must be left untouched.
    legacy = [doc for doc in all_legacy if "__" in (doc.get("id") or "")]
    skipped = [doc.get("id") for doc in all_legacy if "__" not in (doc.get("id") or "")]
    print(
        f"Found {len(all_legacy)} record(s) without a partitionKey: "
        f"{len(legacy)} document(s) to migrate, {len(skipped)} non-document record(s) skipped."
    )
    for skipped_id in skipped:
        print(f"  skip (non-document): {skipped_id}")

    if not legacy:
        return 0

    if args.dry_run or not args.yes:
        for doc in legacy:
            print(f"  would migrate {doc.get('id')} -> partitionKey={_dataset_for(doc)}")
        if not args.dry_run:
            print("\nNo changes made. Re-run with --yes to apply (or --dry-run to silence this notice).")
            return 2
        return 0

    migrated = 0
    failed = 0
    for doc in legacy:
        doc_id = doc.get("id")
        dataset = _dataset_for(doc)
        body = _strip_system_fields(doc)
        body["partitionKey"] = dataset
        try:
            # Upsert the latest source snapshot so a prior interrupted attempt is
            # repaired before the legacy copy is removed.
            container.upsert_item(body=body)
            # Only delete the source if it still matches the snapshot we copied.
            # Concurrent updates leave the source intact for a later retry.
            container.delete_item(
                item=doc_id,
                partition_key={},
                etag=doc.get("_etag"),
                match_condition=MatchConditions.IfNotModified,
            )
            migrated += 1
            print(f"  migrated {doc_id} -> {dataset}")
        except exceptions.CosmosAccessConditionFailedError:
            failed += 1
            print(f"  RETRY {doc_id}: source changed during migration", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failed += 1
            print(f"  FAILED {doc_id}: {exc}", file=sys.stderr)

    print(f"\nDone. migrated={migrated} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
