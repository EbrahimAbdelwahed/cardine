from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from cardine.courses import ProjectionCourseView, register_course_events
from study_agent.adapters.sqlite import SQLiteEventStore
from study_agent.domain import (
    AnswerRecord,
    ContinuationSummaryV1,
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    RunId,
    SessionId,
    question_interaction_id_for,
)
from study_agent.grounding import EvidenceEnvelope
from study_agent.playbooks import (
    PlaybookEngine,
    PlaybookRunStatus,
    ReadDependency,
    StepTrace,
    StepTraceStatus,
    ToolBehaviorPin,
    ValidationOutcome,
    ValidatorDisposition,
    VerifiedRunRecord,
    VersionPins,
)
from study_agent.playbooks.builtin import GROUNDED_ANSWER_FLOW
from study_agent.ports import EvidenceStatus, RetrievalEvidenceSet, retrieval_read_set_fingerprint
from study_agent.sessions import (
    GroundedSessionFinalizer,
    IdempotencyConflictError,
    ProjectionSessionView,
    RetryableSessionConflictError,
    SessionCommandError,
    SessionService,
    StateWritePolicyError,
    register_session_events,
)
from study_agent.skills import ArtifactReference, SemanticVersion, StateWritePolicy
from study_agent.skills.builtin import GROUNDED_ANSWER_SKILL
from study_agent.state import EventRegistry
from tests.course_fixtures import create_canonical_course

NOW = datetime(2026, 7, 11, 16, tzinfo=UTC)
V1 = SemanticVersion.parse("1.0.0")
COURSE = CourseId("course-1")
SESSION = SessionId("session-1")


class Clock:
    def now(self) -> datetime:
        return NOW


class NoContent:
    def get_text(self, revision_id):  # type: ignore[no-untyped-def]
        raise AssertionError("insufficient finalization must not read source text")

    def resolve(self, citation):  # type: ignore[no-untyped-def]
        raise AssertionError("insufficient finalization must not resolve citations")


def _context(*, correlation: str = "correlation-1") -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "test-service",
        COURSE,
        CorrelationId(correlation),
        session_id=SESSION,
    )


def _store(path: Path) -> tuple[SQLiteEventStore, ProjectionSessionView]:
    registry = EventRegistry()
    register_course_events(registry)
    register_session_events(registry)
    events = SQLiteEventStore(path, registry)
    create_canonical_course(events, COURSE)
    return events, ProjectionSessionView(events.projection)


def _service(events: SQLiteEventStore, view: ProjectionSessionView) -> SessionService:
    return SessionService(events, Clock(), view, ProjectionCourseView(events.projection))


def _pins() -> VersionPins:
    return VersionPins(
        ArtifactReference("grounded_answer", V1),
        ArtifactReference("grounded_answer_flow", V1),
        ArtifactReference("grounded_answer.v1", V1),
        (
            ToolBehaviorPin("session.get_context", V1),
            ToolBehaviorPin("source.search", V1),
        ),
        ArtifactReference("scripted-model", V1),
        ArtifactReference("event_state", V1),
    )


def _insufficient_run(*, question: str = "What is absent?") -> VerifiedRunRecord:
    evidence = EvidenceEnvelope.from_retrieval(
        RetrievalEvidenceSet(
            EvidenceStatus.INSUFFICIENT,
            (),
            "a" * 64,
            "sqlite_fts5",
            "1.0.0",
            "index-v1",
            retrieval_read_set_fingerprint(()),
        )
    ).to_json()
    termination = ValidationOutcome(
        True,
        ValidatorDisposition.TERMINATE,
        {
            "status": "insufficient_evidence",
            "segments": (),
            "unsupported_information_note": "No source evidence was found.",
        },
        "retrieval returned insufficient evidence",
    )
    trace = StepTrace(
        "check_evidence",
        "validate",
        StepTraceStatus.COMPLETED,
        NOW,
        {
            "output_fingerprint": "f" * 64,
            "validator": {
                "validator_id": "evidence_sufficiency",
                "validator_version": "1.0.0",
                "passed": True,
                "disposition": "terminate",
                "result_fingerprint": "b" * 64,
                "reason": "retrieval returned insufficient evidence",
            },
        },
    )
    return VerifiedRunRecord(
        RunId("run-1"),
        "d" * 64,
        {"course_id": str(COURSE), "session_id": str(SESSION), "question": question},
        _pins(),
        (ReadDependency("source", "course-1", "revision-set-1"),),
        {"evidence": evidence, "evidence_gate": termination.result},
        (trace,),
        PlaybookRunStatus.TERMINATED,
        termination,
    )


