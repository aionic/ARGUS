"""Environment-driven entrypoint for the Container Apps evaluation job."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from evaluation.blob_io import upload_directory  # noqa: E402
from evaluation.cli import main  # noqa: E402


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
    else:
        raise ValueError(f"Unsupported evaluation stage: {stage}")
    return arguments


if __name__ == "__main__":
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
