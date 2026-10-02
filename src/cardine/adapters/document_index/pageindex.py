"""Adapt Cardine's qualified PageIndex worker to the shared derived index.

The bundled upstream structural subset is the single implementation. This
adapter translates navigation metadata only; summaries and provider text never
become source evidence. No SDK, cloud upload, or second semantic parser is used.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import suppress
from hashlib import sha256
from typing import Protocol

from cardine.adapters.pageindex.worker import (
    QUALIFIED_UPSTREAM_COMMIT,
    PageIndexWorker,
    PageIndexWorkerError,
)
from study_agent.domain._validation import JsonObject
from study_agent.domain.document_index import (
    DocumentIndex,
    DocumentNode,
    LocatorKind,
    SourceLocator,
)
from study_agent.domain.substrate import PageMapEntry, validate_page_map
from study_agent.ports.document_index import DocumentIndexRequest
from study_agent.state.serialization import canonical_json_bytes


class StructuralWorker(Protocol):
    def run(self, markdown: str) -> list[object]: ...


class PageIndexProviderError(RuntimeError):
    """Sanitized indexing failure; callers retain canonical source material."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PageIndexDocumentIndexAdapter:
    """Index canonical Markdown, including the admitted substrate of a PDF.

    The qualified worker owns a killable subprocess timeout. We await its
    bounded completion even when cancelled, so retries cannot overlap orphaned
    executor work. Unsupported source formats fail explicitly for the host to
    handle; they do not invoke a legacy semantic parser.
    """

    def __init__(self, *, worker: StructuralWorker | None = None) -> None:
        self._worker = worker or PageIndexWorker()
        self._config_fingerprint = sha256(
            json.dumps(
                {
                    "implementation": QUALIFIED_UPSTREAM_COMMIT,
                    "projection": "document-index-v1-nodes-1024",
                    "input_policy": "canonical-source-binding-v2",
                    "timeout_seconds": getattr(self._worker, "timeout_seconds", 3.0),
                    "max_input_bytes": getattr(self._worker, "max_input_bytes", 2 * 1024 * 1024),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    @property
    def config_fingerprint(self) -> str:
        return self._config_fingerprint

    async def build(self, request: DocumentIndexRequest) -> DocumentIndex:
        task = asyncio.create_task(asyncio.to_thread(self.build_sync, request))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Repeated cancellation must not abandon a running worker. Its own
            # subprocess deadline guarantees bounded cleanup before propagation.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            with suppress(Exception):
                task.result()
            raise

    def configuration_fingerprint(self, request: DocumentIndexRequest) -> str:
        """Expected complete configuration identity without running PageIndex."""
        if request.media_type not in {
            "text/markdown",
            "text/x-markdown",
            "text/plain",
            "application/pdf",
        }:
            raise PageIndexProviderError("pageindex_unsupported_media_type")
        try:
            request.__post_init__()
        except (TypeError, ValueError):
            raise PageIndexProviderError("pageindex_invalid_source_binding") from None
        text = request.normalized_text
        if not text:
            raise PageIndexProviderError("pageindex_normalized_text_required")
        provenance = _input_provenance(request, text)
        return sha256(
            canonical_json_bytes({"adapter": self._config_fingerprint, "input": provenance})
        ).hexdigest()

    def build_sync(self, request: DocumentIndexRequest) -> DocumentIndex:
        """The same sole structural pipeline for synchronous product coordination."""
        config_fingerprint = self.configuration_fingerprint(request)
        text = request.normalized_text
        assert text is not None
        try:
            tree = self._worker.run(text)
        except PageIndexWorkerError as error:
            code = "pageindex_timeout" if error.code == "pageindex_timeout" else "pageindex_failure"
            raise PageIndexProviderError(code) from None
        except Exception:
            raise PageIndexProviderError("pageindex_failure") from None
        try:
            nodes = _normalize_tree(tree, text)
            return DocumentIndex(
                source_id=request.source_id,
                revision_id=request.revision_id,
                substrate_id=request.substrate_id,
                index_version="document-index-v1",
                producer_id="pageindex-qualified-structural",
                producer_version=QUALIFIED_UPSTREAM_COMMIT,
                config_fingerprint=config_fingerprint,
                nodes=nodes,
            )
        except (TypeError, ValueError):
            raise PageIndexProviderError("pageindex_malformed_response") from None


def _input_provenance(request: DocumentIndexRequest, text: str) -> JsonObject:
    if request.media_type != "application/pdf":
        return {"strategy": "canonical-normalized-text", "media_type": request.media_type}
    # The host supplies admission-owned page boundaries and immutable PDF bytes.
    # Index only the verified normalized substrate; never extract provider text.
    try:
        digest = sha256(request.content).hexdigest()
        if not request.content.startswith(b"%PDF-"):
            raise ValueError("PDF signature")
        if request.metadata.get("original_sha256") != digest:
            raise ValueError("PDF digest")
        page_count = request.metadata.get("page_count")
        raw_map = request.metadata.get("page_map")
        if type(page_count) is not int or not isinstance(raw_map, tuple):
            raise ValueError("page map")
        entries: list[PageMapEntry] = []
        for entry in raw_map:
            if not isinstance(entry, Mapping) or set(entry) != {"page", "offset"}:
                raise ValueError("page entry")
            page, offset = entry["page"], entry["offset"]
            if type(page) is not int or type(offset) is not int:
                raise ValueError("page boundary")
            entries.append(PageMapEntry(offset, page))
        validate_page_map(page_count, entries, len(text))
        if len(entries) != page_count or any(
            entry.page != ordinal for ordinal, entry in enumerate(entries, start=1)
        ):
            raise ValueError("incomplete map")
        return {
            "strategy": "canonical-normalized-markdown-from-pdf",
            "media_type": "application/pdf",
            "original_sha256": digest,
            "substrate_id": str(request.substrate_id),
            "page_count": page_count,
            "page_map": tuple(entry.to_json() for entry in entries),
        }
    except (TypeError, ValueError):
        raise PageIndexProviderError("pageindex_invalid_pdf_binding") from None


def _normalize_tree(tree: list[object], text: str) -> tuple[DocumentNode, ...]:
    # The synthetic root covers preamble and heading-free documents and gives
    # provider forests one deterministic root without inventing evidence.
    root_key = "document-root"
    # A final newline terminates the final line; it is not an extra source line.
    line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
    seen: set[str] = {root_key}
    nodes: list[DocumentNode] = []

    def normalize_siblings(
        raw_nodes: list[object], parent_key: str, lower: int, upper: int, depth: int
    ) -> tuple[str, ...]:
        if depth > 32:
            raise ValueError("depth")
        starts: list[int] = []
        keys: list[str] = []
        records: list[Mapping[str, object]] = []
        for raw in raw_nodes:
            if not isinstance(raw, Mapping):
                raise ValueError("node")
            key, start = raw.get("node_id"), raw.get("line_num")
            if type(key) is not str or not key or key != key.strip() or key in seen:
                raise ValueError("node key")
            if type(start) is not int or not lower <= start <= upper:
                raise ValueError("line range")
            if starts and start <= starts[-1]:
                raise ValueError("sibling order")
            title = raw.get("title")
            if type(title) is not str or not title.strip():
                raise ValueError("title")
            seen.add(key)
            if len(seen) > 1025:
                raise ValueError("node bound")
            starts.append(start)
            keys.append(key)
            records.append(raw)
        for position, raw in enumerate(records):
            start = starts[position]
            end = starts[position + 1] - 1 if position + 1 < len(starts) else upper
            children_raw = raw.get("nodes", [])
            if not isinstance(children_raw, list):
                raise ValueError("children")
            node_position = len(nodes)
            nodes.append(
                DocumentNode(
                    node_key=keys[position],
                    parent_key=parent_key,
                    title=str(raw["title"]).strip(),
                    summary=None,
                    locator=SourceLocator(
                        kind=LocatorKind.MARKDOWN_LINE_RANGE, start_line=start, end_line=end
                    ),
                    order=node_position + 1,
                    children=(),
                )
            )
            child_keys = normalize_siblings(children_raw, keys[position], start, end, depth + 1)
            previous = nodes[node_position]
            nodes[node_position] = DocumentNode(
                node_key=previous.node_key,
                parent_key=previous.parent_key,
                title=previous.title,
                summary=None,
                locator=previous.locator,
                order=previous.order,
                children=child_keys,
            )
        return tuple(keys)

    children = normalize_siblings(tree, root_key, 1, line_count, 1)
    root = DocumentNode(
        node_key=root_key,
        parent_key=None,
        title=None,
        summary=None,
        locator=SourceLocator(kind=LocatorKind.TEXT_SPAN, start_offset=0, end_offset=len(text)),
        order=0,
        children=children,
    )
    return (root, *nodes)
