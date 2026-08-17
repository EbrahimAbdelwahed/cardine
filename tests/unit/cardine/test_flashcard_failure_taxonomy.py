from __future__ import annotations

from cardine.application.flashcard_proposals import (
    _failure_reason,
    _worker_failure_reason,
)
from study_agent.flashcards.lesson_worker_service import LessonWorkerConflictError


def test_worker_failure_taxonomy_preserves_provider_schema_failure() -> None:
    assert _worker_failure_reason(("gateway_schema_incompatible",)) == "schema_incompatible"


def test_worker_failure_taxonomy_separates_validation_from_execution() -> None:
    assert _worker_failure_reason(("child_proof_invalid",)) == "capability_validation_failed"
    assert _worker_failure_reason(("gateway_failed",)) == "capability_execution_failed"


def test_local_scope_conflict_is_not_mislabeled_as_provider_unavailable() -> None:
    assert _failure_reason(LessonWorkerConflictError("changed")) == "scope_stale"
    assert _failure_reason(ValueError("planner bound")) == "capability_execution_failed"
