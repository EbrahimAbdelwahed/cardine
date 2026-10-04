"""Strict private inputs for grounded flashcard generation.

The scope fingerprint commits to the prepared values returned by a trusted
adapter.  It deliberately does not claim that those values completely describe
the external source collection; completeness remains a port obligation.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, cast

from study_agent.domain import BlobId, ChunkId, Citation, RevisionId, SourceId
from study_agent.domain._validation import (
    JsonObject,
    JsonValue,
    freeze_json,
    freeze_object,
    require_text,
)
from study_agent.grounding import EvidenceEnvelope

MAX_FLASHCARD_SCOPE_ENTRIES = 256
MAX_FLASHCARD_SCOPE_EVIDENCE_ITEMS = 24
MAX_CANONICAL_LOCATOR_CHARACTERS = 16_000

_SCOPE_FINGERPRINT_DOMAIN = b"prepared-flashcard-scope@1\0"
_PORTABLE_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class FlashcardScopeIndexEntry:
    """Structural source metadata; headings are not generated summaries."""

    topic_key: str
    heading: str
    locator: str
    relative_position: int
    character_count: int
    evidence_handles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _bounded_text(self.topic_key, "topic_key", 128)
        _reject_receipt_shaped_handle(self.topic_key, "topic_key")
        _bounded_text(self.heading, "heading", 1000)
        _bounded_text(self.locator, "locator", MAX_CANONICAL_LOCATOR_CHARACTERS)
        if type(self.relative_position) is not int or self.relative_position < 0:
            raise ValueError("relative_position must be a non-negative integer")
        if (
            type(self.character_count) is not int
            or self.character_count < 1
            or self.character_count > 100_000_000
        ):
            raise ValueError("character_count must be an integer between 1 and 100000000")
        handles = tuple(self.evidence_handles)
        if len(handles) > MAX_FLASHCARD_SCOPE_EVIDENCE_ITEMS:
            raise ValueError("evidence_handles exceeds 24 items")
        for handle in handles:
            _bounded_text(handle, "evidence handle", 256)
            _reject_receipt_shaped_handle(handle, "evidence handle")
        if len(set(handles)) != len(handles):
            raise ValueError("evidence_handles must be unique and ordered")
        object.__setattr__(self, "evidence_handles", handles)

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "topic_key": self.topic_key,
                "heading": self.heading,
                "locator": self.locator,
                "relative_position": self.relative_position,
                "character_count": self.character_count,
                "evidence_handles": self.evidence_handles,
            }
        )

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> FlashcardScopeIndexEntry:
        _exact(
            value,
            {
                "topic_key",
                "heading",
                "locator",
                "relative_position",
                "character_count",
                "evidence_handles",
            },
            "flashcard scope index entry",
        )
        return cls(
            topic_key=_string(value, "topic_key"),
            heading=_string(value, "heading"),
            locator=_string(value, "locator"),
            relative_position=_integer(value, "relative_position"),
            character_count=_integer(value, "character_count"),
            evidence_handles=_strings(value, "evidence_handles"),
        )


@dataclass(frozen=True, slots=True)
class PreparedFlashcardScope:
    index: tuple[FlashcardScopeIndexEntry, ...]
    evidence: EvidenceEnvelope
    scope_fingerprint: str

    def __post_init__(self) -> None:
        entries = tuple(self.index)
        if not 1 <= len(entries) <= MAX_FLASHCARD_SCOPE_ENTRIES:
            raise ValueError("flashcard scope index must contain 1..256 entries")
        if not all(isinstance(entry, FlashcardScopeIndexEntry) for entry in entries):
            raise TypeError("flashcard scope index must contain FlashcardScopeIndexEntry values")
        if tuple(entry.relative_position for entry in entries) != tuple(range(len(entries))):
            raise ValueError("flashcard scope positions must be contiguous and canonical")
        topic_keys = tuple(entry.topic_key for entry in entries)
        if len(set(topic_keys)) != len(topic_keys):
            raise ValueError("flashcard scope topic keys must be unique")
        if not isinstance(self.evidence, EvidenceEnvelope):
            raise TypeError("flashcard scope evidence must be an EvidenceEnvelope")
        if len(self.evidence.items) > MAX_FLASHCARD_SCOPE_EVIDENCE_ITEMS:
            raise ValueError("flashcard scope evidence exceeds 24 active items")
        active_handles = {item.handle for item in self.evidence.items}
        linked_handles = {
            handle for entry in entries for handle in entry.evidence_handles
        }
        if not linked_handles.issubset(active_handles):
            raise ValueError("scope index links an evidence handle outside its envelope")
        _require_sha256(self.scope_fingerprint, "scope_fingerprint")
        expected = _scope_fingerprint(entries, self.evidence)
        if self.scope_fingerprint != expected:
            raise ValueError("scope_fingerprint does not match the prepared scope")
        object.__setattr__(self, "index", entries)

    @classmethod
    def prepare(
        cls,
        index: tuple[FlashcardScopeIndexEntry, ...],
        evidence: EvidenceEnvelope,
    ) -> PreparedFlashcardScope:
        entries = tuple(index)
        return cls(entries, evidence, _scope_fingerprint(entries, evidence))

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "index": tuple(entry.to_json() for entry in self.index),
                "evidence": self.evidence.to_json(),
                "scope_fingerprint": self.scope_fingerprint,
            }
        )

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> PreparedFlashcardScope:
        _exact(value, {"index", "evidence", "scope_fingerprint"}, "prepared flashcard scope")
        return cls(
            index=tuple(
                FlashcardScopeIndexEntry.from_json(_mapping(item, "scope index entry"))
                for item in _array(value, "index")
            ),
            evidence=EvidenceEnvelope.from_json(value["evidence"]),
            scope_fingerprint=_string(value, "scope_fingerprint"),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> PreparedFlashcardScope:
        decoded: Any = json.loads(data)
        frozen = freeze_json(cast(JsonValue, decoded))
        if not isinstance(frozen, Mapping):
            raise ValueError("prepared flashcard scope must be a JSON object")
        scope = cls.from_json(frozen)
        if scope.to_bytes() != data:
            raise ValueError("prepared flashcard scope bytes are not canonical")
        return scope


@dataclass(frozen=True, slots=True)
class VerifiedMediaEvidence:
    """Trusted media evidence before proposal-batch commitment ordering exists."""

    handle: str
    evidence_handle: str
    blob_id: BlobId
    sha256: str
    citation: Citation
    verifier_id: str
    verifier_version: str
    verifier_fingerprint: str
    alt_text: str

    def __post_init__(self) -> None:
        _bounded_text(self.handle, "media handle", 256)
        _reject_receipt_shaped_handle(self.handle, "media handle")
        _reject_filename(self.handle, "media handle")
        _bounded_text(self.evidence_handle, "media evidence handle", 256)
        _reject_receipt_shaped_handle(self.evidence_handle, "media evidence handle")
        if not isinstance(self.blob_id, BlobId):
            raise TypeError("media blob_id must be a BlobId")
        _reject_filename(str(self.blob_id), "media blob_id")
        _require_sha256(self.sha256, "media sha256")
        if str(self.blob_id) != f"sha256:{self.sha256}":
            raise ValueError("media blob_id must match its sha256")
        if not isinstance(self.citation, Citation):
            raise TypeError("media citation must be a Citation")
        _portable_identifier(self.verifier_id, "verifier_id")
        _bounded_text(self.verifier_version, "verifier_version", 128)
        if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", self.verifier_version) is None:
            raise ValueError("verifier_version must be portable")
        _require_sha256(self.verifier_fingerprint, "verifier_fingerprint")
        _bounded_text(self.alt_text, "alt_text", 2000)
        if "<" in self.alt_text or ">" in self.alt_text:
            raise ValueError("alt_text cannot contain HTML")

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "handle": self.handle,
                "evidence_handle": self.evidence_handle,
                "blob_id": str(self.blob_id),
                "sha256": self.sha256,
                "citation": _citation_json(self.citation),
                "verifier_id": self.verifier_id,
                "verifier_version": self.verifier_version,
                "verifier_fingerprint": self.verifier_fingerprint,
                "alt_text": self.alt_text,
            }
        )

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> VerifiedMediaEvidence:
        _exact(
            value,
            {
                "handle",
                "evidence_handle",
                "blob_id",
                "sha256",
                "citation",
                "verifier_id",
                "verifier_version",
                "verifier_fingerprint",
                "alt_text",
            },
            "verified media evidence",
        )
        citation = _mapping(value["citation"], "media citation")
        _exact(
            citation,
            {
                "source_id",
                "revision_id",
                "chunk_id",
                "start_offset",
                "end_offset",
                "locator",
                "quoted_snippet",
            },
            "media citation",
        )
        quoted = citation["quoted_snippet"]
        if quoted is not None and not isinstance(quoted, str):
            raise ValueError("quoted_snippet must be a string or null")
        return cls(
            handle=_string(value, "handle"),
            evidence_handle=_string(value, "evidence_handle"),
            blob_id=BlobId(_string(value, "blob_id")),
            sha256=_string(value, "sha256"),
            citation=Citation(
                SourceId(_string(citation, "source_id")),
                RevisionId(_string(citation, "revision_id")),
                ChunkId(_string(citation, "chunk_id")),
                _integer(citation, "start_offset"),
                _integer(citation, "end_offset"),
                _string(citation, "locator"),
                quoted,
            ),
            verifier_id=_string(value, "verifier_id"),
            verifier_version=_string(value, "verifier_version"),
            verifier_fingerprint=_string(value, "verifier_fingerprint"),
            alt_text=_string(value, "alt_text"),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> VerifiedMediaEvidence:
        decoded: Any = json.loads(data)
        frozen = freeze_json(cast(JsonValue, decoded))
        if not isinstance(frozen, Mapping):
            raise ValueError("verified media evidence must be a JSON object")
        evidence = cls.from_json(frozen)
        if evidence.to_bytes() != data:
            raise ValueError("verified media evidence bytes are not canonical")
        return evidence


def _scope_fingerprint(
    index: tuple[FlashcardScopeIndexEntry, ...], evidence: EvidenceEnvelope
) -> str:
    payload = freeze_object(
        {
            "index": tuple(entry.to_json() for entry in index),
            "evidence": evidence.to_json(),
        }
    )
    return sha256(_SCOPE_FINGERPRINT_DOMAIN + _canonical_bytes(payload)).hexdigest()


def _citation_json(citation: Citation) -> JsonObject:
    return freeze_object(
        {
            "source_id": str(citation.source_id),
            "revision_id": str(citation.revision_id),
            "chunk_id": str(citation.chunk_id),
            "start_offset": citation.start_offset,
            "end_offset": citation.end_offset,
            "locator": citation.locator,
            "quoted_snippet": citation.quoted_snippet,
        }
    )


def _canonical_bytes(value: JsonObject) -> bytes:
    def plain(item: JsonValue) -> object:
        if isinstance(item, Mapping):
            return {key: plain(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [plain(child) for child in item]
        return item

    return json.dumps(
        plain(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _exact(value: Mapping[str, JsonValue], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{name} fields must be exactly {sorted(fields)}")


def _mapping(value: JsonValue, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: Mapping[str, JsonValue], key: str) -> tuple[JsonValue, ...]:
    items = value.get(key)
    if not isinstance(items, tuple):
        raise ValueError(f"{key} must be an array")
    return items


def _string(value: Mapping[str, JsonValue], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string")
    return item


def _integer(value: Mapping[str, JsonValue], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise ValueError(f"{key} must be an integer")
    return item


def _strings(value: Mapping[str, JsonValue], key: str) -> tuple[str, ...]:
    items = _array(value, key)
    if any(not isinstance(item, str) for item in items):
        raise ValueError(f"{key} must contain only strings")
    return cast(tuple[str, ...], items)


def _bounded_text(value: str, name: str, maximum: int) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    require_text(value, name)
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")


def _require_sha256(value: str, name: str) -> None:
    _bounded_text(value, name, 64)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 fingerprint")


def _portable_identifier(value: str, name: str) -> None:
    _bounded_text(value, name, 128)
    if _PORTABLE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a portable lowercase identifier")


def _reject_filename(value: str, name: str) -> None:
    if "." in value or "/" in value or "\\" in value:
        raise ValueError(f"{name} must be opaque, not a filename or path")


def _reject_receipt_shaped_handle(value: str, name: str) -> None:
    lowered = value.lower()
    canonical_names = ("source", "revision", "chunk", "blob", "digest", "verifier")
    prefixes = tuple(
        f"{prefix}{separator}"
        for prefix in canonical_names
        for separator in (":", "-", "_", ".", "/", "\\")
    )
    if (
        lowered in canonical_names
        or lowered.startswith(prefixes)
        or lowered.startswith("sha256:")
        or "-sha256:" in lowered
        or (len(lowered) == 64 and all(char in "0123456789abcdef" for char in lowered))
    ):
        raise ValueError(f"{name} must be opaque, not a canonical receipt")


__all__ = [
    "MAX_FLASHCARD_SCOPE_ENTRIES",
    "MAX_FLASHCARD_SCOPE_EVIDENCE_ITEMS",
    "FlashcardScopeIndexEntry",
    "PreparedFlashcardScope",
    "VerifiedMediaEvidence",
]
