"""Projection-only public session query contract."""

from __future__ import annotations

from typing import Protocol

from study_agent.domain.identifiers import AnswerId, CourseId, SessionId
from study_agent.domain.session import (
    AnswerRecord,
    AssistantTurnRecord,
    ContinuationSummaryV1,
    InteractionRecord,
    StudySessionRecord,
    TutorPresentationRecord,
)


class SessionNotFoundError(LookupError):
    def __init__(self, course_id: CourseId, session_id: SessionId) -> None:
        self.course_id = course_id
        self.session_id = session_id
        super().__init__(f"session {session_id} was not found in course {course_id}")


class AnswerNotFoundError(LookupError):
    def __init__(self, session_id: SessionId, answer_id: AnswerId) -> None:
        self.session_id = session_id
        self.answer_id = answer_id
        super().__init__(f"answer {answer_id} was not found in session {session_id}")


class SessionViewPort(Protocol):
    def list_sessions(self, course_id: CourseId) -> tuple[StudySessionRecord, ...]: ...

    def get_session(self, course_id: CourseId, session_id: SessionId) -> StudySessionRecord: ...

    def interactions(
        self, course_id: CourseId, session_id: SessionId
    ) -> tuple[InteractionRecord, ...]: ...

    def answers(
        self, course_id: CourseId, session_id: SessionId
    ) -> tuple[AnswerRecord, ...]: ...

    def get_answer(
        self, course_id: CourseId, session_id: SessionId, answer_id: AnswerId
    ) -> AnswerRecord: ...

    def get_context(
        self, course_id: CourseId, session_id: SessionId
    ) -> ContinuationSummaryV1 | None: ...


class AssistantTurnViewPort(Protocol):
    def turns(
        self, course_id: CourseId, session_id: SessionId
    ) -> tuple[AssistantTurnRecord, ...]: ...


class TutorPresentationViewPort(Protocol):
    def presentations(
        self, course_id: CourseId, session_id: SessionId
    ) -> tuple[TutorPresentationRecord, ...]: ...
