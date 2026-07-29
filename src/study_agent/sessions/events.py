"""Strict schema-v1 codecs for canonical session events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.events import Actor, DomainEvent, PrincipalKind
from study_agent.domain.grounding import (
    AnswerSegment,
    AnswerStatus,
    GroundedAnswer,
    SegmentKind,
)
from study_agent.domain.identifiers import (
    AnswerId,
    ChunkId,
    CorrelationId,
    CourseId,
    EventId,
    InteractionId,
    RevisionId,
    RunId,
    SessionId,
    SourceId,
    TutorPresentationId,
    assistant_interaction_id_for,
    session_turn_event_id_for,
    tutor_presentation_id_for,
)
from study_agent.domain.provenance import (
    AnswerProvenance,
    ClaimOrigin,
    ModelProvenance,
    ModelUsageProvenance,
    PromptProvenance,
    RetrievalProvenance,
    SourceCommitment,
    ValidatorProvenance,
    VersionPins,
)
from study_agent.domain.session import (
    MAX_TUTOR_PRESENTATION_SCHEMA_BYTES,
    AnswerRecord,
    AssistantTurnRecord,
    AssistantTurnStatus,
    ContinuationSummaryV1,
    InteractionKind,
    SummaryExchange,
    TutorPresentationKind,
    TutorPresentationRecord,
    VerifiedRunOutputRef,
)
from study_agent.domain.source import Citation
from study_agent.portability import reject_provider_selectors
from study_agent.state import canonical_json_bytes
from study_agent.tools.schema import validate_schema_definition

SESSION_SCHEMA_VERSION = 1
SESSION_STARTED = "session.started"
SESSION_INTERACTION_RECORDED = "session.interaction_recorded"
SESSION_ANSWER_RECORDED = "session.answer_recorded"
SESSION_ASSISTANT_TURN_RECORDED = "session.assistant_turn_recorded"
SESSION_TUTOR_PRESENTATION_RECORDED = "session.tutor_presentation_recorded"
SESSION_CONTINUATION_SUMMARY_UPDATED = "session.continuation_summary_updated"
SESSION_SUSPENDED = "session.suspended"
SESSION_RESUMED = "session.resumed"
SESSION_ENDED = "session.ended"
SESSION_EVENT_TYPES = frozenset(
    {
        SESSION_STARTED,
        SESSION_INTERACTION_RECORDED,
        SESSION_ANSWER_RECORDED,
        SESSION_ASSISTANT_TURN_RECORDED,
        SESSION_TUTOR_PRESENTATION_RECORDED,
        SESSION_CONTINUATION_SUMMARY_UPDATED,
        SESSION_SUSPENDED,
        SESSION_RESUMED,
        SESSION_ENDED,
    }
)


@dataclass(frozen=True, slots=True)
class SessionStarted:
    session_id: SessionId


@dataclass(frozen=True, slots=True)
class SessionInteractionRecorded:
    interaction_id: InteractionId
    kind: InteractionKind
    content: str


@dataclass(frozen=True, slots=True)
class SessionAnswerRecorded:
    record: AnswerRecord


@dataclass(frozen=True, slots=True)
class SessionAssistantTurnRecorded:
    record: AssistantTurnRecord


@dataclass(frozen=True, slots=True)
class SessionTutorPresentationRecorded:
    record: TutorPresentationRecord


@dataclass(frozen=True, slots=True)
class SessionSummaryUpdated:
    summary: ContinuationSummaryV1


@dataclass(frozen=True, slots=True)
class SessionLifecycleTransition:
    pass


def _object(value: JsonValue | None, name: str, keys: frozenset[str]) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    actual = frozenset(value)
    if actual != keys:
        raise ValueError(
            f"{name} fields mismatch; missing={sorted(keys - actual)}, "
            f"extra={sorted(actual - keys)}"
        )
    return value


def _text(value: JsonValue | None, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty trimmed text")
    return value


def _optional_text(value: JsonValue | None, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name)


def _integer(value: JsonValue | None, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _boolean(value: JsonValue | None, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _array(value: JsonValue | None, name: str) -> tuple[JsonValue, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be an array")
    return value


def _envelope(event: DomainEvent, event_type: str) -> SessionId:
    if event.event_type != event_type or event.schema_version != SESSION_SCHEMA_VERSION:
        raise ValueError(f"event envelope does not match {event_type}@1")
    if event.session_id is None:
        raise ValueError("session events require event.session_id")
    if not isinstance(event.event_id, EventId) or not isinstance(event.course_id, CourseId):
        raise ValueError("session event identity envelope is not typed")
    if not isinstance(event.session_id, SessionId):
        raise ValueError("session event session_id envelope is not typed")
    if not isinstance(event.correlation_id, CorrelationId):
        raise ValueError("session event correlation envelope is not typed")
    if event.causation_id is not None and not isinstance(event.causation_id, EventId):
        raise ValueError("session event causation envelope is not typed")
    if not isinstance(event.actor, Actor) or not isinstance(event.actor.kind, PrincipalKind):
        raise ValueError("session event actor envelope is not typed")
    return event.session_id


def _citation_json(citation: Citation) -> JsonObject:
    return {
        "source_id": str(citation.source_id),
        "revision_id": str(citation.revision_id),
        "chunk_id": str(citation.chunk_id),
        "start_offset": citation.start_offset,
        "end_offset": citation.end_offset,
        "locator": citation.locator,
        "quoted_snippet": citation.quoted_snippet,
    }


def _citation(value: JsonValue, name: str) -> Citation:
    payload = _object(
        value,
        name,
        frozenset(
            {
                "source_id",
                "revision_id",
                "chunk_id",
                "start_offset",
                "end_offset",
                "locator",
                "quoted_snippet",
            }
        ),
    )
    return Citation(
        SourceId(_text(payload.get("source_id"), f"{name}.source_id")),
        RevisionId(_text(payload.get("revision_id"), f"{name}.revision_id")),
        ChunkId(_text(payload.get("chunk_id"), f"{name}.chunk_id")),
        _integer(payload.get("start_offset"), f"{name}.start_offset"),
        _integer(payload.get("end_offset"), f"{name}.end_offset"),
        _text(payload.get("locator"), f"{name}.locator"),
        _optional_text(payload.get("quoted_snippet"), f"{name}.quoted_snippet"),
    )


def _commitment_json(item: SourceCommitment) -> JsonObject:
    return {
        "source_id": str(item.source_id),
        "revision_id": str(item.revision_id),
        "chunk_id": str(item.chunk_id),
        "start_offset": item.start_offset,
        "end_offset": item.end_offset,
    }


def _commitment(value: JsonValue, name: str) -> SourceCommitment:
    payload = _object(
        value,
        name,
        frozenset({"source_id", "revision_id", "chunk_id", "start_offset", "end_offset"}),
    )
    return SourceCommitment(
        SourceId(_text(payload.get("source_id"), f"{name}.source_id")),
        RevisionId(_text(payload.get("revision_id"), f"{name}.revision_id")),
        ChunkId(_text(payload.get("chunk_id"), f"{name}.chunk_id")),
        _integer(payload.get("start_offset"), f"{name}.start_offset"),
        _integer(payload.get("end_offset"), f"{name}.end_offset"),
    )


def _provenance_json(value: AnswerProvenance) -> JsonObject:
    model: JsonObject | None = None
    if value.model is not None:
        model = {
            "adapter_id": value.model.adapter_id,
            "adapter_version": value.model.adapter_version,
            "model_id": value.model.model_id,
            "response_id": value.model.response_id,
            "run_id": str(value.model.run_id),
            "usage": (
                None
                if value.model.usage is None
                else {
                    "input_tokens": value.model.usage.input_tokens,
                    "output_tokens": value.model.usage.output_tokens,
                }
            ),
        }
    return {
        "source_commitments": tuple(_commitment_json(item) for item in value.source_commitments),
        "prompt": {
            "prompt_id": value.prompt.prompt_id,
            "version": value.prompt.version,
            "composition_fingerprint": value.prompt.composition_fingerprint,
            "layer_fingerprints": value.prompt.layer_fingerprints,
        },
        "model": model,
        "retrieval": {
            "strategy_id": value.retrieval.strategy_id,
            "strategy_version": value.retrieval.strategy_version,
            "query_fingerprint": value.retrieval.query_fingerprint,
            "index_version": value.retrieval.index_version,
            "read_set_fingerprint": value.retrieval.read_set_fingerprint,
        },
        "validators": tuple(
            {
                "validator_id": item.validator_id,
                "version": item.version,
                "passed": item.passed,
                "disposition": item.disposition,
                "result_fingerprint": item.result_fingerprint,
            }
            for item in value.validators
        ),
        "pins": {
            "skill": value.pins.skill,
            "playbook": value.pins.playbook,
            "prompt": value.pins.prompt,
            "model_adapter": value.pins.model_adapter,
            "state_contract": value.pins.state_contract,
            "tool_behavior": value.pins.tool_behavior,
        },
        "playbook_run_id": str(value.playbook_run_id),
        "event_schema_version": value.event_schema_version,
        "reducer_schema_version": value.reducer_schema_version,
    }


def _provenance(value: JsonValue, name: str) -> AnswerProvenance:
    payload = _object(
        value,
        name,
        frozenset(
            {
                "source_commitments",
                "prompt",
                "model",
                "retrieval",
                "validators",
                "pins",
                "playbook_run_id",
                "event_schema_version",
                "reducer_schema_version",
            }
        ),
    )
    prompt = _object(
        payload.get("prompt"),
        f"{name}.prompt",
        frozenset({"prompt_id", "version", "composition_fingerprint", "layer_fingerprints"}),
    )
    layers = tuple(
        _text(item, f"{name}.prompt.layer_fingerprints[{index}]")
        for index, item in enumerate(
            _array(
                prompt.get("layer_fingerprints"),
                f"{name}.prompt.layer_fingerprints",
            )
        )
    )
    prompt_value = PromptProvenance(
        _text(prompt.get("prompt_id"), f"{name}.prompt.prompt_id"),
        _text(prompt.get("version"), f"{name}.prompt.version"),
        _optional_text(
            prompt.get("composition_fingerprint"), f"{name}.prompt.composition_fingerprint"
        ),
        layers,
    )
    model_raw = payload.get("model")
    model_value: ModelProvenance | None = None
    if model_raw is not None:
        model = _object(
            model_raw,
            f"{name}.model",
            frozenset(
                {"adapter_id", "adapter_version", "model_id", "response_id", "run_id", "usage"}
            ),
        )
        usage_raw = model.get("usage")
        usage: ModelUsageProvenance | None = None
        if usage_raw is not None:
            usage_payload = _object(
                usage_raw,
                f"{name}.model.usage",
                frozenset({"input_tokens", "output_tokens"}),
            )
            usage = ModelUsageProvenance(
                _integer(
                    usage_payload.get("input_tokens"),
                    f"{name}.model.usage.input_tokens",
                ),
                _integer(
                    usage_payload.get("output_tokens"),
                    f"{name}.model.usage.output_tokens",
                ),
            )
        model_value = ModelProvenance(
            _text(model.get("adapter_id"), f"{name}.model.adapter_id"),
            _text(model.get("adapter_version"), f"{name}.model.adapter_version"),
            _text(model.get("model_id"), f"{name}.model.model_id"),
            _text(model.get("response_id"), f"{name}.model.response_id"),
            RunId(_text(model.get("run_id"), f"{name}.model.run_id")),
            usage,
        )
    retrieval = _object(
        payload.get("retrieval"),
        f"{name}.retrieval",
        frozenset(
            {
                "strategy_id",
                "strategy_version",
                "query_fingerprint",
                "index_version",
                "read_set_fingerprint",
            }
        ),
    )
    validators = tuple(
        _validator(item, f"{name}.validators[{index}]")
        for index, item in enumerate(_array(payload.get("validators"), f"{name}.validators"))
    )
    pins = _object(
        payload.get("pins"),
        f"{name}.pins",
        frozenset(
            {"skill", "playbook", "prompt", "model_adapter", "state_contract", "tool_behavior"}
        ),
    )
    commitments = tuple(
        _commitment(item, f"{name}.source_commitments[{index}]")
        for index, item in enumerate(
            _array(payload.get("source_commitments"), f"{name}.source_commitments")
        )
    )
    return AnswerProvenance(
        commitments,
        prompt_value,
        model_value,
        RetrievalProvenance(
            _text(retrieval.get("strategy_id"), f"{name}.retrieval.strategy_id"),
            _text(retrieval.get("strategy_version"), f"{name}.retrieval.strategy_version"),
            _text(retrieval.get("query_fingerprint"), f"{name}.retrieval.query_fingerprint"),
            _text(retrieval.get("index_version"), f"{name}.retrieval.index_version"),
            _text(
                retrieval.get("read_set_fingerprint"),
                f"{name}.retrieval.read_set_fingerprint",
            ),
        ),
        validators,
        VersionPins(
            _text(pins.get("skill"), f"{name}.pins.skill"),
            _text(pins.get("playbook"), f"{name}.pins.playbook"),
            _text(pins.get("prompt"), f"{name}.pins.prompt"),
            _optional_text(pins.get("model_adapter"), f"{name}.pins.model_adapter"),
            _text(pins.get("state_contract"), f"{name}.pins.state_contract"),
            _text(pins.get("tool_behavior"), f"{name}.pins.tool_behavior"),
        ),
        RunId(_text(payload.get("playbook_run_id"), f"{name}.playbook_run_id")),
        _integer(payload.get("event_schema_version"), f"{name}.event_schema_version"),
        _integer(payload.get("reducer_schema_version"), f"{name}.reducer_schema_version"),
    )


def _validator(value: JsonValue, name: str) -> ValidatorProvenance:
    payload = _object(
        value,
        name,
        frozenset(
            {"validator_id", "version", "passed", "disposition", "result_fingerprint"}
        ),
    )
    return ValidatorProvenance(
        _text(payload.get("validator_id"), f"{name}.validator_id"),
        _text(payload.get("version"), f"{name}.version"),
        _boolean(payload.get("passed"), f"{name}.passed"),
        _text(payload.get("disposition"), f"{name}.disposition"),
        _text(payload.get("result_fingerprint"), f"{name}.result_fingerprint"),
    )


def _answer_json(answer: GroundedAnswer) -> JsonObject:
    return {
        "status": answer.status.value,
        "segments": tuple(
            {
                "kind": segment.kind.value,
                "text": segment.text,
                "citations": tuple(_citation_json(item) for item in segment.citations),
                "claim_origin": segment.claim_origin.value,
            }
            for segment in answer.segments
        ),
        "unsupported_information_note": answer.unsupported_information_note,
        "provenance": _provenance_json(answer.provenance),
    }


def grounded_answer_manifest(answer: GroundedAnswer) -> JsonObject:
    return _answer_json(answer)


def _answer(value: JsonValue, name: str) -> GroundedAnswer:
    payload = _object(
        value,
        name,
        frozenset({"status", "segments", "unsupported_information_note", "provenance"}),
    )
    try:
        status = AnswerStatus(_text(payload.get("status"), f"{name}.status"))
    except ValueError as error:
        raise ValueError(f"{name}.status is unsupported") from error
    segments = tuple(
        _segment(item, f"{name}.segments[{index}]")
        for index, item in enumerate(_array(payload.get("segments"), f"{name}.segments"))
    )
    return GroundedAnswer(
        status,
        segments,
        _optional_text(
            payload.get("unsupported_information_note"),
            f"{name}.unsupported_information_note",
        ),
        _provenance(payload.get("provenance"), f"{name}.provenance"),
    )


def decode_grounded_answer_manifest(value: JsonValue) -> GroundedAnswer:
    return _answer(value, "answer")


def _segment(value: JsonValue, name: str) -> AnswerSegment:
    payload = _object(
        value,
        name,
        frozenset({"kind", "text", "citations", "claim_origin"}),
    )
    try:
        kind = SegmentKind(_text(payload.get("kind"), f"{name}.kind"))
        origin = ClaimOrigin(_text(payload.get("claim_origin"), f"{name}.claim_origin"))
    except ValueError as error:
        raise ValueError(f"{name} contains an unsupported enum") from error
    citations = tuple(
        _citation(item, f"{name}.citations[{index}]")
        for index, item in enumerate(_array(payload.get("citations"), f"{name}.citations"))
    )
    return AnswerSegment(kind, _text(payload.get("text"), f"{name}.text"), citations, origin)


def summary_payload(summary: ContinuationSummaryV1) -> JsonObject:
    return {
        "summary": {
            "schema_version": summary.schema_version,
            "through_interaction_id": str(summary.through_interaction_id),
            "interaction_count": summary.interaction_count,
            "recent_exchanges": tuple(
                {
                    "question_interaction_id": str(item.question_interaction_id),
                    "answer_interaction_id": str(item.answer_interaction_id),
                    "learner_excerpt": item.learner_excerpt,
                    "assistant_excerpt": item.assistant_excerpt,
                    "answer_status": item.answer_status.value,
                    "unsupported_note": item.unsupported_note,
                }
                for item in summary.recent_exchanges
            ),
            "grounded_points": summary.grounded_points,
            "unresolved_notes": summary.unresolved_notes,
            "character_count": summary.character_count,
        }
    }


def _summary(value: JsonValue, name: str) -> ContinuationSummaryV1:
    payload = _object(
        value,
        name,
        frozenset(
            {
                "schema_version",
                "through_interaction_id",
                "interaction_count",
                "recent_exchanges",
                "grounded_points",
                "unresolved_notes",
                "character_count",
            }
        ),
    )
    exchanges = tuple(
        _exchange(item, f"{name}.recent_exchanges[{index}]")
        for index, item in enumerate(
            _array(payload.get("recent_exchanges"), f"{name}.recent_exchanges")
        )
    )
    grounded_points = tuple(
        _text(item, f"{name}.grounded_points[{index}]")
        for index, item in enumerate(
            _array(payload.get("grounded_points"), f"{name}.grounded_points")
        )
    )
    unresolved_notes = tuple(
        _text(item, f"{name}.unresolved_notes[{index}]")
        for index, item in enumerate(
            _array(payload.get("unresolved_notes"), f"{name}.unresolved_notes")
        )
    )
    return ContinuationSummaryV1(
        InteractionId(
            _text(payload.get("through_interaction_id"), f"{name}.through_interaction_id")
        ),
        _integer(payload.get("interaction_count"), f"{name}.interaction_count"),
        exchanges,
        grounded_points,
        unresolved_notes,
        _integer(payload.get("character_count"), f"{name}.character_count"),
        _integer(payload.get("schema_version"), f"{name}.schema_version"),
    )


def decode_summary_manifest(value: JsonValue) -> ContinuationSummaryV1:
    return _summary(value, "summary")


def _exchange(value: JsonValue, name: str) -> SummaryExchange:
    payload = _object(
        value,
        name,
        frozenset(
            {
                "question_interaction_id",
                "answer_interaction_id",
                "learner_excerpt",
                "assistant_excerpt",
                "answer_status",
                "unsupported_note",
            }
        ),
    )
    try:
        status = AnswerStatus(_text(payload.get("answer_status"), f"{name}.answer_status"))
    except ValueError as error:
        raise ValueError(f"{name}.answer_status is unsupported") from error
    return SummaryExchange(
        InteractionId(
            _text(payload.get("question_interaction_id"), f"{name}.question_interaction_id")
        ),
        InteractionId(
            _text(payload.get("answer_interaction_id"), f"{name}.answer_interaction_id")
        ),
        _text(payload.get("learner_excerpt"), f"{name}.learner_excerpt"),
        _text(payload.get("assistant_excerpt"), f"{name}.assistant_excerpt"),
        status,
        _optional_text(payload.get("unsupported_note"), f"{name}.unsupported_note"),
    )


def session_started_payload(session_id: SessionId) -> JsonObject:
    return {"session_id": str(session_id)}


def interaction_recorded_payload(
    interaction_id: InteractionId, kind: InteractionKind, content: str
) -> JsonObject:
    if kind not in (InteractionKind.HUMAN, InteractionKind.NOTE):
        raise ValueError("interaction_recorded accepts only human or note interactions")
    return {"interaction_id": str(interaction_id), "kind": kind.value, "content": content}


def answer_recorded_payload(record: AnswerRecord) -> JsonObject:
    return {
        "answer_id": str(record.id),
        "interaction_id": str(record.interaction_id),
        "question_interaction_id": str(record.question_interaction_id),
        "run_id": str(record.run_id),
        "idempotency_key": record.idempotency_key,
        "command_fingerprint": record.command_fingerprint,
        "answer": _answer_json(record.answer),
        "provenance": _provenance_json(record.answer.provenance),
    }


def assistant_turn_recorded_payload(record: AssistantTurnRecord) -> JsonObject:
    return {
        "turn_id": str(record.id),
        "status": record.status.value,
        "content": record.content,
        "in_reply_to_interaction_id": (
            str(record.in_reply_to_interaction_id)
            if record.in_reply_to_interaction_id is not None
            else None
        ),
        "output": {
            "run_id": str(record.output.run_id),
            "output_key": record.output.output_key,
            "output_fingerprint": record.output.output_fingerprint,
        },
        "idempotency_key": record.idempotency_key,
        "command_fingerprint": record.command_fingerprint,
    }


def tutor_message_output_fingerprint(
    status: AssistantTurnStatus,
    content: str,
    in_reply_to_interaction_id: InteractionId | None,
) -> str:
    return sha256(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "status": status.value,
                "content": content,
                "in_reply_to_interaction_id": (
                    str(in_reply_to_interaction_id)
                    if in_reply_to_interaction_id is not None
                    else None
                ),
            }
        )
    ).hexdigest()


def assistant_turn_command_fingerprint(
    status: AssistantTurnStatus,
    content: str,
    in_reply_to_interaction_id: InteractionId | None,
    output: VerifiedRunOutputRef,
) -> str:
    return sha256(
        canonical_json_bytes(
            {
                "status": status.value,
                "content": content,
                "in_reply_to_interaction_id": (
                    str(in_reply_to_interaction_id)
                    if in_reply_to_interaction_id is not None
                    else None
                ),
                "output": {
                    "run_id": str(output.run_id),
                    "output_key": output.output_key,
                    "output_fingerprint": output.output_fingerprint,
                },
            }
        )
    ).hexdigest()


def tutor_presentation_command_fingerprint(
    kind: TutorPresentationKind,
    content: str,
    in_reply_to_interaction_id: InteractionId | None,
    host_turn_id: str,
    observed_host_context_sequence: int,
    host_context_fingerprint: str,
    decision_fingerprint: str,
    receipt_fingerprint: str,
    continuation_fingerprint: str | None,
    capability_identity: str | None,
    response_schema: JsonObject | None,
) -> str:
    return sha256(
        b"study-agent-tutor-presentation-command-v1\0"
        + canonical_json_bytes(
            {
                "kind": kind.value,
                "content": content,
                "in_reply_to_interaction_id": (
                    str(in_reply_to_interaction_id)
                    if in_reply_to_interaction_id is not None
                    else None
                ),
                "host_turn_id": host_turn_id,
                "observed_host_context_sequence": observed_host_context_sequence,
                "host_context_fingerprint": host_context_fingerprint,
                "decision_fingerprint": decision_fingerprint,
                "receipt_fingerprint": receipt_fingerprint,
                "continuation_fingerprint": continuation_fingerprint,
                "capability_identity": capability_identity,
                "response_schema": response_schema,
            }
        )
    ).hexdigest()


def tutor_presentation_recorded_payload(record: TutorPresentationRecord) -> JsonObject:
    return {
        "presentation_id": str(record.id),
        "kind": record.kind.value,
        "content": record.content,
        "in_reply_to_interaction_id": (
            str(record.in_reply_to_interaction_id)
            if record.in_reply_to_interaction_id is not None
            else None
        ),
        "host_turn_id": record.host_turn_id,
        "observed_host_context_sequence": record.observed_host_context_sequence,
        "host_context_fingerprint": record.host_context_fingerprint,
        "decision_fingerprint": record.decision_fingerprint,
        "receipt_fingerprint": record.receipt_fingerprint,
        "continuation_fingerprint": record.continuation_fingerprint,
        "capability_identity": record.capability_identity,
        "response_schema": record.response_schema,
        "idempotency_key": record.idempotency_key,
        "command_fingerprint": record.command_fingerprint,
    }


def lifecycle_payload() -> JsonObject:
    return {}


def decode_session_started(event: DomainEvent) -> SessionStarted:
    session_id = _envelope(event, SESSION_STARTED)
    payload = _object(event.payload, "payload", frozenset({"session_id"}))
    decoded = SessionId(_text(payload.get("session_id"), "payload.session_id"))
    if decoded != session_id:
        raise ValueError("payload session_id must match event.session_id")
    return SessionStarted(decoded)


def decode_interaction_recorded(event: DomainEvent) -> SessionInteractionRecorded:
    _envelope(event, SESSION_INTERACTION_RECORDED)
    payload = _object(
        event.payload, "payload", frozenset({"interaction_id", "kind", "content"})
    )
    try:
        kind = InteractionKind(_text(payload.get("kind"), "payload.kind"))
    except ValueError as error:
        raise ValueError("payload.kind is unsupported") from error
    if kind not in (InteractionKind.HUMAN, InteractionKind.NOTE):
        raise ValueError("interaction_recorded kind must be human or note")
    return SessionInteractionRecorded(
        InteractionId(_text(payload.get("interaction_id"), "payload.interaction_id")),
        kind,
        _text(payload.get("content"), "payload.content"),
    )


def decode_answer_recorded(event: DomainEvent) -> SessionAnswerRecorded:
    _envelope(event, SESSION_ANSWER_RECORDED)
    payload = _object(
        event.payload,
        "payload",
        frozenset(
            {
                "answer_id",
                "interaction_id",
                "question_interaction_id",
                "run_id",
                "idempotency_key",
                "command_fingerprint",
                "answer",
                "provenance",
            }
        ),
    )
    answer = _answer(payload.get("answer"), "payload.answer")
    duplicate_provenance = _provenance(payload.get("provenance"), "payload.provenance")
    if duplicate_provenance != answer.provenance:
        raise ValueError("answer provenance must exactly match the trusted provenance field")
    record = AnswerRecord(
        AnswerId(_text(payload.get("answer_id"), "payload.answer_id")),
        InteractionId(_text(payload.get("interaction_id"), "payload.interaction_id")),
        InteractionId(
            _text(payload.get("question_interaction_id"), "payload.question_interaction_id")
        ),
        RunId(_text(payload.get("run_id"), "payload.run_id")),
        _text(payload.get("idempotency_key"), "payload.idempotency_key"),
        _text(payload.get("command_fingerprint"), "payload.command_fingerprint"),
        answer,
    )
    return SessionAnswerRecorded(record)


def decode_assistant_turn_recorded(event: DomainEvent) -> SessionAssistantTurnRecorded:
    session_id = _envelope(event, SESSION_ASSISTANT_TURN_RECORDED)
    if event.actor.kind is not PrincipalKind.SERVICE:
        raise ValueError("assistant turns require service authority")
    payload = _object(
        event.payload,
        "payload",
        frozenset(
            {
                "turn_id",
                "status",
                "content",
                "in_reply_to_interaction_id",
                "output",
                "idempotency_key",
                "command_fingerprint",
            }
        ),
    )
    output = _object(
        payload.get("output"),
        "payload.output",
        frozenset({"run_id", "output_key", "output_fingerprint"}),
    )
    try:
        status = AssistantTurnStatus(_text(payload.get("status"), "payload.status"))
    except ValueError as error:
        raise ValueError("payload.status is unsupported") from error
    reply_raw = payload.get("in_reply_to_interaction_id")
    reply = (
        None
        if reply_raw is None
        else InteractionId(_text(reply_raw, "payload.in_reply_to_interaction_id"))
    )
    record = AssistantTurnRecord(
        id=InteractionId(_text(payload.get("turn_id"), "payload.turn_id")),
        session_id=session_id,
        occurred_at=event.occurred_at,
        status=status,
        content=_text(payload.get("content"), "payload.content"),
        in_reply_to_interaction_id=reply,
        output=VerifiedRunOutputRef(
            run_id=RunId(_text(output.get("run_id"), "payload.output.run_id")),
            output_key=_text(output.get("output_key"), "payload.output.output_key"),
            output_fingerprint=_text(
                output.get("output_fingerprint"), "payload.output.output_fingerprint"
            ),
        ),
        idempotency_key=_text(payload.get("idempotency_key"), "payload.idempotency_key"),
        command_fingerprint=_text(
            payload.get("command_fingerprint"), "payload.command_fingerprint"
        ),
        event_id=event.event_id,
        course_sequence=event.course_sequence,
    )
    if record.id != assistant_interaction_id_for(
        event.course_id, session_id, record.output.run_id, record.idempotency_key
    ):
        raise ValueError("assistant turn id does not match command identity")
    if event.event_id != session_turn_event_id_for(
        event.course_id,
        session_id,
        record.idempotency_key,
        SESSION_ASSISTANT_TURN_RECORDED,
    ):
        raise ValueError("assistant turn event id does not match command identity")
    if record.output.output_fingerprint != tutor_message_output_fingerprint(
        record.status, record.content, record.in_reply_to_interaction_id
    ):
        raise ValueError("assistant turn output fingerprint mismatch")
    if record.command_fingerprint != assistant_turn_command_fingerprint(
        record.status,
        record.content,
        record.in_reply_to_interaction_id,
        record.output,
    ):
        raise ValueError("assistant turn command fingerprint mismatch")
    return SessionAssistantTurnRecorded(record)


def decode_tutor_presentation_recorded(event: DomainEvent) -> SessionTutorPresentationRecorded:
    session_id = _envelope(event, SESSION_TUTOR_PRESENTATION_RECORDED)
    if event.actor.kind is not PrincipalKind.SERVICE:
        raise ValueError("tutor presentations require service authority")
    payload = _object(
        event.payload,
        "payload",
        frozenset(
            {
                "presentation_id",
                "kind",
                "content",
                "in_reply_to_interaction_id",
                "host_turn_id",
                "observed_host_context_sequence",
                "host_context_fingerprint",
                "decision_fingerprint",
                "receipt_fingerprint",
                "continuation_fingerprint",
                "capability_identity",
                "response_schema",
                "idempotency_key",
                "command_fingerprint",
            }
        ),
    )
    try:
        kind = TutorPresentationKind(_text(payload.get("kind"), "payload.kind"))
    except ValueError as error:
        raise ValueError("payload.kind is unsupported") from error
    reply_raw = payload.get("in_reply_to_interaction_id")
    reply = (
        None
        if reply_raw is None
        else InteractionId(_text(reply_raw, "payload.in_reply_to_interaction_id"))
    )
    continuation = _optional_text(
        payload.get("continuation_fingerprint"), "payload.continuation_fingerprint"
    )
    capability = _optional_text(
        payload.get("capability_identity"), "payload.capability_identity"
    )
    schema_raw = payload.get("response_schema")
    if schema_raw is not None and not isinstance(schema_raw, Mapping):
        raise ValueError("payload.response_schema must be an object")
    schema = schema_raw
    if schema is not None:
        validate_schema_definition(schema)
        reject_provider_selectors(schema, "payload.response_schema")
        if len(canonical_json_bytes(schema)) > MAX_TUTOR_PRESENTATION_SCHEMA_BYTES:
            raise ValueError("payload.response_schema exceeds presentation bounds")
    record = TutorPresentationRecord(
        id=TutorPresentationId(_text(payload.get("presentation_id"), "payload.presentation_id")),
        session_id=session_id,
        occurred_at=event.occurred_at,
        kind=kind,
        content=_text(payload.get("content"), "payload.content"),
        in_reply_to_interaction_id=reply,
        host_turn_id=_text(payload.get("host_turn_id"), "payload.host_turn_id"),
        observed_host_context_sequence=_integer(
            payload.get("observed_host_context_sequence"),
            "payload.observed_host_context_sequence",
        ),
        host_context_fingerprint=_text(
            payload.get("host_context_fingerprint"), "payload.host_context_fingerprint"
        ),
        decision_fingerprint=_text(
            payload.get("decision_fingerprint"), "payload.decision_fingerprint"
        ),
        receipt_fingerprint=_text(
            payload.get("receipt_fingerprint"), "payload.receipt_fingerprint"
        ),
        continuation_fingerprint=continuation,
        capability_identity=capability,
        response_schema=schema,
        idempotency_key=_text(payload.get("idempotency_key"), "payload.idempotency_key"),
        command_fingerprint=_text(
            payload.get("command_fingerprint"), "payload.command_fingerprint"
        ),
        event_id=event.event_id,
        course_sequence=event.course_sequence,
    )
    if record.id != tutor_presentation_id_for(
        event.course_id, session_id, record.host_turn_id, record.kind.value
    ):
        raise ValueError("presentation id does not match host turn identity")
    if record.command_fingerprint != tutor_presentation_command_fingerprint(
        record.kind,
        record.content,
        record.in_reply_to_interaction_id,
        record.host_turn_id,
        record.observed_host_context_sequence,
        record.host_context_fingerprint,
        record.decision_fingerprint,
        record.receipt_fingerprint,
        record.continuation_fingerprint,
        record.capability_identity,
        record.response_schema,
    ):
        raise ValueError("presentation command fingerprint mismatch")
    if event.event_id != session_turn_event_id_for(
        event.course_id,
        session_id,
        record.idempotency_key,
        SESSION_TUTOR_PRESENTATION_RECORDED,
    ):
        raise ValueError("presentation event id does not match command identity")
    return SessionTutorPresentationRecorded(record)


def decode_summary_updated(event: DomainEvent) -> SessionSummaryUpdated:
    _envelope(event, SESSION_CONTINUATION_SUMMARY_UPDATED)
    payload = _object(event.payload, "payload", frozenset({"summary"}))
    return SessionSummaryUpdated(_summary(payload.get("summary"), "payload.summary"))


def decode_lifecycle(event: DomainEvent, event_type: str) -> SessionLifecycleTransition:
    _envelope(event, event_type)
    _object(event.payload, "payload", frozenset())
    return SessionLifecycleTransition()
