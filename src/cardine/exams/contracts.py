"""Exact, provider-neutral contracts for grounded exam-sample analysis."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from itertools import pairwise
from typing import Any, cast

from study_agent.domain import ChunkId, CourseId, RevisionId, RunId, SourceId
from study_agent.domain._validation import JsonObject, JsonValue, freeze_json, freeze_object
from study_agent.grounding import EvidenceEnvelope
from study_agent.state import canonical_json_bytes

MAX_EXAM_SAMPLES = 16
MAX_EXAM_EVIDENCE_ITEMS = 64
MAX_EXAM_EVIDENCE_PER_SAMPLE = 8
MAX_EXAM_EVIDENCE_TEXT_BYTES = 64 * 1024

_SCOPE_DOMAIN = b"prepared-exam-sample-scope@1\0"
_PROJECTION_DOMAIN = b"exam-prompt-evidence-projection@1\0"
_REQUEST_DOMAIN = b"exam-analysis-request@1\0"
_PORTABLE_KEY = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class ExamAnalysisRequest:
    sample_revision_ids: tuple[RevisionId, ...]
    language: str

    def __post_init__(self) -> None:
        revisions = tuple(self.sample_revision_ids)
        if not 1 <= len(revisions) <= MAX_EXAM_SAMPLES:
            raise ValueError("sample_revision_ids must contain 1..16 revisions")
        if not all(isinstance(item, RevisionId) for item in revisions):
            raise TypeError("sample_revision_ids must contain RevisionId values")
        if len(set(revisions)) != len(revisions):
            raise ValueError("sample_revision_ids must be ordered and unique")
        _text(self.language, "language", 64)
        object.__setattr__(self, "sample_revision_ids", revisions)

    @property
    def fingerprint(self) -> str:
        return sha256(_REQUEST_DOMAIN + self.to_bytes()).hexdigest()

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "sample_revision_ids": tuple(str(item) for item in self.sample_revision_ids),
                "language": self.language,
            }
        )

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> ExamAnalysisRequest:
        _exact(value, {"sample_revision_ids", "language"}, "exam analysis request")
        return cls(
            tuple(RevisionId(item) for item in _strings(value, "sample_revision_ids")),
            _string(value, "language"),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ExamAnalysisRequest:
        request = cls.from_json(_decode(data, "exam analysis request"))
        if request.to_bytes() != data:
            raise ValueError("exam analysis request bytes are not canonical")
        return request


@dataclass(frozen=True, slots=True)
class PreparedExamSample:
    sample_key: str
    course_id: CourseId
    source_id: SourceId
    revision_id: RevisionId
    source_role: str
    is_current_revision: bool
    normalized_character_length: int
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _opaque(self.sample_key, "sample_key")
        if not isinstance(self.course_id, CourseId):
            raise TypeError("course_id must be CourseId")
        if not isinstance(self.source_id, SourceId):
            raise TypeError("source_id must be SourceId")
        if not isinstance(self.revision_id, RevisionId):
            raise TypeError("revision_id must be RevisionId")
        if self.source_role != "exam_sample":
            raise ValueError("exam sample source role must equal exam_sample")
        if self.is_current_revision is not True:
            raise ValueError("exam samples must be current source revisions")
        if (
            type(self.normalized_character_length) is not int
            or self.normalized_character_length < 1
        ):
            raise ValueError("normalized_character_length must be positive")
        handles = tuple(self.evidence_ids)
        if not 1 <= len(handles) <= MAX_EXAM_EVIDENCE_PER_SAMPLE:
            raise ValueError("each exam sample must bind 1..8 evidence items")
        for handle in handles:
            _opaque(handle, "evidence_id")
        if len(set(handles)) != len(handles):
            raise ValueError("sample evidence_ids must be ordered and unique")
        object.__setattr__(self, "evidence_ids", handles)

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "sample_key": self.sample_key,
                "course_id": str(self.course_id),
                "source_id": str(self.source_id),
                "revision_id": str(self.revision_id),
                "source_role": self.source_role,
                "is_current_revision": self.is_current_revision,
                "normalized_character_length": self.normalized_character_length,
                "evidence_ids": self.evidence_ids,
            }
        )

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> PreparedExamSample:
        _exact(
            value,
            {
                "sample_key",
                "course_id",
                "source_id",
                "revision_id",
                "source_role",
                "is_current_revision",
                "normalized_character_length",
                "evidence_ids",
            },
            "prepared exam sample",
        )
        current = value["is_current_revision"]
        if type(current) is not bool:
            raise ValueError("is_current_revision must be boolean")
        return cls(
            _string(value, "sample_key"),
            CourseId(_string(value, "course_id")),
            SourceId(_string(value, "source_id")),
            RevisionId(_string(value, "revision_id")),
            _string(value, "source_role"),
            current,
            _integer(value, "normalized_character_length"),
            _strings(value, "evidence_ids"),
        )


@dataclass(frozen=True, slots=True)
class PreparedExamSampleScope:
    samples: tuple[PreparedExamSample, ...]
    evidence: EvidenceEnvelope
    scope_fingerprint: str

    def __post_init__(self) -> None:
        samples = tuple(self.samples)
        if not 1 <= len(samples) <= MAX_EXAM_SAMPLES:
            raise ValueError("prepared exam scope must contain 1..16 samples")
        if not all(isinstance(item, PreparedExamSample) for item in samples):
            raise TypeError("prepared exam scope contains an invalid sample")
        keys = tuple(item.sample_key for item in samples)
        revisions = tuple(item.revision_id for item in samples)
        sources = tuple(item.source_id for item in samples)
        if len(set(keys)) != len(keys) or len(set(revisions)) != len(revisions):
            raise ValueError("sample keys and revisions must be ordered and unique")
        if len(set(sources)) != len(sources):
            raise ValueError("each selected exam sample must belong to one distinct source")
        if len({item.course_id for item in samples}) != 1:
            raise ValueError("all exam samples must belong to one course")
        if not isinstance(self.evidence, EvidenceEnvelope):
            raise TypeError("evidence must be an EvidenceEnvelope")
        if len(self.evidence.items) > MAX_EXAM_EVIDENCE_ITEMS:
            raise ValueError("exam scope evidence exceeds 64 items")
        handles = tuple(item.handle for item in self.evidence.items)
        bound = tuple(handle for sample in samples for handle in sample.evidence_ids)
        if len(bound) != len(set(bound)) or set(bound) != set(handles):
            raise ValueError("every evidence handle must belong to exactly one sample")
        if len(handles) != len(set(handles)):
            raise ValueError("exam scope evidence handles must be unique")
        if (
            sum(len(item.evidence.text.encode("utf-8")) for item in self.evidence.items)
            > MAX_EXAM_EVIDENCE_TEXT_BYTES
        ):
            raise ValueError("exam scope quoted evidence exceeds 64 KiB")
        by_handle = {item.handle: item.evidence for item in self.evidence.items}
        for sample in samples:
            spans = tuple(by_handle[handle] for handle in sample.evidence_ids)
            if any(
                item.citation.source_id != sample.source_id
                or item.citation.revision_id != sample.revision_id
                for item in spans
            ):
                raise ValueError("sample evidence belongs to another source revision")
            offsets = tuple(
                (item.citation.start_offset, item.citation.end_offset) for item in spans
            )
            if offsets[0][0] != 0 or offsets[-1][1] != sample.normalized_character_length:
                raise ValueError("sample evidence must cover the complete normalized source")
            if any(left[1] != right[0] for left, right in pairwise(offsets)):
                raise ValueError("sample evidence spans must be ordered, adjacent, and complete")
            chunk_ids = tuple(item.chunk.chunk_id for item in spans)
            if len(set(chunk_ids)) != len(chunk_ids):
                raise ValueError("sample evidence cannot repeat chunks")
        _sha(self.scope_fingerprint, "scope_fingerprint")
        if self.scope_fingerprint != _scope_fingerprint(samples, self.evidence):
            raise ValueError("scope_fingerprint does not match prepared exam scope")
        object.__setattr__(self, "samples", samples)

    @classmethod
    def prepare(
        cls, samples: tuple[PreparedExamSample, ...], evidence: EvidenceEnvelope
    ) -> PreparedExamSampleScope:
        frozen = tuple(samples)
        return cls(frozen, evidence, _scope_fingerprint(frozen, evidence))

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "samples": tuple(item.to_json() for item in self.samples),
                "evidence": self.evidence.to_json(),
                "scope_fingerprint": self.scope_fingerprint,
            }
        )

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> PreparedExamSampleScope:
        _exact(value, {"samples", "evidence", "scope_fingerprint"}, "prepared exam scope")
        return cls(
            tuple(
                PreparedExamSample.from_json(_mapping(item, "prepared exam sample"))
                for item in _array(value, "samples")
            ),
            EvidenceEnvelope.from_json(value["evidence"]),
            _string(value, "scope_fingerprint"),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> PreparedExamSampleScope:
        scope = cls.from_json(_decode(data, "prepared exam scope"))
        if scope.to_bytes() != data:
            raise ValueError("prepared exam scope bytes are not canonical")
        return scope


@dataclass(frozen=True, slots=True)
class ExamPromptEvidenceItem:
    sample_key: str
    evidence_id: str
    locator: str
    text: str

    def __post_init__(self) -> None:
        _opaque(self.sample_key, "sample_key")
        _opaque(self.evidence_id, "evidence_id")
        _text(self.locator, "locator", 2000)
        _text(self.text, "evidence text", MAX_EXAM_EVIDENCE_TEXT_BYTES)

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "sample_key": self.sample_key,
                "evidence_id": self.evidence_id,
                "locator": self.locator,
                "text": self.text,
            }
        )

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> ExamPromptEvidenceItem:
        _exact(value, {"sample_key", "evidence_id", "locator", "text"}, "exam prompt evidence")
        return cls(
            *(_string(value, key) for key in ("sample_key", "evidence_id", "locator", "text"))
        )


@dataclass(frozen=True, slots=True)
class ExamPromptEvidenceProjection:
    items: tuple[ExamPromptEvidenceItem, ...]
    scope_fingerprint: str
    projection_fingerprint: str

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if not 1 <= len(items) <= MAX_EXAM_EVIDENCE_ITEMS:
            raise ValueError("exam prompt projection must contain 1..64 evidence items")
        pairs = tuple((item.sample_key, item.evidence_id) for item in items)
        if len(set(pairs)) != len(pairs):
            raise ValueError("exam prompt evidence mapping must be ordered and unique")
        _sha(self.scope_fingerprint, "scope_fingerprint")
        _sha(self.projection_fingerprint, "projection_fingerprint")
        if self.projection_fingerprint != _projection_fingerprint(items, self.scope_fingerprint):
            raise ValueError("projection_fingerprint does not match prompt projection")
        object.__setattr__(self, "items", items)

    @classmethod
    def from_scope(cls, scope: PreparedExamSampleScope) -> ExamPromptEvidenceProjection:
        by_handle = {item.handle: item.evidence for item in scope.evidence.items}
        items = tuple(
            ExamPromptEvidenceItem(
                sample.sample_key,
                handle,
                by_handle[handle].citation.locator,
                by_handle[handle].text,
            )
            for sample in scope.samples
            for handle in sample.evidence_ids
        )
        return cls(
            items, scope.scope_fingerprint, _projection_fingerprint(items, scope.scope_fingerprint)
        )

    def verify_scope(self, scope: PreparedExamSampleScope) -> None:
        expected = ExamPromptEvidenceProjection.from_scope(scope)
        if self.to_bytes() != expected.to_bytes():
            raise ValueError("prompt projection does not resolve byte-identically to scope")

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "items": tuple(item.to_json() for item in self.items),
                "scope_fingerprint": self.scope_fingerprint,
                "projection_fingerprint": self.projection_fingerprint,
            }
        )

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> ExamPromptEvidenceProjection:
        _exact(
            value,
            {"items", "scope_fingerprint", "projection_fingerprint"},
            "exam prompt projection",
        )
        return cls(
            tuple(
                ExamPromptEvidenceItem.from_json(_mapping(item, "prompt evidence"))
                for item in _array(value, "items")
            ),
            _string(value, "scope_fingerprint"),
            _string(value, "projection_fingerprint"),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ExamPromptEvidenceProjection:
        projection = cls.from_json(_decode(data, "exam prompt projection"))
        if projection.to_bytes() != data:
            raise ValueError("exam prompt projection bytes are not canonical")
        return projection


@dataclass(frozen=True, slots=True)
class ExamObservation:
    value: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.value, "observation value", 1000)
        ids = tuple(self.evidence_ids)
        if not 1 <= len(ids) <= 16:
            raise ValueError("observation evidence_ids must contain 1..16 handles")
        for item in ids:
            _opaque(item, "observation evidence_id")
        if len(set(ids)) != len(ids):
            raise ValueError("observation evidence_ids must be ordered and unique")
        object.__setattr__(self, "evidence_ids", ids)

    def to_json(self) -> JsonObject:
        return freeze_object({"value": self.value, "evidence_ids": self.evidence_ids})

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> ExamObservation:
        _exact(value, {"value", "evidence_ids"}, "exam observation")
        return cls(_string(value, "value"), _strings(value, "evidence_ids"))


@dataclass(frozen=True, slots=True)
class ExamAnalysisProposal:
    sample_size: int
    observed_topics: tuple[ExamObservation, ...]
    observed_formats: tuple[ExamObservation, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.sample_size) is not int or not 1 <= self.sample_size <= MAX_EXAM_SAMPLES:
            raise ValueError("sample_size must be an integer from 1 to 16")
        for observations, name in (
            (self.observed_topics, "observed_topics"),
            (self.observed_formats, "observed_formats"),
        ):
            values = tuple(observations)
            if not 1 <= len(values) <= 64 or not all(
                isinstance(item, ExamObservation) for item in values
            ):
                raise ValueError(f"{name} must contain 1..64 observations")
            object.__setattr__(self, name, values)
        limitations = tuple(self.limitations)
        if not 2 <= len(limitations) <= 4:
            raise ValueError("limitations must contain 2..4 validator-derived strings")
        for item in limitations:
            _text(item, "limitation", 1000)
        if len(set(limitations)) != len(limitations):
            raise ValueError("limitations must be ordered and unique")
        object.__setattr__(self, "limitations", limitations)

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "sample_size": self.sample_size,
                "observed_topics": tuple(item.to_json() for item in self.observed_topics),
                "observed_formats": tuple(item.to_json() for item in self.observed_formats),
                "limitations": self.limitations,
            }
        )

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> ExamAnalysisProposal:
        _exact(
            value,
            {"sample_size", "observed_topics", "observed_formats", "limitations"},
            "exam analysis proposal",
        )
        return cls(
            _integer(value, "sample_size"),
            tuple(
                ExamObservation.from_json(_mapping(item, "topic"))
                for item in _array(value, "observed_topics")
            ),
            tuple(
                ExamObservation.from_json(_mapping(item, "format"))
                for item in _array(value, "observed_formats")
            ),
            _strings(value, "limitations"),
        )


@dataclass(frozen=True, slots=True)
class ExamEvidenceMapping:
    evidence_id: str
    sample_key: str
    source_id: SourceId
    revision_id: RevisionId
    chunk_id: ChunkId
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class ExamAnalysisProofReference:
    task_id: str
    run_id: RunId
    receipt_fingerprint: str
    proof_fingerprint: str


def _scope_fingerprint(samples: tuple[PreparedExamSample, ...], evidence: EvidenceEnvelope) -> str:
    payload = freeze_object(
        {"samples": tuple(item.to_json() for item in samples), "evidence": evidence.to_json()}
    )
    return sha256(_SCOPE_DOMAIN + canonical_json_bytes(payload)).hexdigest()


def _projection_fingerprint(items: tuple[ExamPromptEvidenceItem, ...], scope: str) -> str:
    payload = freeze_object(
        {"items": tuple(item.to_json() for item in items), "scope_fingerprint": scope}
    )
    return sha256(_PROJECTION_DOMAIN + canonical_json_bytes(payload)).hexdigest()


def _decode(data: bytes, name: str) -> JsonObject:
    try:
        raw: Any = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} bytes are invalid JSON") from error
    frozen = freeze_json(cast(JsonValue, raw))
    if not isinstance(frozen, Mapping):
        raise ValueError(f"{name} must be an object")
    return freeze_object(frozen)


def _exact(value: Mapping[str, JsonValue], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{name} fields must be exact")


def _mapping(value: JsonValue, name: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return freeze_object(value)


def _array(value: Mapping[str, JsonValue], key: str) -> tuple[JsonValue, ...]:
    raw = value.get(key)
    if not isinstance(raw, tuple):
        raise ValueError(f"{key} must be an array")
    return raw


def _strings(value: Mapping[str, JsonValue], key: str) -> tuple[str, ...]:
    raw = _array(value, key)
    if not all(isinstance(item, str) for item in raw):
        raise ValueError(f"{key} must contain strings")
    return cast(tuple[str, ...], raw)


def _string(value: Mapping[str, JsonValue], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise ValueError(f"{key} must be a string")
    return raw


def _integer(value: Mapping[str, JsonValue], key: str) -> int:
    raw = value.get(key)
    if type(raw) is not int:
        raise ValueError(f"{key} must be an integer")
    return raw


def _text(value: str, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be non-empty trimmed bounded text")


def _opaque(value: str, name: str) -> None:
    _text(value, name, 256)
    if _PORTABLE_KEY.fullmatch(value) is None or any(
        token in value for token in ("source", "revision", "course", "chunk")
    ):
        raise ValueError(f"{name} must be an opaque portable key")


def _sha(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 fingerprint")


__all__ = [
    "ExamAnalysisProofReference",
    "ExamAnalysisProposal",
    "ExamAnalysisRequest",
    "ExamEvidenceMapping",
    "ExamObservation",
    "ExamPromptEvidenceItem",
    "ExamPromptEvidenceProjection",
    "PreparedExamSample",
    "PreparedExamSampleScope",
]