class RecoveryEngine:
    def __init__(self, run: VerifiedRunRecord) -> None:
        self.run = run

    def recover(self, **kwargs: object) -> VerifiedRunRecord:
        assert kwargs["run_id"] == self.run.run_id
        assert kwargs["inputs"] == self.run.inputs
        assert kwargs["pins"] == self.run.pins
        assert kwargs["read_dependencies"] == self.run.read_dependencies
        return self.run


class SideEffectRecoveryEngine(RecoveryEngine):
    def __init__(self, run: VerifiedRunRecord, effect: Callable[[], object]) -> None:
        super().__init__(run)
        self._effect = effect

    def recover(self, **kwargs: object) -> VerifiedRunRecord:
        self._effect()
        return super().recover(**kwargs)


def _finalize(
    finalizer: GroundedSessionFinalizer,
    run: VerifiedRunRecord,
    key: str,
) -> AnswerRecord:
    return finalizer.finalize_grounded_run(
        context=_context(),
        engine=cast(PlaybookEngine, RecoveryEngine(run)),
        run_id=run.run_id,
        definition=GROUNDED_ANSWER_FLOW,
        inputs=run.inputs,
        pins=run.pins,
        read_dependencies=run.read_dependencies,
        idempotency_key=key,
    )


def test_lifecycle_is_event_sourced_and_context_is_bounded(tmp_path: Path) -> None:
    events, view = _store(tmp_path / "events.sqlite3")
    service = _service(events, view)

    started = service.start(_context())
    assert started.status.value == "active"
    note = service.record_note(_context(correlation="note-1"), "Review valve anatomy.")
    assert note.content == "Review valve anatomy."
    assert service.suspend(_context(correlation="suspend-1")).status.value == "suspended"
    assert service.resume(_context(correlation="resume-1")).status.value == "active"
    context = service.get_context(_context())
    assert context is not None
    assert context.unresolved_notes == ("Review valve anatomy.",)
    assert service.end(_context(correlation="end-1")).status.value == "ended"
    with pytest.raises(SessionCommandError, match="active"):
        service.record_note(_context(correlation="late"), "Too late")
    assert events.verify_projection(COURSE)


def test_insufficient_finalize_is_atomic_idempotent_and_has_no_model(tmp_path: Path) -> None:
    events, view = _store(tmp_path / "events.sqlite3")
    context = _context()
    _service(events, view).start(context)
    finalizer = GroundedSessionFinalizer(
        events,
        Clock(),
        view,
        NoContent(),
        GROUNDED_ANSWER_SKILL.state_write_policy,
    )
    run = _insufficient_run()

    first = _finalize(finalizer, run, "retry-1")
    retry = _finalize(finalizer, run, "retry-1")

    assert retry == first
    assert first.answer.provenance.model is None
    assert first.answer.provenance.prompt.composition_fingerprint is None
    assert len(events.read(COURSE)) == 5
    assert view.get_context(COURSE, SESSION) is not None
    context_before_suspend = view.get_context(COURSE, SESSION)
    lifecycle = _service(events, view)
    lifecycle.suspend(_context(correlation="suspend-after-answer"))
    lifecycle.resume(_context(correlation="resume-after-answer"))
    assert lifecycle.get_context(_context()) == context_before_suspend
    assert events.verify_projection(COURSE)


def test_same_retry_identity_with_changed_question_conflicts(tmp_path: Path) -> None:
    events, view = _store(tmp_path / "events.sqlite3")
    context = _context()
    _service(events, view).start(context)
    finalizer = GroundedSessionFinalizer(
        events,
        Clock(),
        view,
        NoContent(),
        GROUNDED_ANSWER_SKILL.state_write_policy,
    )
    _finalize(finalizer, _insufficient_run(), "retry-1")
    with pytest.raises(IdempotencyConflictError):
        _finalize(
            finalizer,
            _insufficient_run(question="A changed question?"),
            "retry-1",
        )
    assert len(events.read(COURSE)) == 5


