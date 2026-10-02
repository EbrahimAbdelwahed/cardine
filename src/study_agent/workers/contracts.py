"""Strict provider-neutral contracts for isolated capability workers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any, cast

from study_agent.capabilities.contracts import CapabilityContinuation, TutorCapabilityId
from study_agent.domain._validation import (
    JsonObject,
    JsonValue,
    freeze_json,
    freeze_object,
    require_text,
)
from study_agent.domain.identifiers import RunId
from study_agent.playbooks import (
    PlaybookRunStatus,
    ToolBehaviorPin,
    ValidatorDisposition,
    VerifiedRunRecord,
    VersionPins,
)
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.state import canonical_json_bytes
from study_agent.tools.schema import validate_schema_definition

MAX_TASK_BYTES = 128 * 1024
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_OUTPUT_SCHEMA_BYTES = 32 * 1024
MAX_CONTINUATION_SUMMARY_BYTES = 16 * 1024
MAX_VERIFIED_OUTPUT_BYTES = 256 * 1024
MAX_STORED_STATE_BYTES = 512 * 1024

_TASK_DOMAIN = "generation-worker-task@1"
_PAYLOAD_DOMAIN = "generation-worker-payload@1"
_OUTPUT_DOMAIN = "generation-worker-output@1"
_RECEIPT_DOMAIN = "generation-worker-receipt@1"
_STATE_DOMAIN = "generation-worker-state@1"
_PINS_DOMAIN = "generation-worker-pins@1"
_VALIDATORS_DOMAIN = "generation-worker-validators@1"
_RUN_DOMAIN = "generation-worker-run@1"
_EXECUTION_INPUT_DOMAIN = "generation-worker-execution-input@1"


class GenerationWorkerTaskKind(StrEnum):
    FLASHCARD_BUNDLE = "flashcard_bundle"
    EXAM_ANALYSIS = "exam_analysis"
    GRADE_RESPONSE = "grade_response"


class ValidationReceiptSource(StrEnum):
    VALIDATE_STEP = "validate_step"
    STRUCTURED_OUTPUT_FALLBACK = "structured_output_fallback"


class GenerationWorkerStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUSPENDED = "suspended"
    RESUME_CLAIMED = "resume_claimed"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    CANCELLED = "cancelled"
    STALE = "stale"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset(
    {
        GenerationWorkerStatus.COMPLETED,
        GenerationWorkerStatus.TERMINATED,
        GenerationWorkerStatus.CANCELLED,
        GenerationWorkerStatus.STALE,
        GenerationWorkerStatus.FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class ValidationExpectation:
    step_id: str
    source: ValidationReceiptSource
    validator_id: str
    validator_version: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.step_id, "validation step_id"),
            (self.validator_id, "validator_id"),
            (self.validator_version, "validator_version"),
        ):
            _bounded_text(value, name, 256)
        if not isinstance(self.source, ValidationReceiptSource):
            raise TypeError("validation source must use ValidationReceiptSource")

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "step_id": self.step_id,
                "source": self.source.value,
                "validator_id": self.validator_id,
                "validator_version": self.validator_version,
            }
        )

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> ValidationExpectation:
        _exact(
            value,
            {"step_id", "source", "validator_id", "validator_version"},
            "validation expectation",
        )
        return cls(
            _string(value, "step_id"),
            ValidationReceiptSource(_string(value, "source")),
            _string(value, "validator_id"),
            _string(value, "validator_version"),
        )


@dataclass(frozen=True, slots=True)
class ObservedValidationReceipt:
    step_id: str
    source: ValidationReceiptSource
    validator_id: str
    validator_version: str
    passed: bool
    result_fingerprint: str
    disposition: ValidatorDisposition = ValidatorDisposition.CONTINUE

    def __post_init__(self) -> None:
        ValidationExpectation(self.step_id, self.source, self.validator_id, self.validator_version)
        if type(self.passed) is not bool:
            raise TypeError("validation passed must be boolean")
        if not isinstance(self.disposition, ValidatorDisposition):
            raise TypeError("validation disposition must use ValidatorDisposition")
        _require_sha256(self.result_fingerprint, "validation result_fingerprint")

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                **ValidationExpectation(
                    self.step_id,
                    self.source,
                    self.validator_id,
                    self.validator_version,
                ).to_json(),
                "passed": self.passed,
                "disposition": self.disposition.value,
                "result_fingerprint": self.result_fingerprint,
            }
        )


@dataclass(frozen=True, slots=True)
class VerifiedPromptReceipt:
    prompt_id: str
    prompt_version: str
    composition_fingerprint: str
    layer_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _bounded_text(self.prompt_id, "prompt_id", 256)
        _bounded_text(self.prompt_version, "prompt_version", 128)
        _require_sha256(self.composition_fingerprint, "composition_fingerprint")
        layers = tuple(self.layer_fingerprints)
        for item in layers:
            _require_sha256(item, "prompt layer fingerprint")
        if len(set(layers)) != len(layers):
            raise ValueError("prompt layer fingerprints must be ordered and unique")
        object.__setattr__(self, "layer_fingerprints", layers)

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "prompt_id": self.prompt_id,
                "prompt_version": self.prompt_version,
                "composition_fingerprint": self.composition_fingerprint,
                "layer_fingerprints": self.layer_fingerprints,
            }
        )


@dataclass(frozen=True, slots=True)
class GenerationWorkerTask:
    task_id: str
    task_kind: GenerationWorkerTaskKind
    capability_id: TutorCapabilityId
    capability_version: SemanticVersion
    manifest_fingerprint: str
    required_authority: tuple[str, ...]
    pins: VersionPins
    definition_fingerprint: str
    language: str
    preferences: JsonObject
    continuation_summary: JsonObject | None
    index_references: tuple[str, ...]
    evidence_references: tuple[str, ...]
    payload: JsonObject
    output_schema: JsonObject
    output_schema_fingerprint: str
    expected_validations: tuple[ValidationExpectation, ...]

    def __post_init__(self) -> None:
        _bounded_text(self.task_id, "task_id", 256)
        if not isinstance(self.task_kind, GenerationWorkerTaskKind):
            raise TypeError("task_kind must use GenerationWorkerTaskKind")
        if not isinstance(self.capability_id, TutorCapabilityId):
            raise TypeError("capability_id must use TutorCapabilityId")
        if not isinstance(self.capability_version, SemanticVersion):
            raise TypeError("capability_version must use SemanticVersion")
        _require_sha256(self.manifest_fingerprint, "manifest_fingerprint")
        _require_sha256(self.definition_fingerprint, "definition_fingerprint")
        if not isinstance(self.pins, VersionPins):
            raise TypeError("pins must use VersionPins")
        _bounded_text(self.language, "language", 64)

        authority = _bounded_unique_texts(self.required_authority, "required_authority", 32, 256)
        if not authority:
            raise ValueError("required_authority cannot be empty")
        object.__setattr__(self, "required_authority", tuple(sorted(authority)))
        object.__setattr__(
            self,
            "index_references",
            _bounded_unique_texts(self.index_references, "index_references", 256, 512),
        )
        object.__setattr__(
            self,
            "evidence_references",
            _bounded_unique_texts(self.evidence_references, "evidence_references", 256, 512),
        )

        preferences = freeze_object(self.preferences)
        summary = (
            freeze_object(self.continuation_summary)
            if self.continuation_summary is not None
            else None
        )
        payload = freeze_object(self.payload)
        schema = freeze_object(self.output_schema)
        for value, name in ((preferences, "preferences"), (payload, "payload")):
            _reject_forbidden_keys(value, name)
        if summary is not None:
            _reject_forbidden_keys(summary, "continuation_summary")
            _require_size(summary, MAX_CONTINUATION_SUMMARY_BYTES, "continuation summary")
        _require_size(payload, MAX_PAYLOAD_BYTES, "worker payload")
        _require_size(schema, MAX_OUTPUT_SCHEMA_BYTES, "worker output schema")
        validate_schema_definition(schema)
        if fingerprint_output_schema(schema) != self.output_schema_fingerprint:
            raise ValueError("output schema fingerprint does not match output_schema")
        object.__setattr__(self, "preferences", preferences)
        object.__setattr__(self, "continuation_summary", summary)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "output_schema", schema)

        expectations = tuple(self.expected_validations)
        if (
            not expectations
            or len(expectations) > 32
            or not all(isinstance(item, ValidationExpectation) for item in expectations)
        ):
            raise ValueError("expected_validations must contain 1..32 expectations")
        keys = tuple(
            (item.step_id, item.source, item.validator_id, item.validator_version)
            for item in expectations
        )
        if len(set(keys)) != len(keys):
            raise ValueError("expected validations must be ordered and unique")
        object.__setattr__(self, "expected_validations", expectations)
        if len(self.to_bytes()) > MAX_TASK_BYTES:
            raise ValueError("canonical worker task exceeds 128 KiB")

    @property
    def payload_fingerprint(self) -> str:
        return _fingerprint(_PAYLOAD_DOMAIN, self.payload)

    @property
    def pins_fingerprint(self) -> str:
        return _fingerprint(_PINS_DOMAIN, pins_to_json(self.pins))

    @property
    def fingerprint(self) -> str:
        return _fingerprint(_TASK_DOMAIN, self.to_json())

    def capability_inputs(self) -> JsonObject:
        return self.payload

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "task_id": self.task_id,
                "task_kind": self.task_kind.value,
                "capability_id": self.capability_id.value,
                "capability_version": str(self.capability_version),
                "manifest_fingerprint": self.manifest_fingerprint,
                "required_authority": self.required_authority,
                "pins": pins_to_json(self.pins),
                "definition_fingerprint": self.definition_fingerprint,
                "language": self.language,
                "preferences": self.preferences,
                "continuation_summary": self.continuation_summary,
                "index_references": self.index_references,
                "evidence_references": self.evidence_references,
                "payload": self.payload,
                "output_schema": self.output_schema,
                "output_schema_fingerprint": self.output_schema_fingerprint,
                "expected_validations": tuple(item.to_json() for item in self.expected_validations),
            }
        )

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json())

    @classmethod
    def from_bytes(cls, data: bytes) -> GenerationWorkerTask:
        if len(data) > MAX_TASK_BYTES:
            raise ValueError("canonical worker task exceeds 128 KiB")
        value = _decode_object(data, "worker task")
        _exact(value, _TASK_FIELDS, "worker task")
        task = cls(
            task_id=_string(value, "task_id"),
            task_kind=GenerationWorkerTaskKind(_string(value, "task_kind")),
            capability_id=TutorCapabilityId(_string(value, "capability_id")),
            capability_version=SemanticVersion.parse(_string(value, "capability_version")),
            manifest_fingerprint=_string(value, "manifest_fingerprint"),
            required_authority=_string_tuple(value, "required_authority"),
            pins=pins_from_json(_mapping(value, "pins")),
            definition_fingerprint=_string(value, "definition_fingerprint"),
            language=_string(value, "language"),
            preferences=_mapping(value, "preferences"),
            continuation_summary=_optional_mapping(value, "continuation_summary"),
            index_references=_string_tuple(value, "index_references"),
            evidence_references=_string_tuple(value, "evidence_references"),
            payload=_mapping(value, "payload"),
            output_schema=_mapping(value, "output_schema"),
            output_schema_fingerprint=_string(value, "output_schema_fingerprint"),
            expected_validations=tuple(
                ValidationExpectation.from_json(_as_mapping(item, "validation expectation"))
                for item in _array(value, "expected_validations")
            ),
        )
        if task.to_bytes() != data:
            raise ValueError("worker task bytes are not canonical")
        return task


_TASK_FIELDS = {
    "task_id",
    "task_kind",
    "capability_id",
    "capability_version",
    "manifest_fingerprint",
    "required_authority",
    "pins",
    "definition_fingerprint",
    "language",
    "preferences",
    "continuation_summary",
    "index_references",
    "evidence_references",
    "payload",
    "output_schema",
    "output_schema_fingerprint",
    "expected_validations",
}


@dataclass(frozen=True, slots=True)
class ChildCapabilityObservation:
    status: GenerationWorkerStatus
    capability_id: TutorCapabilityId
    capability_version: SemanticVersion
    manifest_fingerprint: str
    run_id: RunId
    pins: VersionPins
    definition_fingerprint: str
    output_schema_fingerprint: str
    validations: tuple[ObservedValidationReceipt, ...] = ()
    prompt: VerifiedPromptReceipt | None = None
    continuation: CapabilityContinuation | None = None
    verified_run: VerifiedRunRecord | None = None
    output: JsonValue | None = None
    failure_code: str | None = None
    execution_input_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            GenerationWorkerStatus.RUNNING,
            GenerationWorkerStatus.SUSPENDED,
            *_TERMINAL_STATUSES,
        }:
            raise ValueError("child observation status is invalid")
        if not isinstance(self.capability_id, TutorCapabilityId):
            raise TypeError("observed capability_id is invalid")
        if not isinstance(self.capability_version, SemanticVersion):
            raise TypeError("observed capability_version is invalid")
        if not isinstance(self.run_id, RunId) or not isinstance(self.pins, VersionPins):
            raise TypeError("observed run identity or pins are invalid")
        for value, name in (
            (self.manifest_fingerprint, "manifest_fingerprint"),
            (self.definition_fingerprint, "definition_fingerprint"),
            (self.output_schema_fingerprint, "output_schema_fingerprint"),
        ):
            _require_sha256(value, name)
        validations = tuple(self.validations)
        if not all(isinstance(item, ObservedValidationReceipt) for item in validations):
            raise TypeError("observed validations are invalid")
        object.__setattr__(self, "validations", validations)
        if self.output is not None:
            frozen = freeze_json(self.output)
            _require_json_value_size(frozen, MAX_VERIFIED_OUTPUT_BYTES, "verified output")
            object.__setattr__(self, "output", frozen)
        if self.failure_code is not None:
            _require_failure_code(self.failure_code)
        if self.execution_input_fingerprint is not None:
            _require_sha256(self.execution_input_fingerprint, "execution_input_fingerprint")

        suspended = self.status is GenerationWorkerStatus.SUSPENDED
        completed = self.status is GenerationWorkerStatus.COMPLETED
        verified_terminal = self.status in {
            GenerationWorkerStatus.COMPLETED,
            GenerationWorkerStatus.TERMINATED,
        }
        if suspended != (self.continuation is not None):
            raise ValueError("only suspended observations carry a continuation")
        if self.continuation is not None and self.continuation.run_id != self.run_id:
            raise ValueError("continuation run does not match observation")
        if verified_terminal != (self.verified_run is not None):
            raise ValueError("completed/terminated observations require a verified run")
        if completed != (self.output is not None):
            raise ValueError("only completed observations carry verified output")
        if completed and (self.prompt is None or not validations):
            raise ValueError("completed observations require prompt and validation provenance")
        if self.verified_run is not None:
            expected = PlaybookRunStatus.COMPLETED if completed else PlaybookRunStatus.TERMINATED
            if self.verified_run.run_id != self.run_id or self.verified_run.status is not expected:
                raise ValueError("verified run does not match observed terminal state")
        if (
            self.status
            in {
                GenerationWorkerStatus.FAILED,
                GenerationWorkerStatus.CANCELLED,
                GenerationWorkerStatus.STALE,
            }
            and self.failure_code is None
        ):
            raise ValueError("failed, cancelled, and stale observations require a failure_code")


@dataclass(frozen=True, slots=True)
class GenerationWorkerReceipt:
    task_id: str
    task_kind: GenerationWorkerTaskKind
    status: GenerationWorkerStatus
    child_run_id: RunId
    task_fingerprint: str
    pins_fingerprint: str
    input_fingerprint: str
    output_fingerprint: str
    validator_fingerprint: str
    run_fingerprint: str
    prompt_fingerprint: str | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        _bounded_text(self.task_id, "task_id", 256)
        if not isinstance(self.task_kind, GenerationWorkerTaskKind):
            raise TypeError("receipt task_kind is invalid")
        if self.status not in _TERMINAL_STATUSES:
            raise ValueError("worker receipts exist only for terminal states")
        if not isinstance(self.child_run_id, RunId):
            raise TypeError("receipt child_run_id must be RunId")
        for name in (
            "task_fingerprint",
            "pins_fingerprint",
            "input_fingerprint",
            "output_fingerprint",
            "validator_fingerprint",
            "run_fingerprint",
        ):
            _require_sha256(cast(str, getattr(self, name)), name)
        if self.prompt_fingerprint is not None:
            _require_sha256(self.prompt_fingerprint, "prompt_fingerprint")
        if self.failure_code is not None:
            _require_public_failure_code(self.failure_code)
        if self.status is GenerationWorkerStatus.COMPLETED:
            if self.prompt_fingerprint is None or self.failure_code is not None:
                raise ValueError("completed receipt provenance is inconsistent")
        elif self.failure_code is None:
            raise ValueError("non-completed receipt requires a failure_code")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(_RECEIPT_DOMAIN, self.to_json())

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "task_id": self.task_id,
                "task_kind": self.task_kind.value,
                "status": self.status.value,
                "child_run_id": str(self.child_run_id),
                "task_fingerprint": self.task_fingerprint,
                "pins_fingerprint": self.pins_fingerprint,
                "input_fingerprint": self.input_fingerprint,
                "output_fingerprint": self.output_fingerprint,
                "validator_fingerprint": self.validator_fingerprint,
                "run_fingerprint": self.run_fingerprint,
                "prompt_fingerprint": self.prompt_fingerprint,
                "failure_code": self.failure_code,
            }
        )

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> GenerationWorkerReceipt:
        _exact(value, _RECEIPT_FIELDS, "worker receipt")
        return cls(
            task_id=_string(value, "task_id"),
            task_kind=GenerationWorkerTaskKind(_string(value, "task_kind")),
            status=GenerationWorkerStatus(_string(value, "status")),
            child_run_id=RunId(_string(value, "child_run_id")),
            task_fingerprint=_string(value, "task_fingerprint"),
            pins_fingerprint=_string(value, "pins_fingerprint"),
            input_fingerprint=_string(value, "input_fingerprint"),
            output_fingerprint=_string(value, "output_fingerprint"),
            validator_fingerprint=_string(value, "validator_fingerprint"),
            run_fingerprint=_string(value, "run_fingerprint"),
            prompt_fingerprint=_optional_string(value, "prompt_fingerprint"),
            failure_code=_optional_string(value, "failure_code"),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> GenerationWorkerReceipt:
        value = _decode_object(data, "worker receipt")
        receipt = cls.from_json(value)
        if receipt.to_bytes() != data:
            raise ValueError("worker receipt bytes are not canonical")
        return receipt


_RECEIPT_FIELDS = {
    "task_id",
    "task_kind",
    "status",
    "child_run_id",
    "task_fingerprint",
    "pins_fingerprint",
    "input_fingerprint",
    "output_fingerprint",
    "validator_fingerprint",
    "run_fingerprint",
    "prompt_fingerprint",
    "failure_code",
}


def fingerprint_output_schema(value: JsonObject) -> str:
    return _fingerprint("generation-worker-output-schema@1", value)


def fingerprint_execution_inputs(value: JsonObject) -> str:
    return _fingerprint(_EXECUTION_INPUT_DOMAIN, freeze_object(value))


def fingerprint_output(value: JsonValue | None) -> str:
    return _fingerprint_json_value(_OUTPUT_DOMAIN, value)


def fingerprint_validations(value: tuple[ObservedValidationReceipt, ...]) -> str:
    return _fingerprint_json_value(_VALIDATORS_DOMAIN, tuple(item.to_json() for item in value))


def fingerprint_run(observation: ChildCapabilityObservation) -> str:
    return _fingerprint(
        _RUN_DOMAIN,
        freeze_object(
            {
                "run_id": str(observation.run_id),
                "definition_fingerprint": observation.definition_fingerprint,
                "pins": pins_to_json(observation.pins),
                "status": observation.status.value,
                "output_fingerprint": fingerprint_output(observation.output),
                "validator_fingerprint": fingerprint_validations(observation.validations),
                "prompt_fingerprint": observation.prompt.composition_fingerprint
                if observation.prompt
                else None,
            }
        ),
    )


def fingerprint_store_state(value: JsonObject) -> str:
    return _fingerprint(_STATE_DOMAIN, value)


def pins_to_json(pins: VersionPins) -> JsonObject:
    def ref(value: ArtifactReference) -> JsonObject:
        return freeze_object({"id": value.id, "version": str(value.version)})

    return freeze_object(
        {
            "skill": ref(pins.skill),
            "playbook": ref(pins.playbook),
            "prompt": ref(pins.prompt),
            "tool_behaviors": tuple(
                {"name": item.tool_name, "version": str(item.version)}
                for item in pins.tool_behaviors
            ),
            "model_adapter": ref(pins.model_adapter),
            "state_contract": ref(pins.state_contract),
        }
    )


def pins_from_json(value: Mapping[str, JsonValue]) -> VersionPins:
    _exact(
        value,
        {"skill", "playbook", "prompt", "tool_behaviors", "model_adapter", "state_contract"},
        "version pins",
    )

    def ref(name: str) -> ArtifactReference:
        raw = _mapping(value, name)
        _exact(raw, {"id", "version"}, f"{name} pin")
        return ArtifactReference(_string(raw, "id"), SemanticVersion.parse(_string(raw, "version")))

    behaviors: list[ToolBehaviorPin] = []
    for item in _array(value, "tool_behaviors"):
        raw = _as_mapping(item, "tool behavior pin")
        _exact(raw, {"name", "version"}, "tool behavior pin")
        behaviors.append(
            ToolBehaviorPin(_string(raw, "name"), SemanticVersion.parse(_string(raw, "version")))
        )
    return VersionPins(
        ref("skill"),
        ref("playbook"),
        ref("prompt"),
        tuple(behaviors),
        ref("model_adapter"),
        ref("state_contract"),
    )


_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "password",
        "credential",
        "credentials",
        "access_token",
        "refresh_token",
        "authorization",
        "principal",
        "principal_id",
        "principal_kind",
        "authority",
        "authority_fingerprint",
        "requested_capabilities",
        "grants",
        "course_id",
        "session_id",
        "correlation_id",
        "idempotency_key",
        "provider",
        "provider_id",
        "provider_name",
        "model",
        "model_id",
        "model_name",
        "messages",
        "message_history",
        "history",
        "conversation",
        "conversation_history",
        "canonical_decision",
        "decision",
        "artifact_id",
        "artifact_revision_id",
    }
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_FAILURE_CODE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
_FORBIDDEN_FAILURE_PARTS = frozenset(
    {
        "api",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "key",
        "password",
        "principal",
        "provider",
        "secret",
        "token",
    }
)
_PUBLIC_FAILURE_CODES = frozenset(
    {
        "cancelled",
        "capability_cancelled",
        "capability_failed",
        "capability_stale",
        "child_binding_mismatch",
        "child_continuation_mismatch",
        "child_prompt_provenance_invalid",
        "child_run_mismatch",
        "child_validation_provenance_invalid",
        "child_verified_run_provenance_invalid",
        "conflicting_evidence",
        "failed",
        "insufficient_evidence",
        "malformed_output",
        "rate_limited",
        "safe_failure",
        "gateway_authentication",
        "gateway_authorization",
        "gateway_model_unavailable",
        "gateway_endpoint_incompatible",
        "gateway_schema_incompatible",
        "gateway_rate_limited",
        "gateway_timeout",
        "gateway_protocol_error",
        "gateway_unavailable",
        "stale",
        "stale_generation",
        "terminated",
        "timeout",
        "unavailable",
        "validation_failed",
    }
)


def _normalize_structural_key(key: str) -> str:
    camel_split = _CAMEL_BOUNDARY.sub("_", key)
    return _NON_ALPHANUMERIC.sub("_", camel_split.lower()).strip("_")


def _reject_forbidden_keys(value: JsonValue, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalize_structural_key(key)
            if normalized in _FORBIDDEN_KEYS or normalized.endswith(
                ("_api_key", "_secret", "_password", "_credential", "_token")
            ):
                raise ValueError(f"{path} contains forbidden structural field {key!r}")
            _reject_forbidden_keys(item, f"{path}.{key}")
    elif isinstance(value, tuple):
        for index, item in enumerate(value):
            _reject_forbidden_keys(item, f"{path}[{index}]")


def _require_failure_code(value: str) -> None:
    _bounded_text(value, "failure_code", 64)
    if _FAILURE_CODE.fullmatch(value) is None:
        raise ValueError("failure_code must be a lowercase machine code")
    if (
        _FORBIDDEN_FAILURE_PARTS.intersection(value.split("_"))
        and value not in _PUBLIC_FAILURE_CODES
    ):
        raise ValueError("failure_code contains sensitive implementation metadata")


def _require_public_failure_code(value: str) -> None:
    _require_failure_code(value)
    if value not in _PUBLIC_FAILURE_CODES:
        raise ValueError("failure_code is not in the sanitized public vocabulary")


def sanitize_failure_code(value: str | None, fallback: str) -> str:
    """Collapse child-private terminal codes to a closed public vocabulary."""

    _require_public_failure_code(fallback)
    if value is None:
        return fallback
    try:
        _require_public_failure_code(value)
    except ValueError:
        return fallback
    return value


def _fingerprint(domain: str, value: JsonObject) -> str:
    return sha256(domain.encode() + b"\0" + canonical_json_bytes(value)).hexdigest()


def _fingerprint_json_value(domain: str, value: JsonValue) -> str:
    return _fingerprint(domain, freeze_object({"value": freeze_json(value)}))


def _require_size(value: JsonObject, maximum: int, name: str) -> None:
    if len(canonical_json_bytes(value)) > maximum:
        raise ValueError(f"{name} exceeds {maximum} bytes")


def _require_json_value_size(value: JsonValue, maximum: int, name: str) -> None:
    _require_size(freeze_object({"value": value}), maximum + len(b'{"value":}'), name)


def _decode_object(data: bytes, name: str) -> JsonObject:
    try:
        decoded: Any = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} bytes are invalid JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError(f"{name} must be a JSON object")
    return freeze_object(cast(dict[str, JsonValue], decoded))


def _exact(value: Mapping[str, JsonValue], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} fields must be exact")


def _mapping(value: Mapping[str, JsonValue], key: str) -> JsonObject:
    return _as_mapping(value.get(key), key)


def _optional_mapping(value: Mapping[str, JsonValue], key: str) -> JsonObject | None:
    raw = value.get(key)
    return None if raw is None else _as_mapping(raw, key)


def _as_mapping(value: JsonValue | None, name: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return freeze_object(value)


def _array(value: Mapping[str, JsonValue], key: str) -> tuple[JsonValue, ...]:
    raw = value.get(key)
    if not isinstance(raw, tuple):
        raise ValueError(f"{key} must be an array")
    return raw


def _string(value: Mapping[str, JsonValue], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise ValueError(f"{key} must be a string")
    return raw


def _optional_string(value: Mapping[str, JsonValue], key: str) -> str | None:
    raw = value.get(key)
    if raw is not None and not isinstance(raw, str):
        raise ValueError(f"{key} must be a string or null")
    return raw


def _string_tuple(value: Mapping[str, JsonValue], key: str) -> tuple[str, ...]:
    raw = _array(value, key)
    if not all(isinstance(item, str) for item in raw):
        raise ValueError(f"{key} must contain only strings")
    return cast(tuple[str, ...], raw)


def _bounded_text(value: str, name: str, maximum: int) -> None:
    require_text(value, name)
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")


def _bounded_unique_texts(
    values: Sequence[str], name: str, maximum_items: int, maximum_length: int
) -> tuple[str, ...]:
    items = tuple(values)
    if len(items) > maximum_items:
        raise ValueError(f"{name} contains too many entries")
    for item in items:
        if not isinstance(item, str):
            raise TypeError(f"{name} entries must be strings")
        _bounded_text(item, f"{name} entry", maximum_length)
    if len(set(items)) != len(items):
        raise ValueError(f"{name} entries must be unique")
    return items


def _require_sha256(value: str, name: str) -> None:
    require_text(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 fingerprint")
