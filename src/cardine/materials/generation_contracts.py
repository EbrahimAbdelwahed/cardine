"""Durable contracts for restart-safe paired lesson-material generation.

The workflow deliberately persists references and receipts, never generated
Markdown, in its checkpoint.  The checkpoint is a small, canonical JSON value
that can be compared byte-for-byte by any CAS-capable operational store.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, cast

from study_agent.domain import (
    BlobId,
    BlobRef,
    CourseId,
    RevisionId,
    RunId,
    SessionId,
    SourceDocument,
    SourceId,
    SourceKind,
)
from study_agent.domain._validation import JsonObject, JsonValue, freeze_object, require_text
from study_agent.ports.model import ModelFinishReason, ModelUsage
from study_agent.state import canonical_json_bytes

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_CHECKPOINT_BYTES = 256 * 1024
MAX_UNITS = 256
MAX_SEGMENTS = 16
MAX_BOUNDARY_BYTES = 128 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_TRANSCRIPT_CHARACTERS = 512_000
MAX_LIMITATIONS = 16
MAX_LIMITATION_BYTES = 8 * 1024
MAX_LIMITATION_ITEM_BYTES = 1_000


class MaterialGenerationStage(StrEnum):
    QUEUED = "queued"
    BOUNDARIES = "boundaries"
    COMPLETE_SEGMENT = "complete_segment"
    COMPLETE_MERGE = "complete_merge"
    STUDY = "study"
    PROPOSAL = "proposal"
    PROPOSED = "proposed"
    RETRYABLE = "retryable"
    STALE = "stale"
    FAILED_TERMINAL = "failed_terminal"


class MaterialGenerationErrorCode(StrEnum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    RATE_LIMITED = "rate_limited"
    MALFORMED_OUTPUT = "malformed_output"
    COVERAGE_GAP = "coverage_gap"
    OVERSIZE = "oversize"
    STALE_INPUT = "stale_input"
    CONSENT_REQUIRED = "consent_required"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class PinnedTranscriptInput:
    """A content-addressed, exact snapshot of one current source revision."""

    course_id: CourseId
    session_id: SessionId
    source_id: SourceId
    revision_id: RevisionId
    source_kind: SourceKind
    title: str
    blob: BlobRef
    normalized_blob: BlobRef
    normalized_character_length: int
    source_checksum_sha256: str
    source_sequence: int
    source_role: str = "lesson"
    trust_level: int = 0

    @classmethod
    def from_source(
        cls,
        source: SourceDocument,
        *,
        course_id: CourseId,
        session_id: SessionId,
        source_sequence: int,
    ) -> PinnedTranscriptInput:
        return cls(
            course_id=course_id,
            session_id=session_id,
            source_id=source.source_id,
            revision_id=source.revision_id,
            source_kind=source.kind,
            title=source.title,
            blob=source.blob,
            normalized_blob=source.normalized_blob,
            normalized_character_length=source.normalized_character_length,
            source_checksum_sha256=source.checksum_sha256,
            source_sequence=source_sequence,
            source_role=source.source_role,
            trust_level=source.trust_level,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.course_id, CourseId) or not isinstance(self.session_id, SessionId):
            raise TypeError("pinned transcript requires typed course and session")
        if not isinstance(self.source_id, SourceId) or not isinstance(self.revision_id, RevisionId):
            raise TypeError("pinned transcript requires typed source and revision")
        if not isinstance(self.source_kind, SourceKind):
            raise TypeError("pinned transcript source_kind is invalid")
        if self.source_kind not in (SourceKind.TEXT, SourceKind.MARKDOWN):
            raise ValueError("only text and Markdown transcripts are supported")
        require_text(self.title, "pinned transcript title")
        if len(self.title) > 512:
            raise ValueError("pinned transcript title is oversized")
        require_text(self.source_role, "pinned transcript source_role")
        if not isinstance(self.blob, BlobRef) or not isinstance(self.normalized_blob, BlobRef):
            raise TypeError("pinned transcript blobs are invalid")
        if (
            type(self.normalized_character_length) is not int
            or not 1 <= self.normalized_character_length <= MAX_TRANSCRIPT_CHARACTERS
        ):
            raise ValueError("normalized_character_length is outside its supported bound")
        if (
            self.blob.byte_length > MAX_SOURCE_BYTES
            or self.normalized_blob.byte_length > MAX_SOURCE_BYTES
        ):
            raise ValueError("pinned transcript blob is oversized")
        _sha(self.source_checksum_sha256, "source_checksum_sha256")
        if self.source_checksum_sha256 != self.blob.checksum_sha256:
            raise ValueError("source checksum must match the pinned blob")
        if type(self.source_sequence) is not int or self.source_sequence < 0:
            raise ValueError("source_sequence must be non-negative")
        if type(self.trust_level) is not int or not 0 <= self.trust_level <= 100:
            raise ValueError("trust_level must be between 0 and 100")

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "course_id": str(self.course_id),
                "session_id": str(self.session_id),
                "source_id": str(self.source_id),
                "revision_id": str(self.revision_id),
                "source_kind": self.source_kind.value,
                "title": self.title,
                "blob": _blob_json(self.blob),
                "normalized_blob": _blob_json(self.normalized_blob),
                "normalized_character_length": self.normalized_character_length,
                "source_checksum_sha256": self.source_checksum_sha256,
                "source_sequence": self.source_sequence,
                "source_role": self.source_role,
                "trust_level": self.trust_level,
            }
        )

    @classmethod
    def from_json(cls, raw: Mapping[str, JsonValue]) -> PinnedTranscriptInput:
        _exact(
            raw,
            {
                "course_id",
                "session_id",
                "source_id",
                "revision_id",
                "source_kind",
                "title",
                "blob",
                "normalized_blob",
                "normalized_character_length",
                "source_checksum_sha256",
                "source_sequence",
                "source_role",
                "trust_level",
            },
            "pinned transcript",
        )
        return cls(
            CourseId(_text(raw, "course_id")),
            SessionId(_text(raw, "session_id")),
            SourceId(_text(raw, "source_id")),
            RevisionId(_text(raw, "revision_id")),
            SourceKind(_text(raw, "source_kind")),
            _text(raw, "title"),
            _blob(raw, "blob"),
            _blob(raw, "normalized_blob"),
            _integer(raw, "normalized_character_length"),
            _text(raw, "source_checksum_sha256"),
            _integer(raw, "source_sequence"),
            _text(raw, "source_role"),
            _integer(raw, "trust_level"),
        )

    @property
    def fingerprint(self) -> str:
        return sha256(b"pinned-transcript@1\0" + canonical_json_bytes(self.to_json())).hexdigest()


@dataclass(frozen=True, slots=True)
class GenerationPipelinePins:
    pipeline: str = "material-generation@1"
    boundaries_prompt: str = "material-boundaries@1"
    complete_segment_prompt: str = "complete-segment@1"
    complete_merge_prompt: str = "complete-merge@1"
    study_prompt: str = "study-from-complete@1"
    model_adapter: str = "openai-gpt-5.6-luna@1.0.0"
    model_id: str = "gpt-5.6-luna"
    validator: str = "material-validators@1"
    chunker: str = "material-units@1"

    def __post_init__(self) -> None:
        for name in (
            "pipeline",
            "boundaries_prompt",
            "complete_segment_prompt",
            "complete_merge_prompt",
            "study_prompt",
            "model_adapter",
            "model_id",
            "validator",
            "chunker",
        ):
            require_text(getattr(self, name), name)

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json_bytes(self.to_json())).hexdigest()

    def to_json(self) -> JsonObject:
        return freeze_object({name: getattr(self, name) for name in self.__dataclass_fields__})

    @classmethod
    def from_json(cls, raw: Mapping[str, JsonValue]) -> GenerationPipelinePins:
        _exact(raw, set(cls.__dataclass_fields__), "pipeline pins")
        return cls(**{name: _text(raw, name) for name in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class MaterialGenerationRequest:
    pin: PinnedTranscriptInput
    request_id: str
    pins: GenerationPipelinePins = field(default_factory=GenerationPipelinePins)
    max_units: int = MAX_UNITS
    max_segments: int = MAX_SEGMENTS

    def __post_init__(self) -> None:
        if not isinstance(self.pin, PinnedTranscriptInput):
            raise TypeError("generation request requires PinnedTranscriptInput")
        require_text(self.request_id, "generation request_id")
        if len(self.request_id) > 256:
            raise ValueError("generation request_id is oversized")
        if type(self.max_units) is not int or not 1 <= self.max_units <= MAX_UNITS:
            raise ValueError("max_units is outside the supported bound")
        if type(self.max_segments) is not int or not 1 <= self.max_segments <= MAX_SEGMENTS:
            raise ValueError("max_segments is outside the supported bound")

    @property
    def job_id(self) -> str:
        payload = (
            f"material-generation-job@1\0{self.pin.course_id}\0"
            f"{self.pin.session_id}\0{self.request_id}"
        )
        return f"material-job-sha256:{sha256(payload.encode()).hexdigest()}"

    @property
    def fingerprint(self) -> str:
        return sha256(
            b"material-generation-request@1\0"
            + canonical_json_bytes(
                {
                    "pin": self.pin.to_json(),
                    "request_id": self.request_id,
                    "pins": self.pins.to_json(),
                    "max_units": self.max_units,
                    "max_segments": self.max_segments,
                }
            )
        ).hexdigest()

    @property
    def pipeline_fingerprint(self) -> str:
        return self.pins.fingerprint

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "pin": self.pin.to_json(),
                "request_id": self.request_id,
                "pins": self.pins.to_json(),
                "max_units": self.max_units,
                "max_segments": self.max_segments,
            }
        )

    @classmethod
    def from_json(cls, raw: Mapping[str, JsonValue]) -> MaterialGenerationRequest:
        _exact(
            raw, {"pin", "request_id", "pins", "max_units", "max_segments"}, "generation request"
        )
        return cls(
            PinnedTranscriptInput.from_json(_mapping(raw, "pin")),
            _text(raw, "request_id"),
            GenerationPipelinePins.from_json(_mapping(raw, "pins")),
            _integer(raw, "max_units"),
            _integer(raw, "max_segments"),
        )


@dataclass(frozen=True, slots=True)
class StageReceipt:
    stage: MaterialGenerationStage
    stage_ordinal: int
    attempt: int
    input_blobs: tuple[BlobRef, ...]
    output: BlobRef
    prompt_id: str
    prompt_version: str
    composition_fingerprint: str
    adapter_id: str
    adapter_version: str
    model_id: str
    provider_response_id: str | None
    usage: ModelUsage | None
    finish_reason: ModelFinishReason
    tool_calls: bool
    validator_fingerprint: str
    limitations_fingerprint: str
    receipt_fingerprint: str
    completed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.stage, MaterialGenerationStage):
            raise TypeError("stage receipt stage is invalid")
        if type(self.stage_ordinal) is not int or self.stage_ordinal < 0:
            raise ValueError("stage receipt ordinal must be non-negative")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("stage receipt attempt must be positive")
        input_blobs = tuple(self.input_blobs)
        if not input_blobs or len(input_blobs) > MAX_SEGMENTS + 4:
            raise ValueError("stage receipt input blobs are outside their bound")
        if any(not isinstance(item, BlobRef) for item in input_blobs):
            raise TypeError("stage receipt input blobs must be BlobRefs")
        object.__setattr__(self, "input_blobs", input_blobs)
        if not isinstance(self.output, BlobRef):
            raise TypeError("stage receipt output must be BlobRef")
        for name in (
            "prompt_id",
            "prompt_version",
            "adapter_id",
            "adapter_version",
            "model_id",
        ):
            require_text(getattr(self, name), name)
        _sha(self.composition_fingerprint, "composition_fingerprint")
        _sha(self.validator_fingerprint, "validator_fingerprint")
        _sha(self.limitations_fingerprint, "limitations_fingerprint")
        if not isinstance(self.finish_reason, ModelFinishReason):
            try:
                object.__setattr__(self, "finish_reason", ModelFinishReason(self.finish_reason))
            except ValueError as error:
                raise ValueError("stage receipt finish_reason is invalid") from error
        if self.finish_reason is not ModelFinishReason.STOP:
            raise ValueError("committed stage receipt requires stop finish")
        if type(self.tool_calls) is not bool or self.tool_calls:
            raise ValueError("committed stage receipt must prove no tool calls")
        if self.usage is not None and not isinstance(self.usage, ModelUsage):
            raise TypeError("stage receipt usage must be ModelUsage or None")
        _sha(self.receipt_fingerprint, "stage receipt fingerprint")
        if self.provider_response_id is not None:
            require_text(self.provider_response_id, "provider_response_id")
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        expected = self.fingerprint_for(
            self.stage,
            self.stage_ordinal,
            self.attempt,
            self.input_blobs,
            self.output,
            self.prompt_id,
            self.prompt_version,
            self.composition_fingerprint,
            self.adapter_id,
            self.adapter_version,
            self.model_id,
            self.provider_response_id,
            self.usage,
            self.finish_reason,
            self.tool_calls,
            self.validator_fingerprint,
            self.limitations_fingerprint,
            self.completed_at,
        )
        if self.receipt_fingerprint != expected:
            raise ValueError("stage receipt fingerprint does not match its fields")

    @staticmethod
    def fingerprint_for(
        stage: MaterialGenerationStage,
        stage_ordinal: int,
        attempt: int,
        input_blobs: tuple[BlobRef, ...],
        output: BlobRef,
        prompt_id: str,
        prompt_version: str,
        composition_fingerprint: str,
        adapter_id: str,
        adapter_version: str,
        model_id: str,
        provider_response_id: str | None,
        usage: ModelUsage | None,
        finish_reason: ModelFinishReason,
        tool_calls: bool,
        validator_fingerprint: str,
        limitations_fingerprint: str,
        completed_at: datetime,
    ) -> str:
        return sha256(
            b"material-stage-receipt@2\0"
            + canonical_json_bytes(
                {
                    "stage": stage.value,
                    "stage_ordinal": stage_ordinal,
                    "attempt": attempt,
                    "input_blobs": tuple(_blob_json(item) for item in input_blobs),
                    "output": _blob_json(output),
                    "prompt_id": prompt_id,
                    "prompt_version": prompt_version,
                    "composition_fingerprint": composition_fingerprint,
                    "adapter_id": adapter_id,
                    "adapter_version": adapter_version,
                    "model_id": model_id,
                    "provider_response_id": provider_response_id,
                    "usage": _usage_json(usage),
                    "finish_reason": finish_reason.value,
                    "tool_calls": tool_calls,
                    "validator_fingerprint": validator_fingerprint,
                    "limitations_fingerprint": limitations_fingerprint,
                    "completed_at": _timestamp(completed_at),
                }
            )
        ).hexdigest()

    @property
    def ordinal(self) -> int:
        return self.stage_ordinal

    @property
    def input_refs(self) -> tuple[BlobRef, ...]:
        return self.input_blobs

    @property
    def prompt_composition_fingerprint(self) -> str:
        return self.composition_fingerprint

    @property
    def no_tools(self) -> bool:
        return not self.tool_calls

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "stage": self.stage.value,
                "stage_ordinal": self.stage_ordinal,
                "attempt": self.attempt,
                "input_blobs": tuple(_blob_json(item) for item in self.input_blobs),
                "output": _blob_json(self.output),
                "prompt_id": self.prompt_id,
                "prompt_version": self.prompt_version,
                "composition_fingerprint": self.composition_fingerprint,
                "adapter_id": self.adapter_id,
                "adapter_version": self.adapter_version,
                "model_id": self.model_id,
                "provider_response_id": self.provider_response_id,
                "usage": _usage_json(self.usage),
                "finish_reason": self.finish_reason.value,
                "tool_calls": self.tool_calls,
                "validator_fingerprint": self.validator_fingerprint,
                "limitations_fingerprint": self.limitations_fingerprint,
                "receipt_fingerprint": self.receipt_fingerprint,
                "completed_at": _timestamp(self.completed_at),
            }
        )

    @classmethod
    def from_json(cls, raw: Mapping[str, JsonValue]) -> StageReceipt:
        _exact(
            raw,
            {
                "stage",
                "stage_ordinal",
                "attempt",
                "input_blobs",
                "output",
                "prompt_id",
                "prompt_version",
                "composition_fingerprint",
                "adapter_id",
                "adapter_version",
                "model_id",
                "provider_response_id",
                "usage",
                "finish_reason",
                "tool_calls",
                "validator_fingerprint",
                "limitations_fingerprint",
                "receipt_fingerprint",
                "completed_at",
            },
            "stage receipt",
        )
        return cls(
            MaterialGenerationStage(_text(raw, "stage")),
            _integer(raw, "stage_ordinal"),
            _integer(raw, "attempt"),
            tuple(_blob_value(item, "input_blobs item") for item in _array(raw, "input_blobs")),
            _blob(raw, "output"),
            _text(raw, "prompt_id"),
            _text(raw, "prompt_version"),
            _text(raw, "composition_fingerprint"),
            _text(raw, "adapter_id"),
            _text(raw, "adapter_version"),
            _text(raw, "model_id"),
            _optional_text(raw, "provider_response_id"),
            _usage(raw, "usage"),
            ModelFinishReason(_text(raw, "finish_reason")),
            _boolean(raw, "tool_calls"),
            _text(raw, "validator_fingerprint"),
            _text(raw, "limitations_fingerprint"),
            _text(raw, "receipt_fingerprint"),
            _datetime(raw.get("completed_at"), "completed_at"),
        )


@dataclass(frozen=True, slots=True)
class MaterialGenerationState:
    request: MaterialGenerationRequest
    run_id: RunId
    stage: MaterialGenerationStage = MaterialGenerationStage.QUEUED
    unit_manifest: BlobRef | None = None
    boundaries: BlobRef | None = None
    segments: tuple[BlobRef, ...] = ()
    complete: BlobRef | None = None
    complete_limitations: tuple[str, ...] = ()
    study: BlobRef | None = None
    study_limitations: tuple[str, ...] = ()
    receipts: tuple[StageReceipt, ...] = ()
    error_code: MaterialGenerationErrorCode | None = None
    error_message: str | None = None
    lease_stage: MaterialGenerationStage | None = None
    lease_token: str | None = None
    lease_until: datetime | None = None
    retry_stage: MaterialGenerationStage | None = None
    proposal_batch_id: str | None = None
    proposal_sequence: int | None = None
    generation: int = 0
    claim_attempt: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.request, MaterialGenerationRequest) or not isinstance(
            self.run_id, RunId
        ):
            raise TypeError("generation state identity is invalid")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("generation must be non-negative")
        if type(self.claim_attempt) is not int or self.claim_attempt < 0:
            raise ValueError("claim_attempt must be non-negative")
        segments = tuple(self.segments)
        if len(segments) > MAX_SEGMENTS or any(not isinstance(item, BlobRef) for item in segments):
            raise ValueError("generation state segment refs are invalid")
        object.__setattr__(self, "segments", segments)
        for name in ("complete_limitations", "study_limitations"):
            limitations = tuple(getattr(self, name))
            _validate_limitations(limitations, name, allow_empty=getattr(self, name) == ())
            object.__setattr__(self, name, limitations)
        receipts = tuple(self.receipts)
        if len(receipts) > MAX_SEGMENTS + 8 or any(
            not isinstance(item, StageReceipt) for item in receipts
        ):
            raise ValueError("generation state receipts are invalid")
        object.__setattr__(self, "receipts", receipts)
        for name in ("unit_manifest", "boundaries", "complete", "study"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, BlobRef):
                raise TypeError(f"{name} must be a BlobRef or None")
        if (self.complete is None) != (not self.complete_limitations):
            raise ValueError("complete limitations must accompany the complete output")
        if (self.study is None) != (not self.study_limitations):
            raise ValueError("study limitations must accompany the study output")
        if self.error_message is not None:
            require_text(self.error_message, "error_message")
        if self.error_code is None and self.error_message is not None:
            raise ValueError("error_message requires error_code")
        if self.lease_stage is None and any(
            item is not None for item in (self.lease_token, self.lease_until)
        ):
            raise ValueError("lease fields require lease_stage")
        if self.lease_stage is not None:
            if self.lease_token is None or self.lease_until is None:
                raise ValueError("lease requires token and expiry")
            require_text(self.lease_token, "lease_token")
        if self.stage is MaterialGenerationStage.RETRYABLE and self.retry_stage is None:
            raise ValueError("retryable state requires a resume stage")
        if self.stage is MaterialGenerationStage.PROPOSED and (
            self.proposal_batch_id is None or self.proposal_sequence is None
        ):
            raise ValueError("proposed state requires its committed proposal receipt")
        if self.proposal_sequence is not None and self.proposal_sequence < 1:
            raise ValueError("proposal_sequence must be positive")

    @property
    def job_id(self) -> str:
        return self.request.job_id

    @property
    def request_fingerprint(self) -> str:
        return self.request.fingerprint

    def to_json(self) -> JsonObject:
        result: dict[str, JsonValue] = {
            "request": self.request.to_json(),
            "run_id": str(self.run_id),
            "stage": self.stage.value,
            "unit_manifest": _blob_json(self.unit_manifest) if self.unit_manifest else None,
            "boundaries": _blob_json(self.boundaries) if self.boundaries else None,
            "segments": tuple(_blob_json(item) for item in self.segments),
            "complete": _blob_json(self.complete) if self.complete else None,
            "complete_limitations": self.complete_limitations,
            "study": _blob_json(self.study) if self.study else None,
            "study_limitations": self.study_limitations,
            "receipts": tuple(item.to_json() for item in self.receipts),
            "error_code": self.error_code.value if self.error_code else None,
            "error_message": self.error_message,
            "lease_stage": self.lease_stage.value if self.lease_stage else None,
            "lease_token": self.lease_token,
            "lease_until": _timestamp(self.lease_until) if self.lease_until else None,
            "retry_stage": self.retry_stage.value if self.retry_stage else None,
            "proposal_batch_id": self.proposal_batch_id,
            "proposal_sequence": self.proposal_sequence,
            "generation": self.generation,
            "claim_attempt": self.claim_attempt,
        }
        encoded = canonical_json_bytes(freeze_object(result))
        if len(encoded) > MAX_CHECKPOINT_BYTES:
            raise ValueError("generation checkpoint exceeds its bounded size")
        return freeze_object(result)

    def to_bytes(self) -> bytes:
        encoded = canonical_json_bytes(self.to_json())
        if len(encoded) > MAX_CHECKPOINT_BYTES:
            raise ValueError("generation checkpoint exceeds its bounded size")
        return encoded

    @classmethod
    def from_json(cls, raw: Mapping[str, JsonValue]) -> MaterialGenerationState:
        _exact(
            raw,
            {
                "request",
                "run_id",
                "stage",
                "unit_manifest",
                "boundaries",
                "segments",
                "complete",
                "complete_limitations",
                "study",
                "study_limitations",
                "receipts",
                "error_code",
                "error_message",
                "lease_stage",
                "lease_token",
                "lease_until",
                "retry_stage",
                "proposal_batch_id",
                "proposal_sequence",
                "generation",
                "claim_attempt",
            },
            "generation checkpoint",
        )
        return cls(
            MaterialGenerationRequest.from_json(_mapping(raw, "request")),
            RunId(_text(raw, "run_id")),
            MaterialGenerationStage(_text(raw, "stage")),
            _optional_blob(raw, "unit_manifest"),
            _optional_blob(raw, "boundaries"),
            tuple(_blob_value(item, "segments item") for item in _array(raw, "segments")),
            _optional_blob(raw, "complete"),
            _string_array(raw, "complete_limitations"),
            _optional_blob(raw, "study"),
            _string_array(raw, "study_limitations"),
            tuple(
                StageReceipt.from_json(_mapping(item, "receipt"))
                for item in _array(raw, "receipts")
            ),
            MaterialGenerationErrorCode(_text(raw, "error_code"))
            if raw.get("error_code") is not None
            else None,
            _optional_text(raw, "error_message"),
            MaterialGenerationStage(_text(raw, "lease_stage"))
            if raw.get("lease_stage") is not None
            else None,
            _optional_text(raw, "lease_token"),
            _datetime(raw.get("lease_until"), "lease_until")
            if raw.get("lease_until") is not None
            else None,
            MaterialGenerationStage(_text(raw, "retry_stage"))
            if raw.get("retry_stage") is not None
            else None,
            _optional_text(raw, "proposal_batch_id"),
            _optional_integer(raw, "proposal_sequence"),
            _integer(raw, "generation"),
            _integer(raw, "claim_attempt"),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> MaterialGenerationState:
        if not isinstance(data, bytes) or len(data) > MAX_CHECKPOINT_BYTES:
            raise ValueError("generation checkpoint bytes are invalid or oversized")
        try:
            raw: Any = json.loads(data)
            if not isinstance(raw, dict):
                raise ValueError("checkpoint must be an object")
            value = cls.from_json(freeze_object(cast(Mapping[str, JsonValue], raw)))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("generation checkpoint bytes are not canonical") from error
        if value.to_bytes() != data:
            raise ValueError("generation checkpoint bytes are not canonical")
        return value


@dataclass(frozen=True, slots=True)
class MaterialGenerationView:
    job_id: str
    run_id: RunId
    request_fingerprint: str
    stage: MaterialGenerationStage
    unit_count: int | None
    segment_count: int
    complete: BlobRef | None
    study: BlobRef | None
    error_code: MaterialGenerationErrorCode | None
    error_message: str | None
    proposal_batch_id: str | None

    @property
    def status(self) -> MaterialGenerationStage:
        return self.stage

    @classmethod
    def from_state(cls, state: MaterialGenerationState) -> MaterialGenerationView:
        return cls(
            state.job_id,
            state.run_id,
            state.request_fingerprint,
            state.stage,
            None,
            len(state.segments),
            state.complete,
            state.study,
            state.error_code,
            state.error_message,
            state.proposal_batch_id,
        )


def _blob_json(value: BlobRef) -> JsonObject:
    return {
        "id": str(value.id),
        "checksum_sha256": value.checksum_sha256,
        "byte_length": value.byte_length,
    }


def _blob_value(value: JsonValue, name: str) -> BlobRef:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return _blob(value, name)


def _blob(raw: Mapping[str, JsonValue], name: str) -> BlobRef:
    value = raw.get(name) if name in raw else raw
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    _exact(value, {"id", "checksum_sha256", "byte_length"}, name)
    digest = _text(value, "checksum_sha256")
    ref = BlobRef(BlobId(_text(value, "id")), digest, _integer(value, "byte_length"))
    if str(ref.id) != f"sha256:{ref.checksum_sha256}":
        raise ValueError(f"{name} id does not match checksum")
    return ref


def _optional_blob(raw: Mapping[str, JsonValue], name: str) -> BlobRef | None:
    value = raw.get(name)
    return None if value is None else _blob_value(value, name)


def _sha(value: str, name: str) -> None:
    require_text(value, name)
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 fingerprint")


def _exact(value: Mapping[str, JsonValue], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} fields are not canonical")


def _text(value: Mapping[str, JsonValue], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str):
        raise ValueError(f"{name} must be text")
    require_text(item, name)
    return item


def _optional_text(value: Mapping[str, JsonValue], name: str) -> str | None:
    item = value.get(name)
    if item is None:
        return None
    if not isinstance(item, str):
        raise ValueError(f"{name} must be text or null")
    require_text(item, name)
    return item


def _integer(value: Mapping[str, JsonValue], name: str) -> int:
    item = value.get(name)
    if type(item) is not int:
        raise ValueError(f"{name} must be an integer")
    return item


def _optional_integer(value: Mapping[str, JsonValue], name: str) -> int | None:
    item = value.get(name)
    if item is None:
        return None
    if type(item) is not int:
        raise ValueError(f"{name} must be an integer or null")
    return item


def _boolean(value: Mapping[str, JsonValue], name: str) -> bool:
    item = value.get(name)
    if type(item) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return item


def _usage_json(value: ModelUsage | None) -> JsonObject | None:
    if value is None:
        return None
    return {"input_tokens": value.input_tokens, "output_tokens": value.output_tokens}


def _usage(value: Mapping[str, JsonValue], name: str) -> ModelUsage | None:
    item = value.get(name)
    if item is None:
        return None
    if not isinstance(item, Mapping):
        raise ValueError(f"{name} must be an object or null")
    _exact(item, {"input_tokens", "output_tokens"}, name)
    return ModelUsage(_integer(item, "input_tokens"), _integer(item, "output_tokens"))


def _mapping(value: object, name: str) -> Mapping[str, JsonValue]:
    item: object = value
    if isinstance(value, Mapping) and name in value:
        item = value[name]
    if not isinstance(item, Mapping):
        raise ValueError(f"{name} must be an object")
    return item


def _array(value: Mapping[str, JsonValue], name: str) -> tuple[JsonValue, ...]:
    item = value.get(name)
    if not isinstance(item, tuple):
        raise ValueError(f"{name} must be an array")
    return item


def _string_array(value: Mapping[str, JsonValue], name: str) -> tuple[str, ...]:
    raw = _array(value, name)
    result = tuple(item for item in raw if isinstance(item, str))
    if len(result) != len(raw):
        raise ValueError(f"{name} must contain only text")
    _validate_limitations(result, name, allow_empty=True)
    return result


def _validate_limitations(values: tuple[str, ...], name: str, *, allow_empty: bool) -> None:
    if not allow_empty and not values:
        raise ValueError(f"{name} must be non-empty")
    if len(values) > MAX_LIMITATIONS:
        raise ValueError(f"{name} exceeds its item bound")
    total = 0
    for item in values:
        if not isinstance(item, str) or not item.strip() or "\x00" in item:
            raise ValueError(f"{name} contains invalid text")
        if len(item.encode("utf-8")) > MAX_LIMITATION_ITEM_BYTES:
            raise ValueError(f"{name} contains an oversized item")
        if unicodedata.normalize("NFC", item) != item:
            raise ValueError(f"{name} contains non-NFC text")
        total += len(item.encode("utf-8"))
    if total > MAX_LIMITATION_BYTES:
        raise ValueError(f"{name} exceeds its byte bound")


def limitations_fingerprint(values: tuple[str, ...]) -> str:
    normalized = tuple(values)
    _validate_limitations(normalized, "limitations", allow_empty=True)
    return sha256(
        b"material-limitations@1\0" + canonical_json_bytes(freeze_object({"items": normalized}))
    ).hexdigest()


def _datetime(value: JsonValue, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return result


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "MAX_BOUNDARY_BYTES",
    "MAX_CHECKPOINT_BYTES",
    "MAX_LIMITATIONS",
    "MAX_LIMITATION_BYTES",
    "MAX_LIMITATION_ITEM_BYTES",
    "MAX_OUTPUT_BYTES",
    "MAX_SEGMENTS",
    "MAX_SOURCE_BYTES",
    "MAX_TRANSCRIPT_CHARACTERS",
    "MAX_UNITS",
    "GenerationPipelinePins",
    "MaterialGenerationErrorCode",
    "MaterialGenerationRequest",
    "MaterialGenerationStage",
    "MaterialGenerationState",
    "MaterialGenerationView",
    "PinnedTranscriptInput",
    "StageReceipt",
    "limitations_fingerprint",
]
