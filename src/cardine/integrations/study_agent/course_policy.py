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
from study_agent.domain.identifiers import CourseId, EventId, SourceId
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
SOURCE_RETIRED = "cardine.source_retired"
SOURCE_RESTORED = "cardine.source_restored"
COURSE_POLICY_SCHEMA_VERSION = 1

_PAYLOAD_KEYS = frozenset({"course_id", "principal_id", "request_id", "occurred_at", "status"})
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


class SourceLifetimeCommandError(ValueError):
    """A source retirement command is invalid or lacks HUMAN authority."""


class RetryableSourceLifetimeConflictError(RuntimeError):
    """The course stream advanced before a source lifetime command committed."""


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
    for event_type in (SOURCE_RETIRED, SOURCE_RESTORED):
        registry.register_event(
            event_type,
            COURSE_POLICY_SCHEMA_VERSION,
            _decode_source_lifetime,
            _reduce_source_lifetime,
        )


@dataclass(frozen=True, slots=True)
class SourceLifetimeReceipt:
    course_id: CourseId
    source_id: SourceId
    principal_id: str
    request_id: str
    occurred_at: datetime
    status: str
    sequence: int

    @property
    def retired(self) -> bool:
        return self.status == "retired"


@dataclass(frozen=True, slots=True)
class _SourceLifetimePayload:
    course_id: CourseId
    source_id: SourceId
    principal_id: str
    request_id: str
    occurred_at: datetime
    status: str


def source_lifetime_event_id(
    course_id: CourseId, source_id: SourceId, principal_id: str, request_id: str, status: str
) -> EventId:
    identity = canonical_json_bytes(
        {
            "course_id": str(course_id),
            "source_id": str(source_id),
            "principal_id": principal_id,
            "request_id": request_id,
            "status": status,
        }
    )
    return EventId(f"event-sha256:{sha256(identity).hexdigest()}")


def _decode_source_lifetime(event: DomainEvent) -> _SourceLifetimePayload:
    expected = {SOURCE_RETIRED: "retired", SOURCE_RESTORED: "restored"}.get(event.event_type)
    if expected is None or event.schema_version != COURSE_POLICY_SCHEMA_VERSION:
        raise ValueError("event envelope does not match a source lifetime schema")
    if (
        event.session_id is not None
        or event.causation_id is not None
        or event.actor.kind is not PrincipalKind.HUMAN
    ):
        raise ValueError("source lifetime events require a course-scoped HUMAN actor")
    if frozenset(event.payload) != _PAYLOAD_KEYS | {"source_id"}:
        raise ValueError("source lifetime payload fields mismatch")
    course_id = CourseId(_text(event.payload.get("course_id"), "course_id"))
    source_id = SourceId(_text(event.payload.get("source_id"), "source_id"))
    principal_id = _text(event.payload.get("principal_id"), "principal_id")
    request_id = _text(event.payload.get("request_id"), "request_id")
    occurred_text = _text(event.payload.get("occurred_at"), "occurred_at")
    status = _text(event.payload.get("status"), "status")
    occurred_at = datetime.fromisoformat(occurred_text)
    if occurred_at.tzinfo is None or occurred_at.isoformat() != occurred_text:
        raise ValueError("occurred_at must be canonical and timezone-aware")
    if (
        course_id != event.course_id
        or principal_id != event.actor.principal_id
        or occurred_at != event.occurred_at
        or status != expected
    ):
        raise ValueError("source lifetime payload does not match its envelope")
    if event.event_id != source_lifetime_event_id(
        course_id, source_id, principal_id, request_id, status
    ):
        raise ValueError("source lifetime event id is not canonical")
    return _SourceLifetimePayload(
        course_id, source_id, principal_id, request_id, occurred_at, status
    )


def _reduce_source_lifetime(
    state: JsonObject, event: DomainEvent, payload: _SourceLifetimePayload
) -> Mapping[str, JsonValue]:
    raw_records = state.get("source_lifetime", {})
    records = dict(raw_records) if isinstance(raw_records, Mapping) else {}
    raw_requests = state.get("source_lifetime_requests", {})
    requests = dict(raw_requests) if isinstance(raw_requests, Mapping) else {}
    intent = f"{payload.status}:{payload.source_id}:{payload.principal_id}"
    prior = requests.get(payload.request_id)
    if prior is not None and prior != intent:
        raise ValueError("source lifetime request id was reused with different intent")
    requests[payload.request_id] = intent
    records[str(payload.source_id)] = {
        "source_id": str(payload.source_id),
        "principal_id": payload.principal_id,
        "request_id": payload.request_id,
        "occurred_at": payload.occurred_at.isoformat(),
        "status": payload.status,
        "sequence": event.course_sequence,
    }
    return {**state, "source_lifetime": records, "source_lifetime_requests": requests}


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


