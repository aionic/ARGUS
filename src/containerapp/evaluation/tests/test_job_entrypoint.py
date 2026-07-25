from pathlib import Path

from evaluation.job_entrypoint import build_arguments, print_report


def test_job_entrypoint_builds_holdout_guard_arguments(monkeypatch) -> None:
    monkeypatch.setenv("EVALUATION_STAGE", "Holdout")

    arguments = build_arguments()

    assert "--allow-holdout" in arguments
    assert "--resume-output" in arguments
    assert arguments[arguments.index("--splits") + 1] == "holdout"


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
