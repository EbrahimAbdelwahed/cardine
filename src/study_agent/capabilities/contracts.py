"""Portable public contracts for trusted adaptive-tutor capabilities."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256

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
    ReadDependency,
    VerifiedRunRecord,
    VersionPins,
)
from study_agent.portability import (
    reject_provider_selector_name,
    reject_provider_selectors,
)
from study_agent.skills import SemanticVersion
from study_agent.tools.schema import validate_schema_definition


class TutorCapabilityId(StrEnum):
    EXPLAIN_CONCEPT = "explain_concept"
    ASSESS_UNDERSTANDING = "assess_understanding"
    PROPOSE_FLASHCARDS = "propose_flashcards"
    ANALYZE_EXAM_SAMPLE = "analyze_exam_sample"
    GRADE_RESPONSE = "grade_response"


class CapabilityOutcomeStatus(StrEnum):
    COMPLETED = "completed"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"
    CANCELLED = "cancelled"
    STALE = "stale"
    FAILED = "failed"


class CapabilityGatewayErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    IN_PROGRESS = "in_progress"
    INCOMPATIBLE_RUNTIME = "incompatible_runtime"


class CapabilityGatewayError(RuntimeError):
    def __init__(
        self,
        code: CapabilityGatewayErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        if not isinstance(code, CapabilityGatewayErrorCode):
            raise TypeError("gateway error code must use CapabilityGatewayErrorCode")
        require_text(message, "gateway error message")
        if retryable != (code is CapabilityGatewayErrorCode.IN_PROGRESS):
            raise ValueError("only in_progress gateway errors are retryable")
        self.code = code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    id: TutorCapabilityId
    version: SemanticVersion
    input_schema: JsonObject
    output_schema: JsonObject
    required_authority: tuple[str, ...]
    supports_suspension: bool

    def __post_init__(self) -> None:
        if not isinstance(self.id, TutorCapabilityId):
            raise TypeError("capability id must use the closed TutorCapabilityId vocabulary")
        if not isinstance(self.version, SemanticVersion):
            raise TypeError("capability version must be a SemanticVersion")
        if not isinstance(self.supports_suspension, bool):
            raise TypeError("supports_suspension must be boolean")

        input_schema = freeze_object(self.input_schema)
        output_schema = freeze_object(self.output_schema)
        validate_schema_definition(input_schema)
        validate_schema_definition(output_schema)
        reject_provider_selectors(input_schema, "input_schema")
        reject_provider_selectors(output_schema, "output_schema")
        object.__setattr__(self, "input_schema", input_schema)
        object.__setattr__(self, "output_schema", output_schema)

        authority = tuple(self.required_authority)
        if not authority:
            raise ValueError("required authority cannot be empty")
        for grant in authority:
            if not isinstance(grant, str):
                raise TypeError("required authority entries must be strings")
            require_text(grant, "required authority")
            reject_provider_selector_name(grant, "required authority")
        if len(set(authority)) != len(authority):
            raise ValueError("required authority entries must be unique")
        object.__setattr__(self, "required_authority", tuple(sorted(authority)))

    @property
    def identity(self) -> str:
        return f"{self.id.value}@{self.version.major}"

    def to_json(self) -> JsonObject:
        return {
            "id": self.id.value,
            "version": str(self.version),
            "identity": self.identity,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "required_authority": self.required_authority,
            "supports_suspension": self.supports_suspension,
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            _plain(self.to_json()),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return sha256(b"study-agent-capability-manifest-v1\0" + payload).hexdigest()


@dataclass(frozen=True, slots=True)
class CapabilityContinuation:
    run_id: RunId
    capability_id: TutorCapabilityId
    capability_version: SemanticVersion
    manifest_fingerprint: str
    authority_fingerprint: str
    retry_identity_fingerprint: str
    definition_fingerprint: str
    checkpoint_fingerprint: str
    dialogue_step_id: str
    next_step_index: int
    inputs: JsonObject
    pins: VersionPins
    read_dependencies: tuple[ReadDependency, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise TypeError("continuation run_id must be a RunId")
        if not isinstance(self.capability_id, TutorCapabilityId):
            raise TypeError("continuation capability_id must use TutorCapabilityId")
        if not isinstance(self.capability_version, SemanticVersion):
            raise TypeError("continuation capability_version must be SemanticVersion")
        for value, name in (
            (self.manifest_fingerprint, "manifest_fingerprint"),
            (self.authority_fingerprint, "authority_fingerprint"),
            (self.retry_identity_fingerprint, "retry_identity_fingerprint"),
            (self.definition_fingerprint, "definition_fingerprint"),
            (self.checkpoint_fingerprint, "checkpoint_fingerprint"),
        ):
            _require_sha256(value, name)
        require_text(self.dialogue_step_id, "dialogue_step_id")
        if type(self.next_step_index) is not int or self.next_step_index < 1:
            raise ValueError("continuation next_step_index must be positive")
        object.__setattr__(self, "inputs", freeze_object(self.inputs))
        if not isinstance(self.pins, VersionPins):
            raise TypeError("continuation pins must be VersionPins")
        dependencies = tuple(self.read_dependencies)
        if not all(isinstance(item, ReadDependency) for item in dependencies):
            raise TypeError("continuation dependencies must use ReadDependency")
        keys = tuple((item.kind, item.id) for item in dependencies)
        if len(set(keys)) != len(keys):
            raise ValueError("continuation dependencies must be unique by kind and id")
        object.__setattr__(self, "read_dependencies", dependencies)

    @property
    def fingerprint(self) -> str:
        return _fingerprint("study-agent-capability-continuation-v1", self.to_json())

    def to_json(self) -> JsonObject:
        return {
            "run_id": str(self.run_id),
            "capability_id": self.capability_id.value,
            "capability_version": str(self.capability_version),
            "manifest_fingerprint": self.manifest_fingerprint,
            "authority_fingerprint": self.authority_fingerprint,
            "retry_identity_fingerprint": self.retry_identity_fingerprint,
            "definition_fingerprint": self.definition_fingerprint,
            "checkpoint_fingerprint": self.checkpoint_fingerprint,
            "dialogue_step_id": self.dialogue_step_id,
            "next_step_index": self.next_step_index,
            "inputs": self.inputs,
            "pins": _pins_json(self.pins),
            "read_dependencies": tuple(
                {"kind": item.kind, "id": item.id, "version": item.version}
                for item in self.read_dependencies
            ),
        }


@dataclass(frozen=True, slots=True)
class CompletedCapabilityOutcome:
    run: VerifiedRunRecord
    output: JsonValue
    status: CapabilityOutcomeStatus = field(default=CapabilityOutcomeStatus.COMPLETED, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run, VerifiedRunRecord):
            raise TypeError("completed capability run must be VerifiedRunRecord")
        if self.run.status is not PlaybookRunStatus.COMPLETED:
            raise ValueError("completed capability outcomes require a completed verified run")
        object.__setattr__(self, "output", freeze_json(self.output))


@dataclass(frozen=True, slots=True)
class SuspendedCapabilityOutcome:
    run_id: RunId
    dialogue_request: str
    continuation: CapabilityContinuation
    response_schema: JsonObject
    status: CapabilityOutcomeStatus = field(default=CapabilityOutcomeStatus.SUSPENDED, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise TypeError("suspended outcome run_id must be RunId")
        if not isinstance(self.continuation, CapabilityContinuation):
            raise TypeError("suspended outcome continuation is invalid")
        if self.run_id != self.continuation.run_id:
            raise ValueError("suspended outcome and continuation run ids differ")
        require_text(self.dialogue_request, "dialogue_request")
        schema = freeze_object(self.response_schema)
        validate_schema_definition(schema)
        reject_provider_selectors(schema, "response_schema")
        object.__setattr__(self, "response_schema", schema)


@dataclass(frozen=True, slots=True)
class TerminatedCapabilityOutcome:
    run: VerifiedRunRecord
    status: CapabilityOutcomeStatus = field(default=CapabilityOutcomeStatus.TERMINATED, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run, VerifiedRunRecord):
            raise TypeError("terminated capability run must be VerifiedRunRecord")
        if self.run.status is not PlaybookRunStatus.TERMINATED:
            raise ValueError("terminated capability outcomes require a terminated verified run")


@dataclass(frozen=True, slots=True)
class CancelledCapabilityOutcome:
    run_id: RunId
    message: str
    status: CapabilityOutcomeStatus = field(default=CapabilityOutcomeStatus.CANCELLED, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise TypeError("cancelled outcome run_id must be RunId")
        require_text(self.message, "cancelled outcome message")


@dataclass(frozen=True, slots=True)
class StaleCapabilityOutcome:
    run_id: RunId
    message: str
    status: CapabilityOutcomeStatus = field(default=CapabilityOutcomeStatus.STALE, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise TypeError("stale outcome run_id must be RunId")
        require_text(self.message, "stale outcome message")


@dataclass(frozen=True, slots=True)
class FailedCapabilityOutcome:
    run_id: RunId
    message: str
    failure_reason: str | None = None
    status: CapabilityOutcomeStatus = field(default=CapabilityOutcomeStatus.FAILED, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise TypeError("failed outcome run_id must be RunId")
        require_text(self.message, "failed outcome message")
        if self.failure_reason is not None and self.failure_reason not in {
            "authentication",
            "authorization",
            "model_unavailable",
            "endpoint_incompatible",
            "schema_incompatible",
            "rate_limited",
            "timeout",
            "protocol_error",
            "unavailable",
            "capability_execution_failed",
            "capability_validation_failed",
            "scope_missing",
            "scope_stale",
            "publication_failed",
        }:
            raise ValueError("failed outcome failure reason is invalid")


type CapabilityOutcome = (
    CompletedCapabilityOutcome
    | SuspendedCapabilityOutcome
    | TerminatedCapabilityOutcome
    | CancelledCapabilityOutcome
    | StaleCapabilityOutcome
    | FailedCapabilityOutcome
)


def _plain(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _require_sha256(value: str, name: str) -> None:
    require_text(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _fingerprint(domain: str, value: JsonObject) -> str:
    payload = json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256(domain.encode("utf-8") + b"\0" + payload).hexdigest()


def _pins_json(pins: VersionPins) -> JsonObject:
    return {
        "skill": {"id": pins.skill.id, "version": str(pins.skill.version)},
        "playbook": {"id": pins.playbook.id, "version": str(pins.playbook.version)},
        "prompt": {"id": pins.prompt.id, "version": str(pins.prompt.version)},
        "tool_behaviors": tuple(
            {"name": item.tool_name, "version": str(item.version)} for item in pins.tool_behaviors
        ),
        "model_adapter": {
            "id": pins.model_adapter.id,
            "version": str(pins.model_adapter.version),
        },
        "state_contract": {
            "id": pins.state_contract.id,
            "version": str(pins.state_contract.version),
        },
    }
