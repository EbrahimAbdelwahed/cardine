"""The existing qualified PageIndex worker feeds one provider-neutral tree."""

from __future__ import annotations

import asyncio
from hashlib import sha256

import pytest

from cardine.adapters.document_index.pageindex import (
    PageIndexDocumentIndexAdapter,
    PageIndexProviderError,
)
from cardine.adapters.pageindex.worker import PageIndexWorkerError
from study_agent.domain.document_index import LocatorKind
from study_agent.domain.identifiers import RevisionId, SourceId, SubstrateId
from study_agent.ports.document_index import DocumentIndexRequest


def request(
    text: str = "Preamble\n# First\nBody\n## Child\nMore\n# Second\nEnd",
) -> DocumentIndexRequest:
    return DocumentIndexRequest(
        SourceId("source:fixture"),
        RevisionId("revision:fixture"),
        SubstrateId("substrate:sha256:" + sha256(text.encode()).hexdigest()),
        "text/markdown",
        text.encode(),
        text,
    )


class Worker:
    def __init__(self, tree: list[object]) -> None:
        self.tree = tree
        self.calls = 0

    def run(self, markdown: str) -> list[object]:
        self.calls += 1
        return self.tree


def tree() -> list[object]:
    return [
        {
            "node_id": "a",
            "title": "First",
            "line_num": 2,
            "text": "untrusted",
            "nodes": [
                {"node_id": "c", "title": "Child", "line_num": 4, "text": "untrusted"},
            ],
        },
        {"node_id": "b", "title": "Second", "line_num": 6, "text": "untrusted"},
    ]


def test_normalizes_forest_and_nested_navigation_with_full_root() -> None:
    worker = Worker(tree())
    adapter = PageIndexDocumentIndexAdapter(worker=worker)
    first = asyncio.run(adapter.build(request()))
    second = asyncio.run(adapter.build(request()))
    root, a, c, b = first.nodes
    assert root.children == ("a", "b")
    assert root.locator.kind is LocatorKind.TEXT_SPAN
    assert root.locator.start_offset == 0
    assert root.locator.end_offset == len(request().normalized_text or "")
    assert a.children == ("c",)
    assert c.parent_key == "a"
    assert (a.locator.start_line, a.locator.end_line) == (2, 5)
    assert (c.locator.start_line, c.locator.end_line) == (4, 5)
    assert (b.locator.start_line, b.locator.end_line) == (6, 7)
    assert first.fingerprint == second.fingerprint
    assert first.substrate_id == request().substrate_id
    assert all(node.summary is None for node in first.nodes)
    assert worker.calls == 2


@pytest.mark.parametrize(
    "bad",
    [
        [{"node_id": "a", "title": "A", "line_num": 99}],
        [{"node_id": "a", "title": "A", "line_num": 0}],
        [{"node_id": "a", "title": "A", "line_num": True}],
        [
            {
                "node_id": "a",
                "title": "A",
                "line_num": 2,
                "nodes": [{"node_id": "a", "title": "Duplicate", "line_num": 3}],
            }
        ],
        [{"node_id": "document-root", "title": "A", "line_num": 2}],
        [
            {"node_id": "a", "title": "A", "line_num": 5},
            {"node_id": "b", "title": "B", "line_num": 2},
        ],
        [{"node_id": "a", "title": "A", "line_num": 2, "nodes": "bad"}],
        [
            {
                "node_id": "a",
                "title": "A",
                "line_num": 3,
                "nodes": [{"node_id": "b", "title": "B", "line_num": 2}],
            }
        ],
    ],
)
def test_rejects_invalid_provider_structure(bad: list[object]) -> None:
    with pytest.raises(PageIndexProviderError, match="pageindex_malformed_response"):
        asyncio.run(PageIndexDocumentIndexAdapter(worker=Worker(bad)).build(request()))


def test_heading_free_source_gets_covering_root_without_legacy_parser() -> None:
    index = asyncio.run(PageIndexDocumentIndexAdapter(worker=Worker([])).build(request("plain")))
    assert len(index.nodes) == 1
    assert index.nodes[0].locator.end_offset == 5


def test_provider_failure_retains_request_and_does_not_retry() -> None:
    class FailedWorker:
        calls = 0

        def run(self, markdown: str) -> list[object]:
            self.calls += 1
            raise PageIndexWorkerError("private-source-or-key")

    worker = FailedWorker()
    original = request()
    with pytest.raises(PageIndexProviderError) as caught:
        asyncio.run(PageIndexDocumentIndexAdapter(worker=worker).build(original))
    assert str(caught.value) == "pageindex_failure"
    assert worker.calls == 1
    assert original.content


def test_real_qualified_worker_produces_navigation_index() -> None:
    index = asyncio.run(PageIndexDocumentIndexAdapter().build(request()))
    assert len(index.nodes) == 4
    assert [node.title for node in index.nodes] == [None, "First", "Child", "Second"]


def test_cancellation_waits_for_bounded_worker_cleanup() -> None:
    import threading

    released = threading.Event()
    started = threading.Event()
    finished = threading.Event()

    class BoundedWorker:
        def run(self, markdown: str) -> list[object]:
            started.set()
            released.wait(timeout=0.2)
            finished.set()
            return []

    async def run() -> None:
        task = asyncio.create_task(
            PageIndexDocumentIndexAdapter(worker=BoundedWorker()).build(request())
        )
        while not started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        released.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(run())


def test_unsupported_format_fails_explicitly() -> None:
    from dataclasses import replace

    worker = Worker([])
    pdf = replace(request(), media_type="application/pdf")
    with pytest.raises(PageIndexProviderError, match="pageindex_unsupported_media_type"):
        asyncio.run(PageIndexDocumentIndexAdapter(worker=worker).build(pdf))
    assert worker.calls == 0
