"""Provider-neutral, effect-free contracts for a bounded tutor host."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import cast

from study_agent.domain._validation import JsonObject, JsonValue, freeze_json, freeze_object
from study_agent.domain.identifiers import RunId
from study_agent.domain.session import (
    MAX_TUTOR_PRESENTATION_QUESTION,
    MAX_TUTOR_PRESENTATION_SCHEMA_BYTES,
    MAX_TUTOR_PRESENTATION_TEXT,
    TutorPresentationKind,
)
from study_agent.portability import reject_provider_selectors

HOST_CONTEXT_SCHEMA_VERSION = 1
MAX_HOST_FILES = 16
MAX_HOST_TEXT = 4_000
MAX_QUESTION_TEXT = 1_000


class TutorDecisionKind(StrEnum):
    START_CAPABILITY = "start_capability"
    ANSWER_DIALOGUE = "answer_dialogue"
    ASK_LEARNER = "ask_learner"
    ASSISTANT_MESSAGE = "assistant_message"
    INVOKE_TOOL = "invoke_tool"
    STOP = "stop"


class TutorStopReason(StrEnum):
    COMPLETED = "completed"
    NO_SAFE_ACTION = "no_safe_action"


@dataclass(frozen=True, slots=True)
class AdvertisedCapability:
    id: str
    identity: str
    manifest_fingerprint: str
    input_schema: JsonObject
    supports_suspension: bool

    def __post_init__(self) -> None:
        _require_bounded_text(self.id, "advertised capability id", 128)
        _require_bounded_text(self.identity, "capability identity", 128)
        if not self.identity.startswith(f"{self.id}@"):
            raise ValueError("capability identity and id disagree")
        _require_sha256(self.manifest_fingerprint, "manifest_fingerprint")
        schema = freeze_object(self.input_schema)
        _validate_schema_definition(schema)
        reject_provider_selectors(schema, "input_schema")
        object.__setattr__(self, "input_schema", schema)
        if not isinstance(self.supports_suspension, bool):
            raise TypeError("supports_suspension must be boolean")

    def to_json(self) -> JsonObject:
        return {
            "id": self.id,
            "identity": self.identity,
            "manifest_fingerprint": self.manifest_fingerprint,
            "input_schema": self.input_schema,
            "supports_suspension": self.supports_suspension,
        }


@dataclass(frozen=True, slots=True)
class PendingContinuationDescriptor:
    fingerprint: str
    capability_identity: str
    dialogue_step_id: str
    dialogue_request: str
    response_schema: JsonObject

    def __post_init__(self) -> None:
        _require_sha256(self.fingerprint, "continuation fingerprint")
        _require_bounded_text(self.capability_identity, "capability identity", 128)
        _require_bounded_text(self.dialogue_step_id, "dialogue step id", 128)
        _require_bounded_text(self.dialogue_request, "dialogue request", MAX_QUESTION_TEXT)
        schema = freeze_object(self.response_schema)
        _validate_schema_definition(schema)
        reject_provider_selectors(schema, "response_schema")
        object.__setattr__(self, "response_schema", schema)

    def to_json(self) -> JsonObject:
        return {
            "fingerprint": self.fingerprint,
            "capability_identity": self.capability_identity,
            "dialogue_step_id": self.dialogue_step_id,
            "dialogue_request": self.dialogue_request,
            "response_schema": self.response_schema,
        }


@dataclass(frozen=True, slots=True)
class HostFileDescriptor:
    id: str
    display_name: str
    media_type: str
    byte_size: int
    checksum_sha256: str

    def __post_init__(self) -> None:
        _require_opaque(self.id, "host file id")
        _require_bounded_text(self.display_name, "display name", 255)
        if (
            "/" in self.display_name
            or "\\" in self.display_name
            or self.display_name in {".", ".."}
        ):
            raise ValueError("display name cannot contain a path")
        _require_bounded_text(self.media_type, "media type", 128)
        if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", self.media_type):
            raise ValueError("media type must be canonical")
        if type(self.byte_size) is not int or self.byte_size < 0:
            raise ValueError("byte_size must be non-negative")
        _require_sha256(self.checksum_sha256, "checksum_sha256")

    def to_json(self) -> JsonObject:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "media_type": self.media_type,
            "byte_size": self.byte_size,
            "checksum_sha256": self.checksum_sha256,
        }


@dataclass(frozen=True, slots=True)
class TutorHostContext:
    course_id: str
    session_id: str
    tutor_snapshot_sequence: int
    learner_evidence_through_sequence: int
    tutor_snapshot: JsonObject
    learner_evidence: JsonObject
    advertised_capabilities: tuple[AdvertisedCapability, ...]
    pending_continuation: PendingContinuationDescriptor | None = None
    host_files: tuple[HostFileDescriptor, ...] = ()
    schema_version: int = HOST_CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_opaque(self.course_id, "course id")
        _require_opaque(self.session_id, "session id")
        if self.schema_version != HOST_CONTEXT_SCHEMA_VERSION:
            raise ValueError("unsupported tutor host context schema version")
        for value, name in (
            (self.tutor_snapshot_sequence, "tutor snapshot sequence"),
            (self.learner_evidence_through_sequence, "learner evidence sequence"),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.tutor_snapshot_sequence != self.learner_evidence_through_sequence:
            raise ValueError("tutor snapshot and learner evidence are not sequence-consistent")
        tutor = freeze_object(self.tutor_snapshot)
        evidence = freeze_object(self.learner_evidence)
        _reject_sensitive_structure(tutor, "tutor_snapshot")
        _reject_sensitive_structure(evidence, "learner_evidence")
        object.__setattr__(self, "tutor_snapshot", tutor)
        object.__setattr__(self, "learner_evidence", evidence)
        advertised = tuple(self.advertised_capabilities)
        keys = tuple((item.identity, item.manifest_fingerprint) for item in advertised)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("advertised capabilities must be unique and canonically ordered")
        if len({item.id for item in advertised}) != len(advertised):
            raise ValueError("advertised capability ids must be unique")
        object.__setattr__(self, "advertised_capabilities", advertised)
        files = tuple(self.host_files)
        if len(files) > MAX_HOST_FILES:
            raise ValueError("too many host files")
        file_keys = tuple((item.id, item.checksum_sha256) for item in files)
        if file_keys != tuple(sorted(file_keys)) or len(set(file_keys)) != len(file_keys):
            raise ValueError("host files must be unique and canonically ordered")
        object.__setattr__(self, "host_files", files)
        if self.pending_continuation is not None:
            identities = {item.identity for item in advertised}
            if self.pending_continuation.capability_identity not in identities:
                raise ValueError("pending continuation capability is not advertised")

    def to_json(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "course_id": self.course_id,
            "session_id": self.session_id,
            "tutor_snapshot_sequence": self.tutor_snapshot_sequence,
            "learner_evidence_through_sequence": self.learner_evidence_through_sequence,
            "tutor_snapshot": self.tutor_snapshot,
            "learner_evidence": self.learner_evidence,
            "advertised_capabilities": tuple(
                item.to_json() for item in self.advertised_capabilities
            ),
            "pending_continuation": (
                None if self.pending_continuation is None else self.pending_continuation.to_json()
            ),
            "host_files": tuple(item.to_json() for item in self.host_files),
        }

    @property
    def fingerprint(self) -> str:
        return _fingerprint("study-agent-tutor-host-context-v1", self.to_json())

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_bytes(cls, data: bytes) -> TutorHostContext:
        raw = _canonical_object(data, "tutor host context")
        _exact(
            raw,
            {
                "schema_version",
                "course_id",
                "session_id",
                "tutor_snapshot_sequence",
                "learner_evidence_through_sequence",
                "tutor_snapshot",
                "learner_evidence",
                "advertised_capabilities",
                "pending_continuation",
                "host_files",
            },
            "tutor host context",
        )
        advertised = tuple(
            _advertised_from_json(item)
            for item in _array(raw["advertised_capabilities"], "advertised_capabilities")
        )
        pending_raw = raw["pending_continuation"]
        pending = (
            None
            if pending_raw is None
            else _pending_from_json(_object(pending_raw, "pending_continuation"))
        )
        files = tuple(_file_from_json(item) for item in _array(raw["host_files"], "host_files"))
        return cls(
            _string(raw, "course_id"),
            _string(raw, "session_id"),
            _integer(raw, "tutor_snapshot_sequence"),
            _integer(raw, "learner_evidence_through_sequence"),
            _object(raw["tutor_snapshot"], "tutor_snapshot"),
            _object(raw["learner_evidence"], "learner_evidence"),
            advertised,
            pending,
            files,
            _integer(raw, "schema_version"),
        )


@dataclass(frozen=True, slots=True)
class StartCapabilityDecision:
    capability_id: str
    inputs: JsonObject
    kind: TutorDecisionKind = field(default=TutorDecisionKind.START_CAPABILITY, init=False)

    def __post_init__(self) -> None:
        _require_bounded_text(self.capability_id, "capability id", 128)
        value = freeze_object(self.inputs)
        _reject_sensitive_structure(value, "inputs")
        _reject_start_authority_structure(value, "inputs")
        object.__setattr__(self, "inputs", value)


@dataclass(frozen=True, slots=True)
class AnswerDialogueDecision:
    continuation_fingerprint: str
    response: JsonValue
    kind: TutorDecisionKind = field(default=TutorDecisionKind.ANSWER_DIALOGUE, init=False)

    def __post_init__(self) -> None:
        _require_sha256(self.continuation_fingerprint, "continuation fingerprint")
        value = freeze_json(self.response)
        _reject_sensitive_structure(value, "response")
        object.__setattr__(self, "response", value)


@dataclass(frozen=True, slots=True)
class AskLearnerDecision:
    question: str
    kind: TutorDecisionKind = field(default=TutorDecisionKind.ASK_LEARNER, init=False)

    def __post_init__(self) -> None:
        _require_bounded_text(self.question, "question", MAX_QUESTION_TEXT)


@dataclass(frozen=True, slots=True)
class AssistantMessageDecision:
    message: str
    kind: TutorDecisionKind = field(default=TutorDecisionKind.ASSISTANT_MESSAGE, init=False)

    def __post_init__(self) -> None:
        _require_bounded_text(self.message, "assistant message", MAX_HOST_TEXT)


@dataclass(frozen=True, slots=True)
class InvokeToolDecision:
    """A model-selected operation, executed only by the trusted host runner."""

    tool_name: str
    arguments: JsonObject
    kind: TutorDecisionKind = field(default=TutorDecisionKind.INVOKE_TOOL, init=False)

    def __post_init__(self) -> None:
        _require_bounded_text(self.tool_name, "tool name", 128)
        object.__setattr__(self, "arguments", freeze_object(self.arguments))


@dataclass(frozen=True, slots=True)
class StopDecision:
    reason: TutorStopReason
    kind: TutorDecisionKind = field(default=TutorDecisionKind.STOP, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.reason, TutorStopReason):
            raise TypeError("stop reason is invalid")


type TutorDecision = (
    StartCapabilityDecision
    | AnswerDialogueDecision
    | AskLearnerDecision
    | AssistantMessageDecision
    | InvokeToolDecision
    | StopDecision
)


@dataclass(frozen=True, slots=True)
class HostActionIdentity:
    value: str

    def __post_init__(self) -> None:
        _require_opaque(self.value, "host action identity")

    @property
    def fingerprint(self) -> str:
        return sha256(b"study-agent-host-action-identity-v1\0" + self.value.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class HostRetryReceipt:
    host_turn_id: str
    action_identity_fingerprint: str
    context_fingerprint: str
    action_fingerprint: str
    decision_generation: int
    attempt: int

    SCHEMA_VERSION = 2

    def __post_init__(self) -> None:
        _require_opaque(self.host_turn_id, "host_turn_id")
        for value, name in (
            (self.action_identity_fingerprint, "action_identity_fingerprint"),
            (self.context_fingerprint, "context_fingerprint"),
            (self.action_fingerprint, "action_fingerprint"),
        ):
            _require_sha256(value, name)
        if type(self.decision_generation) is not int or self.decision_generation < 1:
            raise ValueError("decision_generation must be positive")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be positive")

    def to_json(self) -> JsonObject:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "host_turn_id": self.host_turn_id,
            "action_identity_fingerprint": self.action_identity_fingerprint,
            "context_fingerprint": self.context_fingerprint,
            "action_fingerprint": self.action_fingerprint,
            "decision_generation": self.decision_generation,
            "attempt": self.attempt,
        }

    @property
    def fingerprint(self) -> str:
        return _fingerprint("study-agent-host-retry-receipt-v2", self.to_json())

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_bytes(cls, data: bytes) -> HostRetryReceipt:
        raw = _canonical_object(data, "host retry receipt")
        _exact(
            raw,
            {
                "schema_version",
                "host_turn_id",
                "action_identity_fingerprint",
                "context_fingerprint",
                "action_fingerprint",
                "decision_generation",
                "attempt",
            },
            "host retry receipt",
        )
        if _integer(raw, "schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported host retry receipt schema version")
        return cls(
            _string(raw, "host_turn_id"),
            _string(raw, "action_identity_fingerprint"),
            _string(raw, "context_fingerprint"),
            _string(raw, "action_fingerprint"),
            _integer(raw, "decision_generation"),
            _integer(raw, "attempt"),
        )


@dataclass(frozen=True, slots=True)
class TutorCapabilityCompletionReference:
    """Closed proof reference for one authority-verified capability completion.

    The reference deliberately carries no output.  Product handlers must use
    this identity to recover the verified owner result before creating any
    canonical product effect or learner-visible presentation.
    """

    capability_identity: str
    manifest_fingerprint: str
    run_id: RunId
    output_fingerprint: str
    retry_receipt_fingerprint: str

    def __post_init__(self) -> None:
        _require_bounded_text(self.capability_identity, "capability identity", 128)
        if "@" not in self.capability_identity:
            raise ValueError("capability identity must include a version")
        _require_sha256(self.manifest_fingerprint, "manifest_fingerprint")
        if not isinstance(self.run_id, RunId):
            raise TypeError("completion run_id must be RunId")
        _require_sha256(self.output_fingerprint, "output_fingerprint")
        _require_sha256(self.retry_receipt_fingerprint, "retry_receipt_fingerprint")

    def to_json(self) -> JsonObject:
        return {
            "capability_identity": self.capability_identity,
            "manifest_fingerprint": self.manifest_fingerprint,
            "run_id": str(self.run_id),
            "output_fingerprint": self.output_fingerprint,
            "retry_receipt_fingerprint": self.retry_receipt_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class TutorPresentationReceipt:
    """Closed proof emitted by a trusted host or application settlement boundary."""

    host_turn_id: str
    kind: TutorPresentationKind
    content: str
    observed_host_context_sequence: int
    host_context_fingerprint: str
    decision_fingerprint: str
    continuation_fingerprint: str | None = None
    capability_identity: str | None = None
    response_schema: JsonObject | None = None

    SCHEMA_VERSION = 1

    def __post_init__(self) -> None:
        _require_opaque(self.host_turn_id, "host_turn_id")
        if not isinstance(self.kind, TutorPresentationKind):
            raise TypeError("presentation kind is invalid")
        maximum = (
            MAX_TUTOR_PRESENTATION_QUESTION
            if self.kind
            in {
                TutorPresentationKind.LEARNER_QUESTION,
                TutorPresentationKind.CONTINUATION_REQUEST,
            }
            else MAX_TUTOR_PRESENTATION_TEXT
        )
        _require_bounded_text(self.content, "presentation content", maximum)
        if (
            type(self.observed_host_context_sequence) is not int
            or self.observed_host_context_sequence < 0
        ):
            raise ValueError("observed_host_context_sequence must be non-negative")
        _require_sha256(self.host_context_fingerprint, "host_context_fingerprint")
        _require_sha256(self.decision_fingerprint, "decision_fingerprint")
        if self.continuation_fingerprint is not None:
            _require_sha256(self.continuation_fingerprint, "continuation_fingerprint")
        if self.kind is TutorPresentationKind.CONTINUATION_REQUEST:
            if self.continuation_fingerprint is None or self.capability_identity is None:
                raise ValueError("continuation receipt requires continuation identity")
            _require_bounded_text(self.capability_identity, "capability_identity", 128)
            if self.response_schema is None:
                raise ValueError("continuation receipt requires response schema")
        elif (
            self.continuation_fingerprint is not None
            or self.capability_identity is not None
            or self.response_schema is not None
        ):
            raise ValueError("only continuation receipts carry continuation descriptors")
        if self.response_schema is not None:
            schema = freeze_object(self.response_schema)
            _validate_schema_definition(schema)
            reject_provider_selectors(schema, "response_schema")
            if len(_canonical_bytes(schema)) > MAX_TUTOR_PRESENTATION_SCHEMA_BYTES:
                raise ValueError("response schema exceeds presentation bounds")
            object.__setattr__(self, "response_schema", schema)

    @property
    def fingerprint(self) -> str:
        return _fingerprint("study-agent-tutor-presentation-receipt-v1", self.to_json())

    def to_json(self) -> JsonObject:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "host_turn_id": self.host_turn_id,
            "kind": self.kind.value,
            "content": self.content,
            "observed_host_context_sequence": self.observed_host_context_sequence,
            "host_context_fingerprint": self.host_context_fingerprint,
            "decision_fingerprint": self.decision_fingerprint,
            "continuation_fingerprint": self.continuation_fingerprint,
            "capability_identity": self.capability_identity,
            "response_schema": self.response_schema,
        }

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_bytes(cls, data: bytes) -> TutorPresentationReceipt:
        raw = _canonical_object(data, "tutor presentation receipt")
        _exact(
            raw,
            {
                "schema_version",
                "host_turn_id",
                "kind",
                "content",
                "observed_host_context_sequence",
                "host_context_fingerprint",
                "decision_fingerprint",
                "continuation_fingerprint",
                "capability_identity",
                "response_schema",
            },
            "tutor presentation receipt",
        )
        if _integer(raw, "schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported tutor presentation receipt schema version")
        kind = TutorPresentationKind(_string(raw, "kind"))
        schema_raw = raw["response_schema"]
        schema = None if schema_raw is None else _object(schema_raw, "response_schema")
        receipt = cls(
            _string(raw, "host_turn_id"),
            kind,
            _string(raw, "content"),
            _integer(raw, "observed_host_context_sequence"),
            _string(raw, "host_context_fingerprint"),
            _string(raw, "decision_fingerprint"),
            _optional_string(raw, "continuation_fingerprint"),
            _optional_string(raw, "capability_identity"),
            schema,
        )
        if receipt.to_bytes() != data:
            raise ValueError("tutor presentation receipt is not semantically canonical")
        return receipt


def decision_to_json(decision: TutorDecision) -> JsonObject:
    if isinstance(decision, StartCapabilityDecision):
        return {
            "kind": decision.kind.value,
            "capability_id": decision.capability_id,
            "inputs": decision.inputs,
        }
    if isinstance(decision, AnswerDialogueDecision):
        return {
            "kind": decision.kind.value,
            "continuation_fingerprint": decision.continuation_fingerprint,
            "response": decision.response,
        }
    if isinstance(decision, AskLearnerDecision):
        return {"kind": decision.kind.value, "question": decision.question}
    if isinstance(decision, AssistantMessageDecision):
        return {"kind": decision.kind.value, "message": decision.message}
    if isinstance(decision, InvokeToolDecision):
        return {
            "kind": decision.kind.value,
            "tool_name": decision.tool_name,
            "arguments": decision.arguments,
        }
    if isinstance(decision, StopDecision):
        return {"kind": decision.kind.value, "reason": decision.reason.value}
    raise TypeError("unsupported tutor decision")


def decision_schema(context: TutorHostContext) -> JsonObject:
    """Build the exact provider-neutral structured decision schema for ``context``.

    Responses structured outputs require an object root.  Every branch is closed
    and is derived solely from the already-redacted context; capability and
    dialogue branches are emitted only when the context advertises them.
    """

    branches: list[JsonObject] = [
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": (TutorDecisionKind.ASK_LEARNER.value,)},
                "question": {"type": "string", "minLength": 1, "maxLength": MAX_QUESTION_TEXT},
            },
            "required": ("kind", "question"),
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": (TutorDecisionKind.ASSISTANT_MESSAGE.value,),
                },
                "message": {"type": "string", "minLength": 1, "maxLength": MAX_HOST_TEXT},
            },
            "required": ("kind", "message"),
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": (TutorDecisionKind.STOP.value,)},
                "reason": {
                    "type": "string",
                    "enum": tuple(reason.value for reason in TutorStopReason),
                },
            },
            "required": ("kind", "reason"),
            "additionalProperties": False,
        },
    ]
    for capability in context.advertised_capabilities:
        branches.append(
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": (TutorDecisionKind.START_CAPABILITY.value,),
                    },
                    "capability_id": {"type": "string", "enum": (capability.id,)},
                    "inputs": capability.input_schema,
                },
                "required": ("kind", "capability_id", "inputs"),
                "additionalProperties": False,
            }
        )
    for tool in _context_tools(context):
        branches.append(
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": (TutorDecisionKind.INVOKE_TOOL.value,)},
                    "tool_name": {"type": "string", "enum": (tool["name"],)},
                    "arguments": tool["input_schema"],
                },
                "required": ("kind", "tool_name", "arguments"),
                "additionalProperties": False,
            }
        )
    pending = context.pending_continuation
    if pending is not None:
        branches = [
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": (TutorDecisionKind.ANSWER_DIALOGUE.value,),
                    },
                    "continuation_fingerprint": {
                        "type": "string",
                        "enum": (pending.fingerprint,),
                    },
                    "response": pending.response_schema,
                },
                "required": ("kind", "continuation_fingerprint", "response"),
                "additionalProperties": False,
            }
        ]
    return {
        "type": "object",
        "properties": {"decision": {"anyOf": tuple(branches)}},
        "required": ("decision",),
        "additionalProperties": False,
    }


def decision_fingerprint(decision: TutorDecision) -> str:
    return _fingerprint("study-agent-tutor-decision-v1", decision_to_json(decision))


def decision_to_bytes(decision: TutorDecision) -> bytes:
    return _canonical_bytes(decision_to_json(decision))


def decision_from_bytes(data: bytes, context: TutorHostContext) -> TutorDecision:
    raw = _canonical_object(data, "tutor decision")
    kind = TutorDecisionKind(_string(raw, "kind"))
    if kind is TutorDecisionKind.START_CAPABILITY:
        _exact(raw, {"kind", "capability_id", "inputs"}, "start decision")
        decision: TutorDecision = StartCapabilityDecision(
            _string(raw, "capability_id"),
            _object(raw["inputs"], "inputs"),
        )
    elif kind is TutorDecisionKind.ANSWER_DIALOGUE:
        _exact(raw, {"kind", "continuation_fingerprint", "response"}, "dialogue decision")
        decision = AnswerDialogueDecision(_string(raw, "continuation_fingerprint"), raw["response"])
    elif kind is TutorDecisionKind.ASK_LEARNER:
        _exact(raw, {"kind", "question"}, "ask decision")
        decision = AskLearnerDecision(_string(raw, "question"))
    elif kind is TutorDecisionKind.ASSISTANT_MESSAGE:
        _exact(raw, {"kind", "message"}, "message decision")
        decision = AssistantMessageDecision(_string(raw, "message"))
    elif kind is TutorDecisionKind.INVOKE_TOOL:
        _exact(raw, {"kind", "tool_name", "arguments"}, "tool decision")
        decision = InvokeToolDecision(
            _string(raw, "tool_name"), _object(raw["arguments"], "arguments")
        )
    else:
        _exact(raw, {"kind", "reason"}, "stop decision")
        decision = StopDecision(TutorStopReason(_string(raw, "reason")))
    validate_decision(decision, context)
    return decision


def validate_decision(decision: TutorDecision, context: TutorHostContext) -> None:
    if context.pending_continuation is not None and not isinstance(
        decision, AnswerDialogueDecision
    ):
        raise ValueError("pending continuation requires an exact dialogue answer decision")
    if isinstance(decision, StartCapabilityDecision):
        descriptor = next(
            (item for item in context.advertised_capabilities if item.id == decision.capability_id),
            None,
        )
        if descriptor is None:
            raise ValueError("decision names an unadvertised capability")
        try:
            _validate_json(decision.inputs, descriptor.input_schema)
        except ValueError as error:
            raise ValueError("decision inputs violate the advertised schema") from error
    elif isinstance(decision, AnswerDialogueDecision):
        pending = context.pending_continuation
        if pending is None or decision.continuation_fingerprint != pending.fingerprint:
            raise ValueError("decision does not bind the exact pending continuation")
        try:
            _validate_json(decision.response, pending.response_schema)
        except ValueError as error:
            raise ValueError("dialogue response violates the pending schema") from error
    elif isinstance(decision, InvokeToolDecision):
        tool_descriptor = next(
            (item for item in _context_tools(context) if item["name"] == decision.tool_name), None
        )
        if tool_descriptor is None:
            raise ValueError("decision names an unadvertised harness tool")
        try:
            _validate_json(
                decision.arguments, cast(JsonObject, tool_descriptor["input_schema"])
            )
        except ValueError as error:
            raise ValueError("tool arguments violate the advertised schema") from error


def _context_tools(context: TutorHostContext) -> tuple[JsonObject, ...]:
    raw = context.tutor_snapshot.get("harness_tools", ())
    if not isinstance(raw, tuple):
        return ()
    values: list[JsonObject] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        try:
            name = _string(item, "name")
            schema = _object(item["input_schema"], "input_schema")
            _validate_schema_definition(schema)
        except (KeyError, TypeError, ValueError):
            continue
        values.append({"name": name, "input_schema": schema})
    return tuple(values)


def _advertised_from_json(value: JsonValue) -> AdvertisedCapability:
    raw = _object(value, "advertised capability")
    _exact(
        raw,
        {"id", "identity", "manifest_fingerprint", "input_schema", "supports_suspension"},
        "advertised capability",
    )
    suspension = raw["supports_suspension"]
    if not isinstance(suspension, bool):
        raise ValueError("supports_suspension must be boolean")
    return AdvertisedCapability(
        _string(raw, "id"),
        _string(raw, "identity"),
        _string(raw, "manifest_fingerprint"),
        _object(raw["input_schema"], "input_schema"),
        suspension,
    )


def _pending_from_json(raw: JsonObject) -> PendingContinuationDescriptor:
    _exact(
        raw,
        {
            "fingerprint",
            "capability_identity",
            "dialogue_step_id",
            "dialogue_request",
            "response_schema",
        },
        "pending continuation",
    )
    return PendingContinuationDescriptor(
        _string(raw, "fingerprint"),
        _string(raw, "capability_identity"),
        _string(raw, "dialogue_step_id"),
        _string(raw, "dialogue_request"),
        _object(raw["response_schema"], "response_schema"),
    )


def _file_from_json(value: JsonValue) -> HostFileDescriptor:
    raw = _object(value, "host file")
    _exact(raw, {"id", "display_name", "media_type", "byte_size", "checksum_sha256"}, "host file")
    return HostFileDescriptor(
        _string(raw, "id"),
        _string(raw, "display_name"),
        _string(raw, "media_type"),
        _integer(raw, "byte_size"),
        _string(raw, "checksum_sha256"),
    )


_SENSITIVE_TOKENS = frozenset(
    {
        "api_key",
        "credential",
        "secret",
        "password",
        "principal",
        "grant",
        "authority",
        "correlation",
        "idempotency",
        "retry",
        "provider",
        "vendor",
        "model",
        "endpoint",
        "path",
        "rubric",
        "expected_response",
        "raw_prompt",
        "trace",
    }
)

_START_AUTHORITY_TOKENS = frozenset({"repository", "course_id", "session_id", "principal_id"})


def _reject_sensitive_structure(value: JsonValue, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
            if normalized in _SENSITIVE_TOKENS or any(
                normalized.endswith(f"_{token}") for token in _SENSITIVE_TOKENS
            ):
                raise ValueError(f"{path}.{key} is not model-visible")
            _reject_sensitive_structure(item, f"{path}.{key}")
    elif isinstance(value, tuple):
        for index, item in enumerate(value):
            _reject_sensitive_structure(item, f"{path}[{index}]")


def _reject_start_authority_structure(value: JsonValue, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
            if normalized in _START_AUTHORITY_TOKENS:
                raise ValueError(f"{path}.{key} is trusted-host authority")
            _reject_start_authority_structure(item, f"{path}.{key}")
    elif isinstance(value, tuple):
        for index, item in enumerate(value):
            _reject_start_authority_structure(item, f"{path}[{index}]")


def _validate_schema_definition(schema: JsonObject) -> None:
    # Deferred to avoid making the neutral port package depend on the tools package
    # while its own public exports are still initializing.
    from study_agent.tools.schema import validate_schema_definition

    validate_schema_definition(schema)


def _validate_json(value: JsonValue, schema: JsonObject) -> None:
    from study_agent.tools.schema import validate_json

    validate_json(value, schema)


def _require_opaque(value: str, name: str) -> None:
    _require_bounded_text(value, name, 256)
    if "/" in value or "\\" in value or value in {".", ".."} or "://" in value:
        raise ValueError(f"{name} must be opaque and path-free")


def _require_bounded_text(value: str, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be bounded non-blank trimmed text")


def _require_sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{name} must be a lowercase SHA-256")


def _canonical_bytes(value: JsonObject) -> bytes:
    return json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _fingerprint(domain: str, value: JsonObject) -> str:
    return sha256(domain.encode() + b"\0" + _canonical_bytes(value)).hexdigest()


def _canonical_object(data: bytes, name: str) -> JsonObject:
    try:
        decoded = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} is not canonical JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError(f"{name} must be a JSON object")
    value = freeze_object(cast(JsonObject, decoded))
    if _canonical_bytes(value) != data:
        raise ValueError(f"{name} bytes are not canonical")
    return value


def _plain(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _object(value: JsonValue, name: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: JsonValue, name: str) -> tuple[JsonValue, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be an array")
    return value


def _string(value: Mapping[str, JsonValue], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string")
    return item


def _optional_string(value: Mapping[str, JsonValue], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string or null")
    return item


def _integer(value: Mapping[str, JsonValue], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise ValueError(f"{key} must be an integer")
    return item


def _exact(value: Mapping[str, JsonValue], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{name} has an invalid field set")


__all__ = [
    "HOST_CONTEXT_SCHEMA_VERSION",
    "AdvertisedCapability",
    "AnswerDialogueDecision",
    "AskLearnerDecision",
    "AssistantMessageDecision",
    "HostActionIdentity",
    "HostFileDescriptor",
    "HostRetryReceipt",
    "InvokeToolDecision",
    "PendingContinuationDescriptor",
    "StartCapabilityDecision",
    "StopDecision",
    "TutorDecision",
    "TutorDecisionKind",
    "TutorHostContext",
    "TutorPresentationKind",
    "TutorPresentationReceipt",
    "TutorStopReason",
    "decision_fingerprint",
    "decision_from_bytes",
    "decision_schema",
    "decision_to_bytes",
    "decision_to_json",
    "validate_decision",
]