class ProjectionSourceLifetimeView:
    def __init__(self, load_projection: ProjectionLoader) -> None:
        self._load_projection = load_projection

    def get(self, course_id: CourseId, source_id: SourceId) -> SourceLifetimeReceipt | None:
        raw_state = self._load_projection(course_id).state.get("source_lifetime", {})
        if not isinstance(raw_state, Mapping):
            raise ValueError("source lifetime projection is corrupt")
        raw = raw_state.get(str(source_id))
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise ValueError("source lifetime receipt is corrupt")
        sequence = raw.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise ValueError("source lifetime sequence is corrupt")
        return SourceLifetimeReceipt(
            course_id,
            SourceId(_text(raw.get("source_id"), "source_id")),
            _text(raw.get("principal_id"), "principal_id"),
            _text(raw.get("request_id"), "request_id"),
            datetime.fromisoformat(_text(raw.get("occurred_at"), "occurred_at")),
            _text(raw.get("status"), "status"),
            sequence,
        )

    def retired_source_ids(self, course_id: CourseId) -> frozenset[SourceId]:
        raw_state = self._load_projection(course_id).state.get("source_lifetime", {})
        if not isinstance(raw_state, Mapping):
            raise ValueError("source lifetime projection is corrupt")
        return frozenset(
            SourceId(str(source_id))
            for source_id, raw in raw_state.items()
            if isinstance(raw, Mapping) and raw.get("status") == "retired"
        )


class SourceIdentityRecord(Protocol):
    @property
    def source_id(self) -> SourceId: ...


class SourceCatalogRecord(Protocol):
    @property
    def source(self) -> SourceIdentityRecord: ...


class SourceCatalogPort(Protocol):
    def catalog(self) -> tuple[SourceCatalogRecord, ...]: ...


class SourceLifetimeService:
    def __init__(
        self,
        events: EventStore,
        clock: ClockPort,
        view: ProjectionSourceLifetimeView,
        courses: CourseViewPort,
        content: Callable[[CourseId], SourceCatalogPort],
    ) -> None:
        self._events, self._clock, self._view, self._courses, self._content = (
            events,
            clock,
            view,
            courses,
            content,
        )

    def retire(
        self,
        context: ExecutionContext,
        source_id: SourceId,
        request_id: str,
        *,
        expected_sequence: int | None = None,
    ) -> SourceLifetimeReceipt:
        return self._record(context, source_id, request_id, "retired", expected_sequence)

    def restore(
        self,
        context: ExecutionContext,
        source_id: SourceId,
        request_id: str,
        *,
        expected_sequence: int | None = None,
    ) -> SourceLifetimeReceipt:
        return self._record(context, source_id, request_id, "restored", expected_sequence)

    def _record(
        self,
        context: ExecutionContext,
        source_id: SourceId,
        request_id: str,
        status: str,
        expected_sequence: int | None,
    ) -> SourceLifetimeReceipt:
        if (
            context.principal_kind is not PrincipalKind.HUMAN
            or context.session_id is not None
            or context.model_run_id is not None
        ):
            raise SourceLifetimeCommandError(
                "source lifetime changes require a course-scoped HUMAN actor"
            )
        if type(source_id) is not SourceId:
            raise SourceLifetimeCommandError("source_id is invalid")
        request_id = _text(request_id, "request_id")
        self._courses.get(context.course_id)
        records = self._content(context.course_id).catalog()
        if not any(item.source.source_id == source_id for item in records):
            raise SourceLifetimeCommandError("source was not found")
        stream = tuple(self._events.read(context.course_id))
        sequence = stream[-1].course_sequence if stream else 0
        event_id = source_lifetime_event_id(
            context.course_id, source_id, context.principal_id, request_id, status
        )
        for prior in stream:
            if prior.event_id == event_id:
                return _source_receipt_from_event(prior)
            if (
                prior.event_type in (SOURCE_RETIRED, SOURCE_RESTORED)
                and _decode_source_lifetime(prior).request_id == request_id
            ):
                raise SourceLifetimeCommandError(
                    "source lifetime request id was previously used with different intent"
                )
        if expected_sequence is not None and sequence != expected_sequence:
            raise RetryableSourceLifetimeConflictError(
                "course stream advanced before source lifetime command"
            )
        current = self._view.get(context.course_id, source_id)
        if status == "retired" and current is not None and current.retired:
            raise SourceLifetimeCommandError("source is already retired")
        if status == "restored" and (current is None or not current.retired):
            raise SourceLifetimeCommandError("source is not currently retired")
        occurred_at = self._clock.now()
        event = DomainEvent(
            event_id,
            context.course_id,
            sequence + 1,
            SOURCE_RETIRED if status == "retired" else SOURCE_RESTORED,
            COURSE_POLICY_SCHEMA_VERSION,
            Actor(PrincipalKind.HUMAN, context.principal_id),
            occurred_at,
            context.correlation_id,
            {
                **_payload_manifest(
                    context.course_id, context.principal_id, request_id, occurred_at, status
                ),
                "source_id": str(source_id),
            },
        )
        try:
            self._events.append(context.course_id, sequence, (event,))
        except EventSequenceConflictError as error:
            if any(item.event_id == event_id for item in self._events.read(context.course_id)):
                return _source_receipt_from_event(
                    next(
                        item
                        for item in self._events.read(context.course_id)
                        if item.event_id == event_id
                    )
                )
            raise RetryableSourceLifetimeConflictError(
                "course stream advanced before source lifetime command"
            ) from error
        receipt = self._view.get(context.course_id, source_id)
        if receipt is None:
            raise RuntimeError("committed source lifetime receipt is unavailable")
        return receipt


def _source_receipt_from_event(event: DomainEvent) -> SourceLifetimeReceipt:
    payload = _decode_source_lifetime(event)
    return SourceLifetimeReceipt(
        payload.course_id,
        payload.source_id,
        payload.principal_id,
        payload.request_id,
        payload.occurred_at,
        payload.status,
        event.course_sequence,
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
        event_id = consent_event_id(context.course_id, context.principal_id, request_id, status)
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
        event_type = PROVIDER_CONSENT_GRANTED if status == _GRANTED else PROVIDER_CONSENT_REVOKED
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
