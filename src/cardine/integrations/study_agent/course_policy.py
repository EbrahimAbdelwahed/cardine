"""Cardine-owned provider-consent policy on the canonical course stream."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Protocol

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.context import ExecutionContext
from study_agent.domain.events import Actor, DomainEvent, PrincipalKind
from study_agent.domain.identifiers import CourseId, EventId
from study_agent.ports.clock import ClockPort
from study_agent.ports.course import CourseViewPort
from study_agent.ports.model import (
    CancellationToken,
    ModelCapabilities,
    ModelPort,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
)
from study_agent.ports.storage import EventSequenceConflictError, EventStore
from study_agent.state import EventRegistry, Projection
from study_agent.state.serialization import canonical_json_bytes

PROVIDER_CONSENT_GRANTED = "cardine.provider_consent_granted"
PROVIDER_CONSENT_REVOKED = "cardine.provider_consent_revoked"
COURSE_POLICY_SCHEMA_VERSION = 1

_PAYLOAD_KEYS = frozenset(
    {"course_id", "principal_id", "request_id", "occurred_at", "status"}
)
_GRANTED = "granted"
_REVOKED = "revoked"


class ConsentCommandError(ValueError):
    """A consent command is invalid or lacks HUMAN authority."""


class ConsentConflictError(ConsentCommandError):
    """A request id was previously committed with different intent."""


class RetryableConsentConflictError(RuntimeError):
    """The course stream advanced before this command committed."""


class ProviderConsentRequiredError(RuntimeError):
    """A provider call was blocked before any request left Cardine."""


@dataclass(frozen=True, slots=True)
class ConsentReceipt:
    course_id: CourseId
    principal_id: str
    request_id: str
    occurred_at: datetime
    status: str
    sequence: int

    @property
    def granted(self) -> bool:
        return self.status == _GRANTED


@dataclass(frozen=True, slots=True)
class _ConsentPayload:
    course_id: CourseId
    principal_id: str
    request_id: str
    occurred_at: datetime
    status: str


def _text(value: JsonValue | None, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty trimmed text")
    return value


def _payload_manifest(
    course_id: CourseId,
    principal_id: str,
    request_id: str,
    occurred_at: datetime,
    status: str,
) -> JsonObject:
    return {
        "course_id": str(course_id),
        "principal_id": principal_id,
        "request_id": request_id,
        "occurred_at": occurred_at.isoformat(),
        "status": status,
    }


def _decode_consent(event: DomainEvent) -> _ConsentPayload:
    expected_status = {
        PROVIDER_CONSENT_GRANTED: _GRANTED,
        PROVIDER_CONSENT_REVOKED: _REVOKED,
    }.get(event.event_type)
    if expected_status is None or event.schema_version != COURSE_POLICY_SCHEMA_VERSION:
        raise ValueError("event envelope does not match a consent schema")
    if event.session_id is not None or event.causation_id is not None:
        raise ValueError("consent events cannot be session-scoped or caused")
    if event.actor.kind is not PrincipalKind.HUMAN:
        raise ValueError("provider consent requires HUMAN authority")
    payload = event.payload
    if not isinstance(payload, Mapping) or frozenset(payload) != _PAYLOAD_KEYS:
        raise ValueError("consent payload fields mismatch")
    course_id = CourseId(_text(payload.get("course_id"), "course_id"))
    principal_id = _text(payload.get("principal_id"), "principal_id")
    request_id = _text(payload.get("request_id"), "request_id")
    occurred_at_text = _text(payload.get("occurred_at"), "occurred_at")
    status = _text(payload.get("status"), "status")
    try:
        occurred_at = datetime.fromisoformat(occurred_at_text)
    except ValueError as error:
        raise ValueError("occurred_at must be ISO-8601") from error
    if occurred_at.isoformat() != occurred_at_text or occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be canonical and timezone-aware")
    if course_id != event.course_id or principal_id != event.actor.principal_id:
        raise ValueError("consent payload identity does not match its envelope")
    if occurred_at != event.occurred_at or status != expected_status:
        raise ValueError("consent payload state does not match its envelope")
    expected_id = consent_event_id(course_id, principal_id, request_id, status)
    if event.event_id != expected_id:
        raise ValueError("consent event id is not canonical")
    return _ConsentPayload(course_id, principal_id, request_id, occurred_at, status)


def _reduce_consent(
    state: JsonObject, event: DomainEvent, payload: _ConsentPayload
) -> Mapping[str, JsonValue]:
    raw_policy = state.get("consent", {})
    if not isinstance(raw_policy, Mapping):
        raise ValueError("consent projection is corrupt")
    raw_requests = raw_policy.get("requests", {})
    if not isinstance(raw_requests, Mapping):
        raise ValueError("consent request projection is corrupt")
    intent = f"{payload.status}:{payload.principal_id}"
    prior = raw_requests.get(payload.request_id)
    if prior is not None and prior != intent:
        raise ValueError("consent request id was reused with different intent")
    requests = {**raw_requests, payload.request_id: intent}
    receipt = {
        **_payload_manifest(
            payload.course_id,
            payload.principal_id,
            payload.request_id,
            payload.occurred_at,
            payload.status,
        ),
        "sequence": event.course_sequence,
    }
    return {**state, "consent": {**receipt, "requests": requests}}


def register_course_policy_events(registry: EventRegistry) -> None:
    for event_type in (PROVIDER_CONSENT_GRANTED, PROVIDER_CONSENT_REVOKED):
        registry.register_event(
            event_type,
            COURSE_POLICY_SCHEMA_VERSION,
            _decode_consent,
            _reduce_consent,
        )


def consent_event_id(
    course_id: CourseId, principal_id: str, request_id: str, status: str
) -> EventId:
    identity = canonical_json_bytes(
        {
            "course_id": str(course_id),
            "principal_id": principal_id,
            "request_id": request_id,
            "status": status,
        }
    )
    return EventId(f"event-sha256:{sha256(identity).hexdigest()}")


type ProjectionLoader = Callable[[CourseId], Projection]


class ProjectionConsentView:
    def __init__(self, load_projection: ProjectionLoader) -> None:
        self._load_projection = load_projection

    def get(self, course_id: CourseId) -> ConsentReceipt | None:
        projection = self._load_projection(course_id)
        raw = projection.state.get("consent")
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise ValueError("consent projection is corrupt")
        occurred_at = datetime.fromisoformat(_text(raw.get("occurred_at"), "occurred_at"))
        sequence = raw.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise ValueError("consent sequence is corrupt")
        return ConsentReceipt(
            CourseId(_text(raw.get("course_id"), "course_id")),
            _text(raw.get("principal_id"), "principal_id"),
            _text(raw.get("request_id"), "request_id"),
            occurred_at,
            _text(raw.get("status"), "status"),
            sequence,
        )


class CourseConsentService:
    def __init__(
        self,
        events: EventStore,
        clock: ClockPort,
        view: ProjectionConsentView,
        courses: CourseViewPort,
    ) -> None:
        self._events = events
        self._clock = clock
        self._view = view
        self._courses = courses

    def grant(
        self,
        context: ExecutionContext,
        request_id: str,
        *,
        expected_sequence: int | None = None,
    ) -> ConsentReceipt:
        return self._record(context, request_id, _GRANTED, expected_sequence)

    def revoke(
        self,
        context: ExecutionContext,
        request_id: str,
        *,
        expected_sequence: int | None = None,
    ) -> ConsentReceipt:
        return self._record(context, request_id, _REVOKED, expected_sequence)

    def _record(
        self,
        context: ExecutionContext,
        request_id: str,
        status: str,
        expected_sequence: int | None,
    ) -> ConsentReceipt:
        if context.principal_kind is not PrincipalKind.HUMAN:
            raise ConsentCommandError("provider consent requires HUMAN authority")
        if context.session_id is not None or context.model_run_id is not None:
            raise ConsentCommandError("provider consent must be course-scoped")
        self._courses.get(context.course_id)
        request_id = _text(request_id, "request_id")
        stream = tuple(self._events.read(context.course_id))
        sequence = stream[-1].course_sequence if stream else 0
        event_id = consent_event_id(
            context.course_id, context.principal_id, request_id, status
        )
        for prior in stream:
            if prior.event_id == event_id:
                return _receipt_from_event(prior)
            if prior.event_type in (PROVIDER_CONSENT_GRANTED, PROVIDER_CONSENT_REVOKED):
                decoded = _decode_consent(prior)
                if decoded.request_id == request_id:
                    raise ConsentConflictError(
                        "consent request id was previously used with different intent"
                    )
        if expected_sequence is not None and sequence != expected_sequence:
            raise RetryableConsentConflictError(
                f"expected course sequence {expected_sequence}; observed {sequence}"
            )
        current = self._view.get(context.course_id)
        if status == _GRANTED and current is not None and current.granted:
            raise ConsentCommandError("provider consent is already granted")
        if status == _REVOKED and (current is None or not current.granted):
            raise ConsentCommandError("provider consent is not currently granted")
        occurred_at = self._clock.now()
        event_type = (
            PROVIDER_CONSENT_GRANTED if status == _GRANTED else PROVIDER_CONSENT_REVOKED
        )
        event = DomainEvent(
            event_id,
            context.course_id,
            sequence + 1,
            event_type,
            COURSE_POLICY_SCHEMA_VERSION,
            Actor(PrincipalKind.HUMAN, context.principal_id),
            occurred_at,
            context.correlation_id,
            _payload_manifest(
                context.course_id,
                context.principal_id,
                request_id,
                occurred_at,
                status,
            ),
        )
        try:
            self._events.append(context.course_id, sequence, (event,))
        except EventSequenceConflictError as error:
            for prior in self._events.read(context.course_id):
                if prior.event_id == event_id:
                    return _receipt_from_event(prior)
            raise RetryableConsentConflictError(
                "course stream advanced before consent committed"
            ) from error
        receipt = self._view.get(context.course_id)
        if receipt is None or receipt.request_id != request_id:
            raise RuntimeError("committed consent receipt is unavailable")
        return receipt


def _receipt_from_event(event: DomainEvent) -> ConsentReceipt:
    payload = _decode_consent(event)
    return ConsentReceipt(
        payload.course_id,
        payload.principal_id,
        payload.request_id,
        payload.occurred_at,
        payload.status,
        event.course_sequence,
    )


class ConsentModelPort:
    """Fail closed before delegating any provider operation."""

    def __init__(self, model: ModelPort, course_id: CourseId, view: ProjectionConsentView) -> None:
        self._model = model
        self._course_id = course_id
        self._view = view

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._model.capabilities

    def _require_granted(self) -> None:
        receipt = self._view.get(self._course_id)
        if receipt is None or not receipt.granted:
            raise ProviderConsentRequiredError("provider consent is required")

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self._require_granted()
        return await self._model.generate(request)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        self._require_granted()
        iterator = self._model.stream(request)
        while True:
            self._require_granted()
            try:
                yield await iterator.__anext__()
            except StopAsyncIteration:
                return

    async def cancel(self, token: CancellationToken) -> None:
        self._require_granted()
        await self._model.cancel(token)


class ConsentViewPort(Protocol):
    def get(self, course_id: CourseId) -> ConsentReceipt | None: ...


__all__ = (
    "ConsentCommandError",
    "ConsentConflictError",
    "ConsentModelPort",
    "ConsentReceipt",
    "CourseConsentService",
    "ProjectionConsentView",
    "ProviderConsentRequiredError",
    "RetryableConsentConflictError",
    "register_course_policy_events",
)
