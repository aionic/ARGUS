from evaluation.corpus import GOLDEN_DATASETS, ConduentCorpus


def test_conduent_corpus_integrity() -> None:
    corpus = ConduentCorpus()
    report = corpus.validate()

    assert report["valid"] is True
    assert report["documents"] == 38
    assert sum(report["datasets"][name]["golden_documents"] for name in GOLDEN_DATASETS) == 34


def test_conduent_corpus_loads_golden_cases() -> None:
    corpus = ConduentCorpus()
    cases = corpus.iter_cases(GOLDEN_DATASETS)

    assert len(cases) == 34
    assert all(case.document_path.is_file() for case in cases)
    assert all(case.truth is not None for case in cases)
