from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from cardine.demo.product_shell import (
    DueReview,
    ProductShell,
    ProductShellStatus,
    ProductShellView,
    render,
)
from cardine.hosts import TutorHostRunResult, TutorHostRunStatus
from study_agent.capabilities import (
    EXPLAIN_CONCEPT_MANIFEST,
    CapabilityContinuation,
    CapabilityManifest,
    CapabilityOutcome,
    TutorCapabilityId,
)
from study_agent.domain import (
    CourseId,
    ExecutionContext,
    SessionId,
    SessionStatus,
    StudyStatementKind,
    TutorContextField,
    TutorContextState,
    TutorSnapshotV1,
)
from study_agent.domain._validation import JsonObject, JsonValue

COURSE = CourseId("course")
SESSION = SessionId("session")


def _snapshot() -> TutorSnapshotV1:
    fields = tuple(
        TutorContextField(
            kind,
            TutorContextState.MISSING,
            (),
        )
        for kind in StudyStatementKind
    )
    return TutorSnapshotV1(
        COURSE,
        SESSION,
        1,
        SessionStatus.ACTIVE,
        None,
        (),
        fields,
        (),
        (),
        (),
        (),
    )


class _Snapshots:
    def __init__(self, snapshot: TutorSnapshotV1) -> None:
        self.snapshot = snapshot

    def get(self, course_id: CourseId, session_id: SessionId) -> TutorSnapshotV1:
        assert (course_id, session_id) == (COURSE, SESSION)
        return self.snapshot


class _Gateway:
    def discover(self) -> tuple[CapabilityManifest, ...]:
        return (EXPLAIN_CONCEPT_MANIFEST,)

    async def start(
        self,
        capability_id: TutorCapabilityId,
        inputs: JsonObject,
        context: ExecutionContext,
    ) -> CapabilityOutcome:
        raise AssertionError("presentation test must not start capabilities")

    async def resume(
        self,
        continuation: CapabilityContinuation,
        response: JsonValue,
        context: ExecutionContext,
    ) -> CapabilityOutcome:
        raise AssertionError("presentation test must not resume capabilities")


class _Due:
    def due(self, course_id: CourseId) -> tuple[DueReview, ...]:
        assert course_id == COURSE
        return (DueReview("review-1", "Aortic valve", datetime(2026, 7, 21, tzinfo=UTC)),)


class _Host:
    async def respond(
        self,
        course_id: CourseId,
        session_id: SessionId,
        learner_entry: str,
        *,
        pending_fingerprint: str | None = None,
    ) -> TutorHostRunResult:
        assert (course_id, session_id) == (COURSE, SESSION)
        assert learner_entry
        assert pending_fingerprint is None
        return TutorHostRunResult(
            TutorHostRunStatus.COMPLETED,
            completed_output={
                "status": "answered",
                "tutor_message": "The aortic valve opens from the left ventricle.",
            },
        )


def test_free_form_entry_starts_before_context_is_complete() -> None:
    shell = ProductShell(_Snapshots(_snapshot()), _Gateway())

    view = shell.begin(COURSE, SESSION, "Explain the valves in ten minutes")

    assert view.status is ProductShellStatus.WORKING
    assert view.learner_entry == "Explain the valves in ten minutes"
    assert view.snapshot is not None
    assert view.context[0].state is TutorContextState.MISSING


def test_optional_due_review_is_exposed_without_changing_snapshot() -> None:
    shell = ProductShell(_Snapshots(_snapshot()), _Gateway(), _Due())

    view = shell.view(COURSE, SESSION)

    assert view.status is ProductShellStatus.NEEDS_REVIEW
    assert tuple(item.label for item in view.due_reviews) == ("Aortic valve",)
    assert view.evidence_through_sequence == 1


def test_provider_free_completion_refreshes_the_public_snapshot() -> None:
    shell = ProductShell(_Snapshots(_snapshot()), _Gateway())

    recovered = asyncio.run(shell.submit(COURSE, SESSION, "What differs?", _Host()))

    assert recovered.status is ProductShellStatus.READY
    assert recovered.assistant_message == "The aortic valve opens from the left ventricle."
    assert recovered.divergences == ()


def test_stale_refresh_is_recovered_by_the_next_completed_host_turn() -> None:
    shell = ProductShell(_Snapshots(_snapshot()), _Gateway())

    assert shell.mark_stale(COURSE, SESSION).status is ProductShellStatus.STALE
    recovered = asyncio.run(shell.submit(COURSE, SESSION, "Refresh the answer", _Host()))

    assert recovered.status is ProductShellStatus.RECOVERED


def test_render_has_no_ansi_or_provider_details() -> None:
    view = ProductShellView(ProductShellStatus.WORKING, "hello", None, None, None)

    output = render(view)

    assert "STUDY AGENT PRODUCT SHELL" in output
    assert "hello" in output
    assert "OPENAI_API_KEY" not in output
    assert "\x1b" not in output


def test_entry_is_bounded_and_non_empty() -> None:
    shell = ProductShell(_Snapshots(_snapshot()), _Gateway())

    with pytest.raises(ValueError, match="non-empty"):
        shell.begin(COURSE, SESSION, "   ")
    with pytest.raises(ValueError, match="text bound"):
        shell.begin(COURSE, SESSION, "x" * 4_001)
