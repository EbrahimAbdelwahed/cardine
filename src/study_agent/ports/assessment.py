"""Read-only port for canonical assessment state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from study_agent.domain import CourseId, ExecutionContext, RunId

if TYPE_CHECKING:
    from study_agent.artifacts.content import AssessmentItemContent
    from study_agent.assessments.contracts import AssessmentSnapshot, CanonicalResponse
    from study_agent.assessments.grading import DeterministicGradeDecision
    from study_agent.assessments.verified_grading import VerifiedGradeOutcome


class AssessmentViewPort(Protocol):
    def get(self, course_id: CourseId) -> AssessmentSnapshot: ...


class DeterministicClosedGradingPolicyPort(Protocol):
    def grade(
        self, content: AssessmentItemContent, response: CanonicalResponse
    ) -> DeterministicGradeDecision: ...


class VerifiedGradeOwnerStore(Protocol):
    """Atomic owner slot for one completed grading child run."""

    def create(self, run_id: RunId, payload: bytes) -> bool: ...

    def load(self, run_id: RunId) -> bytes: ...


class VerifiedGradePort(Protocol):
    """Recover only a completed, proof-bound provider-neutral grade."""

    def recover(
        self, run_id: RunId, context: ExecutionContext
    ) -> VerifiedGradeOutcome: ...



__all__ = [
    "AssessmentViewPort",
    "DeterministicClosedGradingPolicyPort",
    "VerifiedGradeOwnerStore",
    "VerifiedGradePort",
]
