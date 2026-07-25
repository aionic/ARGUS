import json
from pathlib import Path

import pytest

from evaluation.cli import _enforce_holdout_lock, _load_result, _result_path
from evaluation.corpus import CorpusCase


def _case(tmp_path: Path) -> CorpusCase:
    document = tmp_path / "sample.tif"
    document.write_bytes(b"sample")
    return CorpusCase(
        dataset="cms1500",
        document_id="cms1500-test",
        document_path=document,
        original_filename="sample.tif",
        schema_id="cms1500-v1",
        schema={},
        truth={},
        manifest={"content_sha256": "abc"},
        split="tuning",
    )


def test_result_cache_path_changes_with_configuration(tmp_path: Path) -> None:
    case = _case(tmp_path)
    first = {
        "vendor": "azure-content-understanding",
        "variant": "guided",
        "run_configuration_hash": "a" * 64,
    }
    second = {**first, "run_configuration_hash": "b" * 64}

    assert _result_path(tmp_path, case, first) != _result_path(tmp_path, case, second)


def test_cached_result_accepts_run_identity_sample_key(tmp_path: Path) -> None:
    case = _case(tmp_path)
    configuration = {"run_configuration_hash": "a" * 64}
    cache_path = tmp_path / "result.json"
    cache_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "sample": case.document_id,
                    "document_sha256": case.manifest["content_sha256"],
                    "run_configuration_hash": configuration["run_configuration_hash"],
                    "corpus_hash": "corpus",
                }
            }
        ),
        encoding="utf-8",
    )

    assert _load_result(cache_path, case, configuration, "corpus")["metadata"]["sample"] == case.document_id


def test_holdout_requires_matching_frozen_lock(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _enforce_holdout_lock(tmp_path, {"holdout"}, True, "expected")

    (tmp_path / "config-lock.json").write_text(
        json.dumps({"configuration_set_hash": "different"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        _enforce_holdout_lock(tmp_path, {"holdout"}, True, "expected")

    (tmp_path / "config-lock.json").write_text(
        json.dumps({"configuration_set_hash": "expected"}),
        encoding="utf-8",
    )
    _enforce_holdout_lock(tmp_path, {"holdout"}, True, "expected")
