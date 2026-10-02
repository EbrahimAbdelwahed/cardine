"""Immutable, rebuildable document navigation; never canonical identity."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from ._validation import JsonObject, JsonValue, freeze_object, require_text
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

    @classmethod
    def from_json(cls, value: JsonObject) -> SourceLocator:
        _exact(
            value,
            {
                "kind",
                "start_page",
                "end_page",
                "start_line",
                "end_line",
                "start_offset",
                "end_offset",
            },
        )
        return cls(
            kind=LocatorKind(_string(value, "kind")),
            start_page=_optional_int(value, "start_page"),
            end_page=_optional_int(value, "end_page"),
            start_line=_optional_int(value, "start_line"),
            end_line=_optional_int(value, "end_line"),
            start_offset=_optional_int(value, "start_offset"),
            end_offset=_optional_int(value, "end_offset"),
        )


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

    @classmethod
    def from_json(cls, value: JsonObject) -> DocumentNode:
        _exact(
            value, {"node_key", "parent_key", "title", "summary", "locator", "order", "children"}
        )
        children = _sequence(value["children"])
        if any(not isinstance(child, str) for child in children):
            raise ValueError("child keys must be strings")
        order = value["order"]
        if type(order) is not int:
            raise ValueError("node order must be integer")
        return cls(
            node_key=_string(value, "node_key"),
            parent_key=_optional_string(value, "parent_key"),
            title=_optional_string(value, "title"),
            summary=_optional_string(value, "summary"),
            locator=SourceLocator.from_json(_object(value["locator"])),
            order=order,
            children=tuple(_as_string(child) for child in children),
        )


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

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "source_id": str(self.source_id),
                "revision_id": str(self.revision_id),
                "substrate_id": str(self.substrate_id),
                "index_version": self.index_version,
                "producer_id": self.producer_id,
                "producer_version": self.producer_version,
                "config_fingerprint": self.config_fingerprint,
                "nodes": tuple(node.to_json() for node in self.nodes),
                "fingerprint": self.fingerprint,
            }
        )

    @classmethod
    def from_json(cls, value: JsonObject) -> DocumentIndex:
        _exact(
            value,
            {
                "source_id",
                "revision_id",
                "substrate_id",
                "index_version",
                "producer_id",
                "producer_version",
                "config_fingerprint",
                "nodes",
                "fingerprint",
            },
        )
        fingerprint = _string(value, "fingerprint")
        require_text(fingerprint, "fingerprint")
        return cls(
            source_id=SourceId(_string(value, "source_id")),
            revision_id=RevisionId(_string(value, "revision_id")),
            substrate_id=SubstrateId(_string(value, "substrate_id")),
            index_version=_string(value, "index_version"),
            producer_id=_string(value, "producer_id"),
            producer_version=_string(value, "producer_version"),
            config_fingerprint=_string(value, "config_fingerprint"),
            nodes=tuple(
                DocumentNode.from_json(_object(node)) for node in _sequence(value["nodes"])
            ),
            fingerprint=fingerprint,
        )


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


def _exact(value: JsonObject, fields: set[str]) -> None:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("document index JSON fields mismatch")


def _object(value: JsonValue) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError("expected document index JSON object")
    return value


def _sequence(value: JsonValue) -> Sequence[JsonValue]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("expected document index JSON array")
    return value


def _as_string(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ValueError("expected document index JSON string")
    return value


def _string(value: JsonObject, key: str) -> str:
    return _as_string(value[key])


def _optional_string(value: JsonObject, key: str) -> str | None:
    return None if value[key] is None else _as_string(value[key])


def _optional_int(value: JsonObject, key: str) -> int | None:
    item = value[key]
    if item is not None and type(item) is not int:
        raise ValueError("locator offsets must be integers or null")
    return item