def test_note_after_answer_regenerates_summary_atomically(tmp_path: Path) -> None:
    events, view = _store(tmp_path / "events.sqlite3")
    context = _context()
    lifecycle = _service(events, view)
    lifecycle.start(context)
    finalizer = GroundedSessionFinalizer(
        events, Clock(), view, NoContent(), GROUNDED_ANSWER_SKILL.state_write_policy
    )
    _finalize(finalizer, _insufficient_run(), "retry-note")

    note = lifecycle.record_note(
        _context(correlation="note-after-answer"), "Review this uncertainty."
    )
    summary = lifecycle.get_context(context)

    assert note.content == "Review this uncertainty."
    assert summary is not None
    assert summary.interaction_count == 3
    assert summary.through_interaction_id == note.id
    assert "Review this uncertainty." in summary.unresolved_notes
    assert events.verify_projection(COURSE)


def test_concurrent_write_after_sequence_snapshot_is_retryable(tmp_path: Path) -> None:
    events, view = _store(tmp_path / "events.sqlite3")
    context = _context()
    lifecycle = _service(events, view)
    lifecycle.start(context)
    run = _insufficient_run()
    finalizer = GroundedSessionFinalizer(
        events, Clock(), view, NoContent(), GROUNDED_ANSWER_SKILL.state_write_policy
    )
    racing_engine = SideEffectRecoveryEngine(
        run,
        lambda: lifecycle.record_note(
            _context(correlation="concurrent-note"), "Concurrent note."
        ),
    )

    with pytest.raises(RetryableSessionConflictError, match="stream advanced"):
        finalizer.finalize_grounded_run(
            context=context,
            engine=cast(PlaybookEngine, racing_engine),
            run_id=run.run_id,
            definition=GROUNDED_ANSWER_FLOW,
            inputs=run.inputs,
            pins=run.pins,
            read_dependencies=run.read_dependencies,
            idempotency_key="retry-race",
        )

    assert view.answers(COURSE, SESSION) == ()
    assert tuple(item.content for item in view.interactions(COURSE, SESSION)) == (
        "Concurrent note.",
    )
    assert events.verify_projection(COURSE)


def test_skill_policy_has_exact_finalizer_allowlist() -> None:
    assert GROUNDED_ANSWER_SKILL.state_write_policy.allowed_event_types == (
        "session.interaction_recorded",
        "session.answer_recorded",
        "session.continuation_summary_updated",
    )


def test_finalizer_rejects_non_exact_state_write_policy_before_append(
    tmp_path: Path,
) -> None:
    events, view = _store(tmp_path / "events.sqlite3")
    context = _context()
    _service(events, view).start(context)
    finalizer = GroundedSessionFinalizer(
        events,
        Clock(),
        view,
        NoContent(),
        StateWritePolicy(("session.answer_recorded",)),
    )
    with pytest.raises(StateWritePolicyError, match="exactly allow"):
        _finalize(finalizer, _insufficient_run(), "retry-policy")
    assert len(events.read(COURSE)) == 2


def test_reducer_failure_rolls_back_the_entire_exchange_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events, view = _store(tmp_path / "events.sqlite3")
    context = _context()
    _service(events, view).start(context)
    run = _insufficient_run()
    bad_summary = ContinuationSummaryV1(
        question_interaction_id_for(COURSE, SESSION, run.run_id, "retry-rollback"),
        2,
        (),
        (),
        (),
        0,
    )
    monkeypatch.setattr(
        "study_agent.sessions.service.build_continuation_summary",
        lambda interactions, answers: bad_summary,
    )
    finalizer = GroundedSessionFinalizer(
        events,
        Clock(),
        view,
        NoContent(),
        GROUNDED_ANSWER_SKILL.state_write_policy,
    )

    with pytest.raises(RetryableSessionConflictError, match="history changed"):
        _finalize(finalizer, run, "retry-rollback")

    assert len(events.read(COURSE)) == 2
    assert view.interactions(COURSE, SESSION) == ()
    assert view.answers(COURSE, SESSION) == ()
    assert events.verify_projection(COURSE)
