"""Environment-driven entrypoint for the Container Apps evaluation job."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from evaluation.blob_io import download_prefix, upload_directory  # noqa: E402
from evaluation.cli import main  # noqa: E402

EXPORT_ARTIFACTS = (
    "report.json",
    "summary.csv",
    "fields.csv",
    "report.md",
    "config-lock.json",
    "run-manifest.json",
)
EXPORT_CHUNK_SIZE = 6000


def print_report() -> int:
    required = [
        "BLOB_ACCOUNT_URL",
        "EVALUATION_BLOB_CONTAINER",
        "EVALUATION_OUTPUT_BLOB_PREFIX",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError(f"Report stage is missing settings: {', '.join(missing)}")
    output_dir = Path(os.getenv("EVALUATION_OUTPUT_DIR", "/tmp/argus-evaluation"))
    download_prefix(
        os.environ["BLOB_ACCOUNT_URL"],
        os.environ["EVALUATION_BLOB_CONTAINER"],
        os.environ["EVALUATION_OUTPUT_BLOB_PREFIX"],
        output_dir,
    )
    for name in ("report.md", "summary.csv"):
        path = output_dir / name
        if not path.exists():
            raise FileNotFoundError(f"Evaluation report artifact not found: {path}")
        print(f"--- {name} ---")
        print(path.read_text(encoding="utf-8"))
    return 0


def export_results() -> int:
    required = [
        "BLOB_ACCOUNT_URL",
        "EVALUATION_BLOB_CONTAINER",
        "EVALUATION_OUTPUT_BLOB_PREFIX",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError(f"Export stage is missing settings: {', '.join(missing)}")
    output_dir = Path(os.getenv("EVALUATION_OUTPUT_DIR", "/tmp/argus-evaluation"))
    download_prefix(
        os.environ["BLOB_ACCOUNT_URL"],
        os.environ["EVALUATION_BLOB_CONTAINER"],
        os.environ["EVALUATION_OUTPUT_BLOB_PREFIX"],
        output_dir,
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name in EXPORT_ARTIFACTS:
            path = output_dir / name
            if not path.exists():
                raise FileNotFoundError(f"Evaluation export artifact not found: {path}")
            bundle.write(path, arcname=name)
    payload = archive.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    encoded = base64.b64encode(payload).decode("ascii")
    chunks = [encoded[index : index + EXPORT_CHUNK_SIZE] for index in range(0, len(encoded), EXPORT_CHUNK_SIZE)]
    print(
        "ARGUS_EXPORT_START "
        + json.dumps(
            {
                "sha256": digest,
                "bytes": len(payload),
                "chunks": len(chunks),
                "artifacts": list(EXPORT_ARTIFACTS),
            },
            separators=(",", ":"),
        )
    )
    for index, chunk in enumerate(chunks, start=1):
        print(f"ARGUS_EXPORT_CHUNK {index:06d}/{len(chunks):06d} {chunk}")
    print(f"ARGUS_EXPORT_END {digest}")
    return 0


def build_arguments() -> list[str]:
    stage = os.getenv("EVALUATION_STAGE", "Pilot").lower()
    arguments = [
        "evaluation.cli",
        "--datasets",
        os.getenv(
            "EVALUATION_DATASETS",
            "cms1500,commercial-documents,enrollments,invoice-demo",
        ),
        "--variants",
        os.getenv(
            "EVALUATION_VARIANTS",
            "baseline-current,semantic-guided,selective-confidence,full-confidence,selective-confidence-preprocessed",
        ),
        "--output-dir",
        os.getenv("EVALUATION_OUTPUT_DIR", "/tmp/argus-evaluation"),
    ]
    if stage == "pilot":
        arguments.extend(
            [
                "--splits",
                "tuning,calibration",
                "--limit",
                os.getenv("EVALUATION_PILOT_LIMIT", "1"),
            ]
        )
    elif stage == "tune":
        arguments.extend(["--splits", "tuning,calibration"])
    elif stage == "freeze":
        arguments.extend(["--prepare-only", "--freeze-config"])
    elif stage == "holdout":
        arguments.extend(["--splits", "holdout", "--allow-holdout", "--resume-output"])
    elif stage == "score":
        arguments.extend(
            [
                "--mode",
                "score",
                "--splits",
                "holdout",
                "--allow-holdout",
                "--resume-output",
            ]
        )
    elif stage == "finalize":
        arguments.extend(
            [
                "--mode",
                "score",
                "--splits",
                "tuning,calibration,holdout",
                "--allow-holdout",
                "--resume-output",
            ]
        )
    else:
        raise ValueError(f"Unsupported evaluation stage: {stage}")
    return arguments


if __name__ == "__main__":
    selected_stage = os.getenv("EVALUATION_STAGE", "Pilot").lower()
    if selected_stage == "report":
        raise SystemExit(print_report())
    if selected_stage == "export":
        raise SystemExit(export_results())
    corpus_root = os.getenv("CONDUENT_CORPUS_ROOT")
    if (
        corpus_root
        and os.getenv("EVALUATION_PUBLISH_CORPUS", "false").lower() in {"1", "true", "yes"}
        and os.getenv("BLOB_ACCOUNT_URL")
        and os.getenv("EVALUATION_BLOB_CONTAINER")
        and os.getenv("EVALUATION_CORPUS_BLOB_PREFIX")
    ):
        upload_directory(
            os.environ["BLOB_ACCOUNT_URL"],
            os.environ["EVALUATION_BLOB_CONTAINER"],
            os.environ["EVALUATION_CORPUS_BLOB_PREFIX"],
            Path(corpus_root),
        )
    sys.argv = build_arguments()
    raise SystemExit(main())
