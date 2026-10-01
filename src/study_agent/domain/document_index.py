"""Immutable, rebuildable document navigation; never canonical identity."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from ._validation import JsonObject, require_text
from .identifiers import RevisionId, SourceId, SubstrateId


class LocatorKind(StrEnum):
    PDF_PAGE_RANGE = "pdf_page_range"
    MARKDOWN_LINE_RANGE = "markdown_line_range"
    TEXT_SPAN = "text_span"


@dataclass(frozen=True, slots=True)
class SourceLocator:
    kind: LocatorKind
    start_page: int | None = None
    end_page: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None

    def __post_init__(self) -> None:
        fields = {
            LocatorKind.PDF_PAGE_RANGE: ("start_page", "end_page", 1),
            LocatorKind.MARKDOWN_LINE_RANGE: ("start_line", "end_line", 1),
            LocatorKind.TEXT_SPAN: ("start_offset", "end_offset", 0),
        }
        if not isinstance(self.kind, LocatorKind):
            raise ValueError("locator kind must be LocatorKind")
        first, last, minimum = fields[self.kind]
        for name in (
            "start_page",
            "end_page",
            "start_line",
            "end_line",
            "start_offset",
            "end_offset",
        ):
            value = getattr(self, name)
            if name in (first, last):
                if type(value) is not int or value < minimum:
                    raise ValueError(f"{name} must be an integer >= {minimum}")
            elif value is not None:
                raise ValueError(f"{name} does not belong to this locator kind")
        start, end = getattr(self, first), getattr(self, last)
        if end < start or (self.kind is LocatorKind.TEXT_SPAN and end == start):
            raise ValueError("locator must describe a nonempty forward range")

    def to_json(self) -> JsonObject:
        return {
            "kind": self.kind.value,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
        }


@dataclass(frozen=True, slots=True)
class DocumentNode:
    node_key: str
    parent_key: str | None
    title: str | None
    summary: str | None
    locator: SourceLocator
    order: int
    children: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.node_key, "node_key")
        for name in ("parent_key", "title", "summary"):
            if getattr(self, name) is not None:
                require_text(getattr(self, name), name)
        if not isinstance(self.locator, SourceLocator):
            raise ValueError("node locator must be SourceLocator")
        self.locator.__post_init__()
        if type(self.order) is not int or self.order < 0:
            raise ValueError("node order must be a non-negative integer")
        children = tuple(self.children)
        for child in children:
            require_text(child, "child key")
        if len(set(children)) != len(children):
            raise ValueError("child keys must be unique")
        object.__setattr__(self, "children", children)

    def to_json(self) -> JsonObject:
        return {
            "node_key": self.node_key,
            "parent_key": self.parent_key,
            "title": self.title,
            "summary": self.summary,
            "locator": self.locator.to_json(),
            "order": self.order,
            "children": self.children,
        }


@dataclass(frozen=True, slots=True)
class DocumentIndex:
    source_id: SourceId
    revision_id: RevisionId
    substrate_id: SubstrateId
    index_version: str
    producer_id: str
    producer_version: str
    config_fingerprint: str
    nodes: tuple[DocumentNode, ...]
    fingerprint: str = ""

    def __post_init__(self) -> None:
        for name, cls in (
            ("source_id", SourceId),
            ("revision_id", RevisionId),
            ("substrate_id", SubstrateId),
        ):
            if not isinstance(getattr(self, name), cls):
                raise ValueError(f"{name} must be {cls.__name__}")
        for name in ("index_version", "producer_id", "producer_version", "config_fingerprint"):
            require_text(getattr(self, name), name)
        nodes = tuple(self.nodes)
        if not nodes or any(not isinstance(n, DocumentNode) for n in nodes):
            raise ValueError("index requires document nodes")
        by_key = {n.node_key: n for n in nodes}
        if len(by_key) != len(nodes):
            raise ValueError("node keys must be unique")
        if [n.order for n in nodes] != sorted({n.order for n in nodes}):
            raise ValueError("nodes must have unique, ascending deterministic order")
        roots = [n for n in nodes if n.parent_key is None]
        if len(roots) != 1:
            raise ValueError("index must have one root")
        for node in nodes:
            node.__post_init__()
            if node.parent_key is not None and node.parent_key not in by_key:
                raise ValueError("every parent must exist")
            expected = tuple(n.node_key for n in nodes if n.parent_key == node.node_key)
            if node.children != expected:
                raise ValueError("parent/child references or child order disagree")
        visited: set[str] = set()
        pending = [roots[0].node_key]
        while pending:
            key = pending.pop()
            if key in visited:
                raise ValueError("index contains a cycle")
            visited.add(key)
            pending.extend(by_key[key].children)
        if len(visited) != len(nodes):
            raise ValueError("index must be a connected acyclic tree")
        object.__setattr__(self, "nodes", nodes)
        computed = document_index_fingerprint(self)
        if self.fingerprint and self.fingerprint != computed:
            raise ValueError("document index fingerprint mismatch")
        object.__setattr__(self, "fingerprint", computed)


def document_index_fingerprint(index: DocumentIndex) -> str:
    """Includes all source binding, producer/config, locator and navigation fields."""
    payload = {
        "source_id": str(index.source_id),
        "revision_id": str(index.revision_id),
        "substrate_id": str(index.substrate_id),
        "index_version": index.index_version,
        "producer_id": index.producer_id,
        "producer_version": index.producer_version,
        "config_fingerprint": index.config_fingerprint,
        "nodes": [n.to_json() for n in index.nodes],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return sha256(b"document-index@1\0" + encoded).hexdigest()
