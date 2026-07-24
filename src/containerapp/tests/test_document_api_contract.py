"""Contract tests for document cost and confidence API projections."""

import asyncio
import json

import api_routes
import mcp_server


class _PageIterator:
    def __init__(self, items: list[dict], continuation_token: str | None) -> None:
        self._items = items
        self._returned = False
        self.continuation_token = continuation_token

    def __iter__(self):
        return self

    def __next__(self):
        if self._returned:
            raise StopIteration
        self._returned = True
        return self._items


class _PagedItems:
    def __init__(self, items: list[dict], continuation_token: str | None) -> None:
        self._items = items
        self._continuation_token = continuation_token

    def by_page(self, continuation_token: str | None = None) -> _PageIterator:
        return _PageIterator(self._items, self._continuation_token)


class _FakeContainer:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.calls: list[dict] = []

    def query_items(self, **kwargs):
        self.calls.append(kwargs)
        query = kwargs["query"]
        if query.startswith("SELECT VALUE COUNT"):
            return [len(self.items)]
        if query.startswith("SELECT *"):
            return self.items
        return _PagedItems(self.items[:1], "next-page")


class _FakeMcpContainer:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.calls: list[dict] = []

    def query_items(self, **kwargs):
        self.calls.append(kwargs)
        return self.items


def _document() -> dict:
    return {
        "id": "legacy__invoice.pdf",
        "dataset": "legacy",
        "properties": {
            "content_understanding_chunks": [{"ocr_markdown": "large payload"}],
            "ocr_confidence": {
                "n_words": 10,
                "mean": 0.91,
                "frac_low": 0.1,
                "min": 0.55,
                "word_min": 0.6,
                "per_chunk": [{"n_words": 10}],
            }
        },
        "state": {"processing_completed": True},
        "extracted_data": {"gpt_extraction_output": {"invoice": "123"}},
        "ocr_text": "recognized text",
        "evaluation": {"score": 1},
        "summary": "complete",
    }


def test_list_documents_preserves_legacy_detail_contract(monkeypatch) -> None:
    container = _FakeContainer([_document()])
    monkeypatch.setattr(api_routes, "get_data_container", lambda: container)

    result = asyncio.run(api_routes.list_documents(dataset="legacy"))

    document = result["documents"][0]
    assert result == {"documents": [document], "count": 1, "continuation": None}
    assert document["ocr_text"] == "recognized text"
    assert document["gpt_extraction"] == {"invoice": "123"}
    assert document["evaluation"] == {"score": 1}
    assert document["summary"] == "complete"
    assert document["extracted_data"]["gpt_extraction_output"] == {"invoice": "123"}
    assert container.calls[0]["enable_cross_partition_query"] is True
    assert "partition_key" not in container.calls[0]


def test_lightweight_list_keeps_shape_total_count_and_legacy_partitions(monkeypatch) -> None:
    container = _FakeContainer([_document(), {**_document(), "id": "legacy__second.pdf"}])
    monkeypatch.setattr(api_routes, "get_data_container", lambda: container)

    result = asyncio.run(api_routes.list_documents(dataset="legacy", limit=1, lightweight=True))

    document = result["documents"][0]
    assert result["count"] == 2
    assert result["continuation"] == "next-page"
    assert document["ocr_text"] is None
    assert document["gpt_extraction"] is None
    assert document["evaluation"] is None
    assert document["summary"] is None
    assert document["extracted_data"] == {}
    assert "content_understanding_chunks" not in document["properties"]
    assert container.calls[0]["enable_cross_partition_query"] is True
    assert "partition_key" not in container.calls[0]
    assert "c.properties," not in container.calls[0]["query"]
    assert "ORDER BY c._ts DESC" in container.calls[0]["query"]


def test_ocr_confidence_uses_persisted_summary() -> None:
    summary = api_routes._summarize_ocr_confidence(_document()["properties"])

    assert summary == {
        "n_words": 10,
        "mean": 0.91,
        "frac_low": 0.1,
        "min": 0.55,
        "word_min": 0.6,
    }


def test_mcp_dataset_listing_includes_legacy_partitions(monkeypatch) -> None:
    container = _FakeContainer([_document()])
    monkeypatch.setattr(api_routes, "get_data_container", lambda: container)
    monkeypatch.setattr(api_routes, "get_conf_container", lambda: None)
    monkeypatch.setattr(api_routes, "get_blob_service_client", lambda: None)

    result = asyncio.run(api_routes._execute_mcp_tool("argus_list_documents", {"dataset": "legacy"}))

    assert result["count"] == 1
    assert container.calls[0]["enable_cross_partition_query"] is True
    assert "partition_key" not in container.calls[0]


def test_mounted_mcp_list_and_search_include_legacy_partitions(monkeypatch) -> None:
    document = {**_document(), "filename": "invoice.pdf"}
    container = _FakeMcpContainer([document])
    monkeypatch.setattr(mcp_server, "get_data_container", lambda: container)

    listed = asyncio.run(mcp_server._handle_list_documents({"dataset": "legacy"}))
    searched = asyncio.run(mcp_server._handle_search_documents({"dataset": "legacy", "query": "invoice"}))

    assert json.loads(listed[0].text)["total_count"] == 1
    assert json.loads(searched[0].text)["total_matches"] == 1
    assert all(call["enable_cross_partition_query"] is True for call in container.calls)
    assert all("partition_key" not in call for call in container.calls)
