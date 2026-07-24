from evaluation.job_entrypoint import build_arguments


def test_job_entrypoint_builds_holdout_guard_arguments(monkeypatch) -> None:
    monkeypatch.setenv("EVALUATION_STAGE", "Holdout")

    arguments = build_arguments()

    assert "--allow-holdout" in arguments
    assert "--resume-output" in arguments
    assert arguments[arguments.index("--splits") + 1] == "holdout"
