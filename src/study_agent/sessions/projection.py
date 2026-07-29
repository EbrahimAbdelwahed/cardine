"""Pure reducers for session state embedded in a mixed per-course projection."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.events import DomainEvent
from study_agent.domain.identifiers import AnswerId, InteractionId, RunId
from study_agent.domain.session import (
    AnswerRecord,
    InteractionKind,
    InteractionRecord,
)
from study_agent.state import EventRegistry

from .events import (
    SESSION_ANSWER_RECORDED,
    SESSION_ASSISTANT_TURN_RECORDED,
    SESSION_CONTINUATION_SUMMARY_UPDATED,
    SESSION_ENDED,
    SESSION_INTERACTION_RECORDED,
    SESSION_RESUMED,
    SESSION_SCHEMA_VERSION,
    SESSION_STARTED,
    SESSION_SUSPENDED,
    SESSION_TUTOR_PRESENTATION_RECORDED,
    SessionAnswerRecorded,
    SessionAssistantTurnRecorded,
    SessionInteractionRecorded,
    SessionLifecycleTransition,
    SessionStarted,
    SessionSummaryUpdated,
    SessionTutorPresentationRecorded,
    decode_answer_recorded,
    decode_assistant_turn_recorded,
    decode_grounded_answer_manifest,
    decode_interaction_recorded,
    decode_lifecycle,
    decode_session_started,
    decode_summary_manifest,
    decode_summary_updated,
    decode_tutor_presentation_recorded,
    grounded_answer_manifest,
    summary_payload,
)
from .summary import verify_continuation_summary


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _mapping(value: JsonValue | None, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"projection field {name} must be an object")
    return value


def _session_maps(
    state: JsonObject,
) -> tuple[
    dict[str, JsonValue],
    dict[str, JsonValue],
    dict[str, JsonValue],
]:
    return (
        dict(_mapping(state.get("sessions", {}), "sessions")),
        dict(_mapping(state.get("session_interactions", {}), "session_interactions")),
        dict(_mapping(state.get("session_answers", {}), "session_answers")),
    )


def _session(
    sessions: Mapping[str, JsonValue], event: DomainEvent
) -> tuple[str, dict[str, JsonValue]]:
    if event.session_id is None:
        raise ValueError("session event is missing session_id")
    session_id = str(event.session_id)
    raw = sessions.get(session_id)
    if not isinstance(raw, Mapping):
        raise ValueError("session does not exist")
    result = dict(raw)
    expected = {
        "session_id",
        "course_id",
        "status",
        "started_at",
        "suspended_at",
        "resumed_at",
        "ended_at",
        "last_event_at",
        "interaction_ids",
        "run_ids",
        "continuation_summary",
    }
    if set(result) != expected:
        raise ValueError("session projection fields are corrupt")
    if result.get("session_id") != session_id:
        raise ValueError("session projection identity is corrupt")
    if result.get("course_id") != str(event.course_id):
        raise ValueError("session projection belongs to another course")
    return session_id, result


def _chronological(session: Mapping[str, JsonValue], event: DomainEvent) -> None:
    raw = session.get("last_event_at")
    if not isinstance(raw, str):
        raise ValueError("session projection last_event_at is corrupt")
    try:
        latest = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("session projection last_event_at is corrupt") from error
    if event.occurred_at < latest:
        raise ValueError("session event timestamp cannot precede canonical history")


def _active(session: Mapping[str, JsonValue]) -> None:
    if session.get("status") != "active":
        raise ValueError("session must be active")


def reduce_session_started(
    state: JsonObject, event: DomainEvent, payload: SessionStarted
) -> Mapping[str, JsonValue]:
    sessions, interactions, answers = _session_maps(state)
    session_id = str(payload.session_id)
    if session_id in sessions:
        raise ValueError("session id already exists")
    sessions[session_id] = {
        "session_id": session_id,
        "course_id": str(event.course_id),
        "status": "active",
        "started_at": _timestamp(event.occurred_at),
        "suspended_at": None,
        "resumed_at": None,
        "ended_at": None,
        "last_event_at": _timestamp(event.occurred_at),
        "interaction_ids": (),
        "run_ids": (),
        "continuation_summary": None,
    }
    return {
        **state,
        "sessions": sessions,
        "session_interactions": interactions,
        "session_answers": answers,
    }


def reduce_interaction_recorded(
    state: JsonObject,
    event: DomainEvent,
    payload: SessionInteractionRecorded,
) -> Mapping[str, JsonValue]:
    sessions, interactions, answers = _session_maps(state)
    session_id, session = _session(sessions, event)
    _chronological(session, event)
    _active(session)
    interaction_id = str(payload.interaction_id)
    if interaction_id in interactions:
        raise ValueError("interaction id already exists")
    ids = _text_array(session.get("interaction_ids"), "interaction_ids")
    if interaction_id in ids:
        raise ValueError("interaction id already exists in session")
    interactions[interaction_id] = {
        "session_id": session_id,
        "kind": payload.kind.value,
        "occurred_at": _timestamp(event.occurred_at),
        "content": payload.content,
        "answer_id": None,
        "run_id": None,
    }
    session["interaction_ids"] = (*ids, interaction_id)
    session["last_event_at"] = _timestamp(event.occurred_at)
    sessions[session_id] = session
    return {
        **state,
        "sessions": sessions,
        "session_interactions": interactions,
        "session_answers": answers,
    }


def reduce_answer_recorded(
    state: JsonObject,
    event: DomainEvent,
    payload: SessionAnswerRecorded,
) -> Mapping[str, JsonValue]:
    sessions, interactions, answers = _session_maps(state)
    session_id, session = _session(sessions, event)
    _chronological(session, event)
    _active(session)
    record = payload.record
    answer_id = str(record.id)
    assistant_id = str(record.interaction_id)
    question_id = str(record.question_interaction_id)
    run_id = str(record.run_id)
    if answer_id in answers:
        raise ValueError("answer id already exists")
    if assistant_id in interactions:
        raise ValueError("assistant interaction id already exists")
    question = interactions.get(question_id)
    if not isinstance(question, Mapping):
        raise ValueError("question interaction does not exist")
    if question.get("session_id") != session_id or question.get("kind") != "human":
        raise ValueError("question interaction must be human and belong to the session")
    for raw in answers.values():
        if not isinstance(raw, Mapping):
            raise ValueError("answer projection is corrupt")
        if raw.get("run_id") == run_id:
            raise ValueError("run id already belongs to an answer")
        if (
            raw.get("session_id") == session_id
            and raw.get("idempotency_key") == record.idempotency_key
        ):
            raise ValueError("idempotency key already belongs to an answer")
    assistant_turns = _mapping(
        state.get("session_assistant_turns", {}), "session_assistant_turns"
    )
    for raw in assistant_turns.values():
        if not isinstance(raw, Mapping):
            raise ValueError("assistant turn projection is corrupt")
        if raw.get("run_id") == run_id:
            raise ValueError("run id already belongs to an assistant turn")
        if (
            raw.get("session_id") == session_id
            and raw.get("idempotency_key") == record.idempotency_key
        ):
            raise ValueError("idempotency key already belongs to an assistant turn")
    interaction_ids = _text_array(session.get("interaction_ids"), "interaction_ids")
    run_ids = _text_array(session.get("run_ids"), "run_ids")
    if run_id in run_ids:
        raise ValueError("run id already exists in session")
    assistant_content = _answer_content(record)
    interactions[assistant_id] = {
        "session_id": session_id,
        "kind": "assistant",
        "occurred_at": _timestamp(event.occurred_at),
        "content": assistant_content,
        "answer_id": answer_id,
        "run_id": run_id,
    }
    answers[answer_id] = {
        "session_id": session_id,
        "answer_id": answer_id,
        "interaction_id": assistant_id,
        "question_interaction_id": question_id,
        "run_id": run_id,
        "idempotency_key": record.idempotency_key,
        "command_fingerprint": record.command_fingerprint,
        "answer": grounded_answer_manifest(record.answer),
    }
    session["interaction_ids"] = (*interaction_ids, assistant_id)
    session["run_ids"] = (*run_ids, run_id)
    session["last_event_at"] = _timestamp(event.occurred_at)
    sessions[session_id] = session
    return {
        **state,
        "sessions": sessions,
        "session_interactions": interactions,
        "session_answers": answers,
    }


def reduce_assistant_turn_recorded(
    state: JsonObject,
    event: DomainEvent,
    payload: SessionAssistantTurnRecorded,
) -> Mapping[str, JsonValue]:
    sessions, interactions, answers = _session_maps(state)
    session_id, session = _session(sessions, event)
    _chronological(session, event)
    _active(session)
    record = payload.record
    if record.session_id != event.session_id:
        raise ValueError("assistant turn belongs to another session")
    turns = dict(
        _mapping(state.get("session_assistant_turns", {}), "session_assistant_turns")
    )
    turn_id = str(record.id)
    run_id = str(record.output.run_id)
    if turn_id in interactions or turn_id in turns:
        raise ValueError("assistant turn id already exists")
    if record.in_reply_to_interaction_id is not None:
        reply = interactions.get(str(record.in_reply_to_interaction_id))
        if (
            not isinstance(reply, Mapping)
            or reply.get("session_id") != session_id
            or reply.get("kind") != "human"
        ):
            raise ValueError("assistant reply target must be human and belong to the session")
    for raw in answers.values():
        if not isinstance(raw, Mapping):
            raise ValueError("answer projection is corrupt")
        if raw.get("run_id") == run_id:
            raise ValueError("run id already belongs to an answer")
        if (
            raw.get("session_id") == session_id
            and raw.get("idempotency_key") == record.idempotency_key
        ):
            raise ValueError("idempotency key already belongs to an answer")
    for raw in turns.values():
        if not isinstance(raw, Mapping):
            raise ValueError("assistant turn projection is corrupt")
        if raw.get("run_id") == run_id:
            raise ValueError("run id already belongs to an assistant turn")
        if (
            raw.get("session_id") == session_id
            and raw.get("idempotency_key") == record.idempotency_key
        ):
            raise ValueError("idempotency key already belongs to an assistant turn")
    turns[turn_id] = {
        "session_id": session_id,
        "status": record.status.value,
        "content": record.content,
        "in_reply_to_interaction_id": (
            str(record.in_reply_to_interaction_id)
            if record.in_reply_to_interaction_id is not None
            else None
        ),
        "run_id": run_id,
        "output_key": record.output.output_key,
        "output_fingerprint": record.output.output_fingerprint,
        "idempotency_key": record.idempotency_key,
        "command_fingerprint": record.command_fingerprint,
        "event_id": str(record.event_id),
        "course_sequence": record.course_sequence,
        "occurred_at": _timestamp(record.occurred_at),
    }
    session["last_event_at"] = _timestamp(event.occurred_at)
    sessions[session_id] = session
    return {
        **state,
        "sessions": sessions,
        "session_interactions": interactions,
        "session_answers": answers,
        "session_assistant_turns": turns,
    }


def reduce_tutor_presentation_recorded(
    state: JsonObject,
    event: DomainEvent,
    payload: SessionTutorPresentationRecorded,
) -> Mapping[str, JsonValue]:
    sessions, interactions, answers = _session_maps(state)
    session_id, session = _session(sessions, event)
    _chronological(session, event)
    _active(session)
    record = payload.record
    if record.session_id != event.session_id:
        raise ValueError("tutor presentation belongs to another session")
    if record.course_sequence != event.course_sequence:
        raise ValueError("tutor presentation course sequence must match event")
    if record.observed_host_context_sequence != event.course_sequence - 1:
        raise ValueError("tutor presentation must commit at its observed host sequence")
    if record.in_reply_to_interaction_id is not None:
        reply = interactions.get(str(record.in_reply_to_interaction_id))
        if (
            not isinstance(reply, Mapping)
            or reply.get("session_id") != session_id
            or reply.get("kind") != "human"
        ):
            raise ValueError("presentation reply target must be human and belong to the session")
    presentations = dict(
        _mapping(state.get("session_tutor_presentations", {}), "session_tutor_presentations")
    )
    presentation_id = str(record.id)
    if presentation_id in presentations:
        raise ValueError("tutor presentation id already exists")
    for raw in presentations.values():
        if not isinstance(raw, Mapping):
            raise ValueError("tutor presentation projection is corrupt")
        if raw.get("host_turn_id") == record.host_turn_id:
            raise ValueError("host turn id already belongs to a presentation")
        if (
            raw.get("session_id") == session_id
            and raw.get("idempotency_key") == record.idempotency_key
        ):
            raise ValueError("idempotency key already belongs to a presentation")
    for raw in answers.values():
        if isinstance(raw, Mapping) and raw.get("session_id") == session_id and raw.get(
            "idempotency_key"
        ) == record.idempotency_key:
            raise ValueError("idempotency key already belongs to a grounded answer")
    turns = _mapping(state.get("session_assistant_turns", {}), "session_assistant_turns")
    for raw in turns.values():
        if isinstance(raw, Mapping) and raw.get("session_id") == session_id and raw.get(
            "idempotency_key"
        ) == record.idempotency_key:
            raise ValueError("idempotency key already belongs to an assistant turn")
    presentations[presentation_id] = {
        "session_id": session_id,
        "presentation_id": presentation_id,
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
        "event_id": str(record.event_id),
        "course_sequence": record.course_sequence,
        "occurred_at": _timestamp(record.occurred_at),
    }
    session["last_event_at"] = _timestamp(event.occurred_at)
    sessions[session_id] = session
    return {
        **state,
        "sessions": sessions,
        "session_interactions": interactions,
        "session_answers": answers,
        "session_tutor_presentations": presentations,
    }


def reduce_summary_updated(
    state: JsonObject,
    event: DomainEvent,
    payload: SessionSummaryUpdated,
) -> Mapping[str, JsonValue]:
    sessions, interactions, answers = _session_maps(state)
    session_id, session = _session(sessions, event)
    _chronological(session, event)
    _active(session)
    ordered_interactions = _domain_interactions(session_id, session, interactions)
    domain_answers = _domain_answers(session_id, answers)
    verify_continuation_summary(payload.summary, ordered_interactions, domain_answers)
    current_raw = session.get("continuation_summary")
    if current_raw is not None:
        current = decode_summary_manifest(current_raw)
        positions = {item.id: index for index, item in enumerate(ordered_interactions)}
        if positions[payload.summary.through_interaction_id] <= positions[
            current.through_interaction_id
        ]:
            raise ValueError("continuation summary must advance monotonically")
    session["continuation_summary"] = summary_payload(payload.summary)["summary"]
    session["last_event_at"] = _timestamp(event.occurred_at)
    sessions[session_id] = session
    return {
        **state,
        "sessions": sessions,
        "session_interactions": interactions,
        "session_answers": answers,
    }


def reduce_suspended(
    state: JsonObject,
    event: DomainEvent,
    _: SessionLifecycleTransition,
) -> Mapping[str, JsonValue]:
    sessions, interactions, answers = _session_maps(state)
    session_id, session = _session(sessions, event)
    _chronological(session, event)
    _active(session)
    session["status"] = "suspended"
    session["suspended_at"] = _timestamp(event.occurred_at)
    session["last_event_at"] = _timestamp(event.occurred_at)
    sessions[session_id] = session
    return {
        **state,
        "sessions": sessions,
        "session_interactions": interactions,
        "session_answers": answers,
    }


def reduce_resumed(
    state: JsonObject,
    event: DomainEvent,
    _: SessionLifecycleTransition,
) -> Mapping[str, JsonValue]:
    sessions, interactions, answers = _session_maps(state)
    session_id, session = _session(sessions, event)
    _chronological(session, event)
    if session.get("status") != "suspended":
        raise ValueError("only a suspended session can resume")
    session["status"] = "active"
    session["resumed_at"] = _timestamp(event.occurred_at)
    session["last_event_at"] = _timestamp(event.occurred_at)
    sessions[session_id] = session
    return {
        **state,
        "sessions": sessions,
        "session_interactions": interactions,
        "session_answers": answers,
    }


def reduce_ended(
    state: JsonObject,
    event: DomainEvent,
    _: SessionLifecycleTransition,
) -> Mapping[str, JsonValue]:
    sessions, interactions, answers = _session_maps(state)
    session_id, session = _session(sessions, event)
    _chronological(session, event)
    if session.get("status") not in ("active", "suspended"):
        raise ValueError("ended session is terminal")
    session["status"] = "ended"
    session["ended_at"] = _timestamp(event.occurred_at)
    session["last_event_at"] = _timestamp(event.occurred_at)
    sessions[session_id] = session
    return {
        **state,
        "sessions": sessions,
        "session_interactions": interactions,
        "session_answers": answers,
    }


def register_session_events(registry: EventRegistry) -> None:
    registry.register_event(
        SESSION_STARTED, SESSION_SCHEMA_VERSION, decode_session_started, reduce_session_started
    )
    registry.register_event(
        SESSION_INTERACTION_RECORDED,
        SESSION_SCHEMA_VERSION,
        decode_interaction_recorded,
        reduce_interaction_recorded,
    )
    registry.register_event(
        SESSION_ANSWER_RECORDED,
        SESSION_SCHEMA_VERSION,
        decode_answer_recorded,
        reduce_answer_recorded,
    )
    registry.register_event(
        SESSION_ASSISTANT_TURN_RECORDED,
        SESSION_SCHEMA_VERSION,
        decode_assistant_turn_recorded,
        reduce_assistant_turn_recorded,
    )
    registry.register_event(
        SESSION_TUTOR_PRESENTATION_RECORDED,
        SESSION_SCHEMA_VERSION,
        decode_tutor_presentation_recorded,
        reduce_tutor_presentation_recorded,
    )
    registry.register_event(
        SESSION_CONTINUATION_SUMMARY_UPDATED,
        SESSION_SCHEMA_VERSION,
        decode_summary_updated,
        reduce_summary_updated,
    )
    registry.register_event(
        SESSION_SUSPENDED,
        SESSION_SCHEMA_VERSION,
        lambda event: decode_lifecycle(event, SESSION_SUSPENDED),
        reduce_suspended,
    )
    registry.register_event(
        SESSION_RESUMED,
        SESSION_SCHEMA_VERSION,
        lambda event: decode_lifecycle(event, SESSION_RESUMED),
        reduce_resumed,
    )
    registry.register_event(
        SESSION_ENDED,
        SESSION_SCHEMA_VERSION,
        lambda event: decode_lifecycle(event, SESSION_ENDED),
        reduce_ended,
    )


def _text_array(value: JsonValue | None, name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"projection field {name} must be an array of strings")
    return cast(tuple[str, ...], value)


def _answer_content(record: AnswerRecord) -> str:
    texts = tuple(segment.text for segment in record.answer.segments)
    if texts:
        return "\n\n".join(texts)
    note = record.answer.unsupported_information_note
    if note is None:  # guarded by GroundedAnswer invariants
        raise ValueError("persisted answer has no canonical assistant content")
    return note


def _domain_interactions(
    session_id: str,
    session: Mapping[str, JsonValue],
    interactions: Mapping[str, JsonValue],
) -> tuple[InteractionRecord, ...]:
    result: list[InteractionRecord] = []
    for interaction_id in _text_array(session.get("interaction_ids"), "interaction_ids"):
        raw = interactions.get(interaction_id)
        if not isinstance(raw, Mapping) or raw.get("session_id") != session_id:
            raise ValueError("interaction projection linkage is corrupt")
        result.append(_decode_interaction(interaction_id, raw))
    return tuple(result)


def _domain_answers(
    session_id: str, answers: Mapping[str, JsonValue]
) -> dict[str, AnswerRecord]:
    result: dict[str, AnswerRecord] = {}
    for answer_id, raw in answers.items():
        if isinstance(raw, Mapping) and raw.get("session_id") == session_id:
            result[answer_id] = _decode_answer(answer_id, raw)
    return result


def _decode_interaction(interaction_id: str, raw: Mapping[str, JsonValue]) -> InteractionRecord:
    expected = {
        "session_id",
        "kind",
        "occurred_at",
        "content",
        "answer_id",
        "run_id",
    }
    if set(raw) != expected:
        raise ValueError("interaction projection fields are corrupt")
    try:
        kind_raw = raw["kind"]
        occurred_raw = raw["occurred_at"]
        content_raw = raw["content"]
    except (KeyError, ValueError) as error:
        raise ValueError("interaction projection is corrupt") from error
    if not isinstance(kind_raw, str) or not isinstance(occurred_raw, str):
        raise ValueError("interaction projection type is corrupt")
    if not isinstance(content_raw, str):
        raise ValueError("interaction projection content is corrupt")
    try:
        kind = InteractionKind(kind_raw)
        occurred_at = datetime.fromisoformat(occurred_raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("interaction projection is corrupt") from error
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("interaction projection timestamp must be timezone-aware")
    answer_raw = raw.get("answer_id")
    run_raw = raw.get("run_id")
    if answer_raw is not None and not isinstance(answer_raw, str):
        raise ValueError("interaction projection answer_id is corrupt")
    if run_raw is not None and not isinstance(run_raw, str):
        raise ValueError("interaction projection run_id is corrupt")
    return InteractionRecord(
        InteractionId(interaction_id),
        kind,
        occurred_at,
        content_raw,
        AnswerId(answer_raw) if isinstance(answer_raw, str) else None,
        RunId(run_raw) if isinstance(run_raw, str) else None,
    )


def _decode_answer(answer_id: str, raw: Mapping[str, JsonValue]) -> AnswerRecord:
    required = {
        "session_id",
        "answer_id",
        "interaction_id",
        "question_interaction_id",
        "run_id",
        "idempotency_key",
        "command_fingerprint",
        "answer",
    }
    if set(raw) != required or raw.get("answer_id") != answer_id:
        raise ValueError("answer projection is corrupt")
    text_fields = (
        "session_id",
        "interaction_id",
        "question_interaction_id",
        "run_id",
        "idempotency_key",
        "command_fingerprint",
    )
    if any(not isinstance(raw.get(key), str) for key in text_fields):
        raise ValueError("answer projection field types are corrupt")
    return AnswerRecord(
        AnswerId(answer_id),
        InteractionId(cast(str, raw["interaction_id"])),
        InteractionId(cast(str, raw["question_interaction_id"])),
        RunId(cast(str, raw["run_id"])),
        cast(str, raw["idempotency_key"]),
        cast(str, raw["command_fingerprint"]),
        decode_grounded_answer_manifest(raw["answer"]),
    )
