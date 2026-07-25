import base64
import io
import json
import zipfile
from pathlib import Path

from evaluation.job_entrypoint import EXPORT_ARTIFACTS, build_arguments, export_results, print_report


def test_job_entrypoint_builds_holdout_guard_arguments(monkeypatch) -> None:
    monkeypatch.setenv("EVALUATION_STAGE", "Holdout")

    arguments = build_arguments()

    assert "--allow-holdout" in arguments
    assert "--resume-output" in arguments
    assert arguments[arguments.index("--splits") + 1] == "holdout"


def test_finalize_entrypoint_scores_all_frozen_splits(monkeypatch) -> None:
    monkeypatch.setenv("EVALUATION_STAGE", "Finalize")

    arguments = build_arguments()

    assert arguments[arguments.index("--mode") + 1] == "score"
    assert arguments[arguments.index("--splits") + 1] == "tuning,calibration,holdout"
    assert "--allow-holdout" in arguments
    assert "--resume-output" in arguments


def test_print_report_downloads_and_emits_artifacts(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("BLOB_ACCOUNT_URL", "https://example.blob.core.windows.net/")
    monkeypatch.setenv("EVALUATION_BLOB_CONTAINER", "datasets")
    monkeypatch.setenv("EVALUATION_OUTPUT_BLOB_PREFIX", "evaluation/results/run")
    monkeypatch.setenv("EVALUATION_OUTPUT_DIR", str(tmp_path))

    def fake_download(*_args) -> Path:
        (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
        (tmp_path / "summary.csv").write_text("dataset,accuracy\ncms,0.5\n", encoding="utf-8")
        return tmp_path

    monkeypatch.setattr("evaluation.job_entrypoint.download_prefix", fake_download)

    assert print_report() == 0
    output = capsys.readouterr().out
    assert "--- report.md ---" in output
    assert "cms,0.5" in output


def test_export_results_emits_reconstructable_archive(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("BLOB_ACCOUNT_URL", "https://example.blob.core.windows.net/")
    monkeypatch.setenv("EVALUATION_BLOB_CONTAINER", "datasets")
    monkeypatch.setenv("EVALUATION_OUTPUT_BLOB_PREFIX", "evaluation/results/run")
    monkeypatch.setenv("EVALUATION_OUTPUT_DIR", str(tmp_path))

    def fake_download(*_args) -> Path:
        for name in EXPORT_ARTIFACTS:
            (tmp_path / name).write_text(f"{name}\n", encoding="utf-8")
        return tmp_path

    monkeypatch.setattr("evaluation.job_entrypoint.download_prefix", fake_download)

    assert export_results() == 0
    lines = capsys.readouterr().out.splitlines()
    metadata = json.loads(lines[0].removeprefix("ARGUS_EXPORT_START "))
    encoded = "".join(line.split(" ", 2)[2] for line in lines if line.startswith("ARGUS_EXPORT_CHUNK "))
    archive = base64.b64decode(encoded)

    assert metadata["bytes"] == len(archive)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert set(bundle.namelist()) == set(EXPORT_ARTIFACTS)
