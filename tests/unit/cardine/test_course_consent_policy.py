from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from cardine.integrations.study_agent.course_policy import (
    ConsentConflictError,
    ConsentModelPort,
    CourseConsentService,
    ProjectionConsentView,
    ProviderConsentRequiredError,
    register_course_policy_events,
)
from study_agent.domain.context import ExecutionContext
from study_agent.domain.events import DomainEvent, PrincipalKind
from study_agent.domain.identifiers import CorrelationId, CourseId
from study_agent.ports.course import CourseNotFoundError
from study_agent.ports.model import (
    CancellationToken,
    MessageRole,
    ModelCapabilities,
    ModelFinishReason,
    ModelInvocation,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelStreamEventKind,
)
from study_agent.ports.storage import EventSequenceConflictError
from study_agent.state import EventRegistry, Projection, apply_event

COURSE = CourseId("course-consent")
NOW = datetime(2026, 8, 12, 18, 30, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Events:
    def __init__(self, registry: EventRegistry) -> None:
        self.records: list[DomainEvent] = []
        self.projection = Projection(COURSE)
        self._registry = registry

    def read(self, course_id: CourseId, after_sequence: int = 0):  # type: ignore[no-untyped-def]
        assert course_id == COURSE
        return tuple(item for item in self.records if item.course_sequence > after_sequence)

    def append(self, course_id: CourseId, expected_sequence: int, events):  # type: ignore[no-untyped-def]
        if expected_sequence != len(self.records):
            raise EventSequenceConflictError(course_id, expected_sequence, len(self.records))
        for event in events:
            self.projection = apply_event(self.projection, event, self._registry)
            self.records.append(event)
        return len(self.records)


class _RacingEvents(_Events):
    def __init__(self, registry: EventRegistry) -> None:
        super().__init__(registry)
        self.race_once = True

    def append(self, course_id: CourseId, expected_sequence: int, events):  # type: ignore[no-untyped-def]
        if self.race_once:
            self.race_once = False
            super().append(course_id, expected_sequence, events)
            raise EventSequenceConflictError(course_id, expected_sequence, len(self.records))
        return super().append(course_id, expected_sequence, events)


class _Courses:
    def __init__(self, *, present: bool = True) -> None:
        self.present = present

    def get(self, course_id: CourseId):  # type: ignore[no-untyped-def]
        if not self.present:
            raise CourseNotFoundError(course_id)
        return object()


class _Model:
    capabilities = ModelCapabilities()

    def __init__(self) -> None:
        self.calls = 0
        self.stream_calls = 0
        self.cancel_calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            "ok",
            None,
            ModelFinishReason.STOP,
            ModelInvocation("test", "1", "test"),
        )

    async def stream(self, request: ModelRequest):  # type: ignore[no-untyped-def]
        del request
        self.stream_calls += 1
        yield ModelStreamEvent(ModelStreamEventKind.CONTENT_DELTA, content_delta="ok")

    async def cancel(self, token: CancellationToken) -> None:
        del token
        self.cancel_calls += 1


def _context(kind: PrincipalKind = PrincipalKind.HUMAN) -> ExecutionContext:
    return ExecutionContext(kind, "person-1", COURSE, CorrelationId("correlation-1"))


def _composition(
    *, events_type: type[_Events] = _Events, course_present: bool = True
):  # type: ignore[no-untyped-def]
    registry = EventRegistry()
    register_course_policy_events(registry)
    events = events_type(registry)
    view = ProjectionConsentView(lambda _: events.projection)
    return events, view, CourseConsentService(
        events, _Clock(), view, _Courses(present=course_present)
    )


def test_consent_replay_is_human_only_and_retry_stable_after_revoke() -> None:
    events, view, service = _composition()
    granted = service.grant(_context(), "grant-1")
    revoked = service.revoke(_context(), "revoke-1", expected_sequence=1)

    assert granted.granted is True
    assert revoked.granted is False
    assert view.get(COURSE) == revoked
    assert service.grant(_context(), "grant-1", expected_sequence=1) == granted
    assert len(events.records) == 2

    with pytest.raises(ConsentConflictError):
        service.revoke(_context(), "grant-1")
    with pytest.raises(ValueError, match="not currently granted"):
        service.revoke(_context(), "revoke-again")
    with pytest.raises(ValueError, match="HUMAN"):
        service.grant(_context(PrincipalKind.MODEL), "model-request")


def test_model_firewall_blocks_absent_and_revoked_consent_before_provider() -> None:
    _, view, service = _composition()
    model = _Model()
    gated = ConsentModelPort(model, COURSE, view)
    request = ModelRequest((ModelMessage(MessageRole.USER, "Studia"),))

    with pytest.raises(ProviderConsentRequiredError):
        asyncio.run(gated.generate(request))
    assert model.calls == 0

    service.grant(_context(), "grant-1")
    assert asyncio.run(gated.generate(request)).content == "ok"
    assert model.calls == 1

    service.revoke(_context(), "revoke-1")
    with pytest.raises(ProviderConsentRequiredError):
        asyncio.run(gated.generate(request))
    assert model.calls == 1

    async def consume_stream() -> None:
        async for _ in gated.stream(request):
            pass

    with pytest.raises(ProviderConsentRequiredError):
        asyncio.run(consume_stream())
    with pytest.raises(ProviderConsentRequiredError):
        asyncio.run(gated.cancel(CancellationToken("cancel-1")))
    assert model.stream_calls == 0
    assert model.cancel_calls == 0


def test_consent_requires_course_and_concurrent_identical_request_converges() -> None:
    events, _, missing_service = _composition(course_present=False)
    with pytest.raises(CourseNotFoundError):
        missing_service.grant(_context(), "missing-course")
    assert events.records == []

    racing, _, service = _composition(events_type=_RacingEvents)
    receipt = service.grant(_context(), "same-request")
    assert receipt.request_id == "same-request"
    assert len(racing.records) == 1
