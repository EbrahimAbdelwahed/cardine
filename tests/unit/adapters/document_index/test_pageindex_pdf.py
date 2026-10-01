from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from hashlib import sha256

import pytest

from cardine.adapters.document_index.pageindex import (
    PageIndexDocumentIndexAdapter,
    PageIndexProviderError,
)
from cardine.adapters.pageindex.worker import PageIndexWorkerError
from study_agent.domain._validation import JsonObject
from study_agent.domain.document_index import DocumentIndex
from study_agent.domain.substrate import PageMapEntry
from study_agent.knowledge.document_index import candidate_nodes, resolve_locator
from study_agent.ports.document_index import DocumentIndexRequest
from tests.unit.knowledge.test_document_index import context

TEXT = "# Café\nFirst\n# Heart\nSecond\n"
PDF = b"%PDF-1.7\nfixture\n%%EOF\n"
BOUNDARY = TEXT.index("# Heart")


def request() -> DocumentIndexRequest:
    binding = context(text=TEXT)
    return DocumentIndexRequest(
        binding.source.source_id,
        binding.source.revision_id,
        binding.substrate.substrate_id,
        "application/pdf",
        PDF,
        TEXT,
        {
            "original_sha256": sha256(PDF).hexdigest(),
            "page_count": 2,
            "page_map": ({"page": 1, "offset": 0}, {"page": 2, "offset": BOUNDARY}),
        },
    )


class Worker:
    calls = 0
    text = ""

    def run(self, markdown: str) -> list[object]:
        self.calls += 1
        self.text = markdown
        return [
            {"node_id": "a", "title": "Café", "line_num": 1, "text": "WRONG provider evidence"},
            {"node_id": "b", "title": "Heart", "line_num": 3, "text": "WRONG provider evidence"},
        ]


def test_pdf_indexes_only_bound_canonical_markdown_and_exact_spans() -> None:
    worker = Worker()
    adapter = PageIndexDocumentIndexAdapter(worker=worker)
    derived = adapter.build_sync(request())
    binding = context(
        text=TEXT, page_count=2, page_map=(PageMapEntry(0, 1), PageMapEntry(BOUNDARY, 2))
    )
    candidates = candidate_nodes(derived, binding)
    assert worker.text == TEXT
    assert worker.calls == 1
    assert [(c.span.start_offset, c.span.end_offset) for c in candidates] == [
        (0, BOUNDARY),
        (BOUNDARY, len(TEXT)),
    ]
    assert derived.config_fingerprint == adapter.configuration_fingerprint(request())
    assert all(node.summary is None for node in derived.nodes)
    restored = DocumentIndex.from_json(derived.to_json())
    assert restored == derived
    assert resolve_locator(restored.nodes[2].locator, binding).start_offset == BOUNDARY
    assert asyncio.run(adapter.build(request())) == derived


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {
            "original_sha256": "wrong",
            "page_count": 2,
            "page_map": ({"page": 1, "offset": 0}, {"page": 2, "offset": BOUNDARY}),
        },
        {
            "original_sha256": sha256(PDF).hexdigest(),
            "page_count": 3,
            "page_map": ({"page": 1, "offset": 0}, {"page": 3, "offset": BOUNDARY}),
        },
        {
            "original_sha256": sha256(PDF).hexdigest(),
            "page_count": 2,
            "page_map": ({"page": 1, "offset": 1}, {"page": 2, "offset": BOUNDARY}),
        },
        {
            "original_sha256": sha256(PDF).hexdigest(),
            "page_count": 2,
            "page_map": ({"page": 1, "offset": 0}, {"page": 2, "offset": len(TEXT)}),
        },
        {
            "original_sha256": sha256(PDF).hexdigest(),
            "page_count": 2,
            "page_map": ({"page": 1, "offset": 0}, {"page": 2, "offset": True}),
        },
    ],
)
def test_invalid_pdf_maps_and_binding_fail_before_worker(metadata: JsonObject) -> None:
    worker = Worker()
    with pytest.raises(PageIndexProviderError, match="pageindex_invalid_pdf_binding"):
        PageIndexDocumentIndexAdapter(worker=worker).build_sync(
            replace(request(), metadata=metadata)
        )
    assert worker.calls == 0


def test_pdf_original_bytes_and_substrate_tampering_are_rejected() -> None:
    worker = Worker()
    adapter = PageIndexDocumentIndexAdapter(worker=worker)
    with pytest.raises(PageIndexProviderError, match="pdf_binding"):
        adapter.build_sync(replace(request(), content=PDF + b"tamper"))
    forged = request()
    object.__setattr__(forged, "normalized_text", "tampered")
    with pytest.raises(PageIndexProviderError, match="source_binding"):
        adapter.build_sync(forged)
    assert worker.calls == 0


def test_pdf_input_provenance_participates_in_cache_configuration() -> None:
    adapter = PageIndexDocumentIndexAdapter(worker=Worker())
    original = request()
    altered = dict(original.metadata)
    altered["page_map"] = ({"page": 1, "offset": 0}, {"page": 2, "offset": BOUNDARY + 1})
    assert adapter.configuration_fingerprint(original) != adapter.configuration_fingerprint(
        replace(original, metadata=altered)
    )
    assert adapter.configuration_fingerprint(original) != adapter.configuration_fingerprint(
        replace(original, media_type="text/markdown", content=TEXT.encode())
    )


def test_pdf_timeout_is_explicit_and_preserves_canonical_input() -> None:
    class FailedWorker:
        def run(self, markdown: str) -> list[object]:
            raise PageIndexWorkerError("pageindex_timeout")

    original = request()
    with pytest.raises(PageIndexProviderError, match="pageindex_timeout"):
        asyncio.run(PageIndexDocumentIndexAdapter(worker=FailedWorker()).build(original))
    assert original.content == PDF and original.normalized_text == TEXT


def test_repeated_pdf_cancellation_waits_for_worker_cleanup() -> None:
    started, released, finished = threading.Event(), threading.Event(), threading.Event()

    class BoundedWorker:
        def run(self, markdown: str) -> list[object]:
            started.set()
            released.wait(timeout=0.5)
            finished.set()
            return []

    async def run() -> None:
        task = asyncio.create_task(
            PageIndexDocumentIndexAdapter(worker=BoundedWorker()).build(request())
        )
        while not started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        released.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(run())
