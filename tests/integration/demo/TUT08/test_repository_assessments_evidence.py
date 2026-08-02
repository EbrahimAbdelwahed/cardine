from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.artifacts import (
    AssessmentItemContent,
    HumanAuthoredArtifactProvenance,
    StudyArtifactEnvelope,
)
from study_agent.cli.repository import LocalRepository
from study_agent.demo.ui_application import (
    RepositoryUiApplication,
    UiRequestError,
)
from study_agent.domain import (
    Actor,
    ArtifactDecision,
    ArtifactReadDependency,
    ArtifactRevisionId,
    AssessmentFormat,
    CorrelationId,
    CourseId,
    CourseProfile,
    DomainEvent,
    EventId,
    ExecutionContext,
    InteractionId,
    InteractionKind,
    PrincipalKind,
    SessionId,
    SourceCommitment,
    SourceId,
    StudyArtifactKind,
)
from study_agent.ingestion import decode_source_revision_ingested
from study_agent.repository_config import EMPTY_CONFIG
from study_agent.sessions import (
    SESSION_INTERACTION_RECORDED,
    SESSION_SCHEMA_VERSION,
    interaction_recorded_payload,
)

COURSE = CourseId("cardine-assessment-course")
SESSION = SessionId("cardine-assessment-session")
OTHER_SESSION = SessionId("cardine-other-session")


def _context(
    key: str,
    *,
    session_id: SessionId = SESSION,
    principal: PrincipalKind = PrincipalKind.HUMAN,
) -> ExecutionContext:
    return ExecutionContext(
        principal,
        "cardine-assessment-fixture",
        COURSE,
        CorrelationId(f"assessment-{key}"),
        frozenset({"study:assessment"}),
        session_id,
        idempotency_key=key,
    )


def _record_interaction(
    repository: LocalRepository,
    session_id: SessionId,
    interaction_id: InteractionId,
    key: str,
) -> None:
    sequence = repository.events.read(COURSE)[-1].course_sequence
    repository.events.append(
        COURSE,
        sequence,
        (
            DomainEvent(
                EventId(f"assessment-{key}-event"),
                COURSE,
                sequence + 1,
                SESSION_INTERACTION_RECORDED,
                SESSION_SCHEMA_VERSION,
                Actor(PrincipalKind.HUMAN, "learner"),
                repository.clock.now(),
                CorrelationId(f"assessment-{key}"),
                interaction_recorded_payload(
                    interaction_id,
                    InteractionKind.HUMAN,
                    f"Assessment fixture interaction {key}",
                ),
                session_id,
            ),
        ),
    )


def _repository(root: Path) -> tuple[Path, tuple[ArtifactRevisionId, ...]]:
    initialize_local_repository(root, EMPTY_CONFIG)
    with LocalRepository.open(root) as repository:
        repository.course_service.create(
            CourseProfile(
                COURSE,
                "Cardine Assessments",
                "en",
                learning_goals=("Assess",),
            ),
            ExecutionContext(
                PrincipalKind.SERVICE,
                "assessment-course",
                COURSE,
                CorrelationId("assessment-course"),
            ),
        )
        repository.for_course(COURSE).ingestion.ingest(
            filename="assessment.md",
            content=b"Canonical assessment source.",
            source_id=SourceId("assessment-source"),
            title="Assessment notes",
            trust_level=90,
            source_role="primary",
            context=_context("ingest", principal=PrincipalKind.SERVICE),
        )
        repository.session_service.start(_context("session", session_id=SESSION))
        repository.session_service.start(
            _context("other-session", session_id=OTHER_SESSION)
        )
        origin = InteractionId("assessment-origin")
        _record_interaction(repository, SESSION, origin, "origin")
        source = next(
            decode_source_revision_ingested(event.payload)
            for event in repository.events.read(COURSE)
            if event.event_type == "source.revision_ingested"
        )
        chunk = source.chunks[0]
        commitment = SourceCommitment(
            chunk.source_id,
            chunk.revision_id,
            chunk.chunk_id,
            chunk.start_offset,
            chunk.end_offset,
        )
        revisions: list[ArtifactRevisionId] = []
        for ordinal, (assessment_format, expected) in enumerate(
            (
                ("single_choice", "Alpha"),
                ("multiple_choice", '["Alpha","Gamma"]'),
                ("free_response", "Alpha is the canonical response"),
            )
        ):
            options = (
                ()
                if assessment_format == "free_response"
                else ("Alpha", "Beta", "Gamma")
            )
            content = AssessmentItemContent(
                AssessmentFormat(assessment_format),
                f"Assessment question {ordinal}",
                options,
                expected,
                ("correctness",),
            )
            envelope = StudyArtifactEnvelope(StudyArtifactKind.ASSESSMENT_ITEM, content)
            proposal = repository.artifact_service.record_human_revision(
                envelope,
                HumanAuthoredArtifactProvenance(
                    PrincipalKind.HUMAN,
                    origin,
                    (commitment,),
                    (
                        ArtifactReadDependency(
                            "source_revision",
                            str(chunk.source_id),
                            str(chunk.revision_id),
                        ),
                    ),
                ),
                None,
                _context(f"proposal-{ordinal}"),
                repository.events.read(COURSE)[-1].course_sequence,
            )
            revision_id = proposal.pending()[0].id
            repository.artifact_service.record_human_decision(
                revision_id,
                ArtifactDecision.ACCEPT,
                None,
                _context(f"accept-{ordinal}"),
                repository.events.read(COURSE)[-1].course_sequence,
            )
            revisions.append(revision_id)
    return root, tuple(revisions)


def _command(
    request_id: str, expected_sequence: int, payload: dict[str, object]
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": request_id,
        "expected_sequence": expected_sequence,
        "payload": payload,
    }


def _items(payload: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    return cast(tuple[dict[str, object], ...], payload["items"])


def _response_payload(format_name: str, response_value: object) -> dict[str, object]:
    field = {
        "single_choice": "selected_option",
        "multiple_choice": "selected_options",
        "free_response": "text",
    }[format_name]
    return {"kind": format_name, field: response_value}


def _events(root: Path) -> tuple[DomainEvent, ...]:
    with LocalRepository.open(root) as repository:
        return tuple(repository.events.read(COURSE))


def test_assessments_are_explicitly_presented_and_learner_safe(tmp_path: Path) -> None:
    root, revisions = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)

    before = app.get("/api/v1/bootstrap")
    available = app.get("/api/v1/assessments")
    available_rows = _items(available)
    assert available["status"] == "ready"
    assert len(available_rows) == 3
    assert all(row["presentation_status"] == "unpresented" for row in available_rows)
    assert all(row["presentation_id"] is None for row in available_rows)
    assert all(row["revision_id"] for row in available_rows)
    assert all(
        row["format"] in {"single_choice", "multiple_choice", "free_response"}
        for row in available_rows
    )
    assert all(row["can_attempt"] is False for row in available_rows)
    assert all(row["can_grade"] is False for row in available_rows)
    assert app.get("/api/v1/bootstrap")["high_water_sequence"] == before[
        "high_water_sequence"
    ]

    presentation_ids: list[str] = []
    for ordinal, revision_id in enumerate(revisions):
        sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
        receipt = app.post(
            f"/api/v1/assessments/{revision_id}/presentations",
            _command(f"present-{ordinal}", sequence, {}),
        )
        rows = _items(cast(dict[str, object], receipt["result"]))
        row = next(item for item in rows if item["revision_id"] == str(revision_id))
        presentation_ids.append(cast(str, row["presentation_id"]))
        assert row["presentation_status"] == "presented"
        encoded = json.dumps(row, sort_keys=True)
        assert row["format"] in {"single_choice", "multiple_choice", "free_response"}
        assert all(
            marker not in encoded
            for marker in ("expected_response", "evaluation_criteria", "rubric", "answer")
        )

    assert len(set(presentation_ids)) == 3
    assert len(_items(app.get("/api/v1/assessments"))) == 3


@pytest.mark.parametrize(
    ("format_name", "response_value", "expected_score"),
    (
        ("single_choice", "Alpha", (1, 1)),
        ("multiple_choice", ["Alpha", "Gamma"], (1, 1)),
        ("free_response", "Alpha is the canonical response", None),
    ),
)
def test_attempt_then_grade_strict_mapping_and_truthful_free_state(
    tmp_path: Path,
    format_name: str,
    response_value: object,
    expected_score: tuple[int, int] | None,
) -> None:
    root, revisions = _repository(tmp_path / format_name)
    app = RepositoryUiApplication(root, COURSE, SESSION)
    for ordinal, revision_id in enumerate(revisions):
        sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
        app.post(
            f"/api/v1/assessments/{revision_id}/presentations",
            _command(f"p-{ordinal}", sequence, {}),
        )
    row = next(
        item
        for item in _items(app.get("/api/v1/assessments"))
        if item["format"] == format_name
    )
    presentation_id = cast(str, row["presentation_id"])
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    attempt = app.post(
        f"/api/v1/assessments/{presentation_id}/attempts",
        _command(
            f"attempt-{format_name}",
            sequence,
            {"response": _response_payload(format_name, response_value)},
        ),
    )
    attempt_rows = _items(cast(dict[str, object], attempt["result"]))
    attempted = next(item for item in attempt_rows if item["presentation_id"] == presentation_id)
    attempt_id = cast(str, attempted["attempt_id"])
    assert attempt_id and attempt_id != presentation_id
    assert attempted["grade_id"] is None
    assert attempted["can_attempt"] is False
    assert attempted["can_grade"] is (expected_score is not None)
    assert tuple(
        event.event_type
        for event in _events(root)
        if event.event_type.startswith("assessment.")
    )[-1:] == ("assessment.attempt_recorded",)

    grade_sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    graded = app.post(
        f"/api/v1/assessments/{attempt_id}/grade",
        _command(f"grade-{format_name}", grade_sequence, {}),
    )
    if expected_score is None:
        assert graded["status"] == "needs_review"
        assert cast(int, graded["high_water_sequence"]) == grade_sequence
        assert all(event.event_type != "assessment.grade_recorded" for event in _events(root))
    else:
        assert graded["status"] == "committed"
        graded_row = next(
            item
            for item in _items(cast(dict[str, object], graded["result"]))
            if item["presentation_id"] == presentation_id
        )
        score = cast(dict[str, object], graded_row["grade"])["score"]
        assert (score["numerator"], score["denominator"]) == expected_score  # type: ignore[index]
        assert graded_row["can_attempt"] is False
        assert graded_row["can_grade"] is False


def test_assessment_retry_restart_stale_malformed_cross_session_and_public_demo_are_safe(
    tmp_path: Path,
) -> None:
    root, revisions = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)
    other = RepositoryUiApplication(root, COURSE, OTHER_SESSION)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    presented = app.post(
        f"/api/v1/assessments/{revisions[0]}/presentations",
        _command("present-retry", sequence, {}),
    )
    presentation_id = cast(
        str, next(iter(_items(cast(dict[str, object], presented["result"]))))["presentation_id"]
    )
    assert app.post(
        f"/api/v1/assessments/{revisions[0]}/presentations",
        _command("present-retry", sequence, {}),
    ) == presented
    attempt_sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    attempt_command = _command(
        "attempt-retry",
        attempt_sequence,
        {"response": _response_payload("single_choice", "Alpha")},
    )
    first = app.post(f"/api/v1/assessments/{presentation_id}/attempts", attempt_command)
    event_count = len(_events(root))
    restarted = RepositoryUiApplication(root, COURSE, SESSION)
    assert restarted.post(
        f"/api/v1/assessments/{presentation_id}/attempts", attempt_command
    ) == first
    assert len(_events(root)) == event_count
    attempt_id = cast(
        str,
        next(
            item
            for item in _items(cast(dict[str, object], first["result"]))
            if item["presentation_id"] == presentation_id
        )["attempt_id"],
    )
    grade_sequence = cast(int, restarted.get("/api/v1/bootstrap")["high_water_sequence"])
    grade_command = _command("grade-retry", grade_sequence, {})
    grade = restarted.post(f"/api/v1/assessments/{attempt_id}/grade", grade_command)
    count_after_grade = len(_events(root))
    assert RepositoryUiApplication(root, COURSE, SESSION).post(
        f"/api/v1/assessments/{attempt_id}/grade", grade_command
    ) == grade
    assert len(_events(root)) == count_after_grade

    invalid_event_count = len(_events(root))
    with pytest.raises(UiRequestError) as malformed:
        restarted.post(
            f"/api/v1/assessments/{presentation_id}/attempts",
            _command(
                "malformed",
                cast(int, restarted.get("/api/v1/bootstrap")["high_water_sequence"]),
                {"response": {"kind": "single_choice", "selected_option": ["bad"]}},
            ),
        )
    assert malformed.value.status_code == 400
    assert len(_events(root)) == invalid_event_count
    stale_sequence = cast(int, restarted.get("/api/v1/bootstrap")["high_water_sequence"]) - 1
    with pytest.raises(UiRequestError) as stale:
        restarted.post(
            f"/api/v1/assessments/{presentation_id}/attempts",
            _command(
                "stale",
                stale_sequence,
                {"response": _response_payload("single_choice", "Beta")},
            ),
        )
    assert stale.value.status_code == 409
    assert len(_events(root)) == invalid_event_count

    assert other.get("/api/v1/assessments")["status"] == "empty"
    with pytest.raises(UiRequestError) as cross:
        other.post(
            f"/api/v1/assessments/{presentation_id}/attempts",
            _command(
                "cross",
                cast(int, other.get("/api/v1/bootstrap")["high_water_sequence"]),
                {"response": _response_payload("single_choice", "Alpha")},
            ),
        )
    assert cross.value.status_code in {400, 409}
    assert len(_events(root)) == invalid_event_count

def test_free_response_needs_review_state_survives_get_and_restart_without_grade(
    tmp_path: Path,
) -> None:
    root, revisions = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)
    for ordinal, revision_id in enumerate(revisions):
        sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
        app.post(
            f"/api/v1/assessments/{revision_id}/presentations",
            _command(f"free-present-{ordinal}", sequence, {}),
        )
    free = next(
        row for row in _items(app.get("/api/v1/assessments")) if row["format"] == "free_response"
    )
    presentation_id = cast(str, free["presentation_id"])
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    attempt = app.post(
        f"/api/v1/assessments/{presentation_id}/attempts",
        _command(
            "free-attempt-needs-review",
            sequence,
            {"response": _response_payload("free_response", "Alpha is the canonical response")},
        ),
    )
    assert attempt["status"] == "committed"
    attempted_id = cast(
        str,
        next(
            row
            for row in _items(cast(dict[str, object], attempt["result"]))
            if row["presentation_id"] == presentation_id
        )["attempt_id"],
    )
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    grade = app.post(
        f"/api/v1/assessments/{attempted_id}/grade",
        _command("free-grade-needs-review", sequence, {}),
    )
    assert grade["status"] == "needs_review"
    assert grade["grade_id"] is None
    row = next(
        item
        for item in _items(app.get("/api/v1/assessments"))
        if item["presentation_id"] == presentation_id
    )
    assert row["presentation_status"] == "attempted"
    assert row["grading_status"] == "needs_review"
    assert row["can_attempt"] is False
    assert row["can_grade"] is False
    assert row["grade_id"] is None
    assert row["active_grade_id"] is None
    assert row["grade_history"] == ()
    restarted = RepositoryUiApplication(root, COURSE, SESSION)
    reloaded = next(
        item
        for item in _items(restarted.get("/api/v1/assessments"))
        if item["presentation_id"] == presentation_id
    )
    assert reloaded == row
    assert all(event.event_type != "assessment.grade_recorded" for event in _events(root))


def test_evidence_is_canonical_and_preserves_contest_and_supersession_history(
    tmp_path: Path,
) -> None:
    root, revisions = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    app.post(
        f"/api/v1/assessments/{revisions[0]}/presentations",
        _command("present-evidence", sequence, {}),
    )
    row = _items(app.get("/api/v1/assessments"))[0]
    presentation_id = cast(str, row["presentation_id"])
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    attempt = app.post(
        f"/api/v1/assessments/{presentation_id}/attempts",
        _command(
            "attempt-evidence",
            sequence,
            {"response": _response_payload("single_choice", "Alpha")},
        ),
    )
    attempt_id = cast(
        str,
        next(
            item
            for item in _items(cast(dict[str, object], attempt["result"]))
            if item["presentation_id"] == presentation_id
        )["attempt_id"],
    )
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    grade = app.post(
        f"/api/v1/assessments/{attempt_id}/grade",
        _command("grade-evidence", sequence, {}),
    )
    grade_id = cast(str, grade["grade_id"])
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    app.post(
        f"/api/v1/assessments/grades/{grade_id}/contest",
        _command("contest-evidence", sequence, {"reason": "Please review"}),
    )
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    successor = app.post(
        f"/api/v1/assessments/{attempt_id}/grade",
        _command("grade-successor", sequence, {"supersedes_grade_id": grade_id}),
    )
    successor_id = cast(str, successor["grade_id"])
    lifecycle_row = next(
        item
        for item in _items(app.get("/api/v1/assessments"))
        if item["presentation_id"] == presentation_id
    )
    assert lifecycle_row["active_grade_id"] == successor_id
    assert lifecycle_row["can_attempt"] is False
    assert lifecycle_row["can_grade"] is False
    history = cast(tuple[dict[str, object], ...], lifecycle_row["grade_history"])
    assert len(history) == 2
    predecessor_history = next(item for item in history if item["grade_id"] == grade_id)
    successor_history = next(item for item in history if item["grade_id"] == successor_id)
    assert predecessor_history["lifecycle"] == "superseded"
    assert predecessor_history["active"] is False
    assert predecessor_history["contested"] is True
    assert successor_history["lifecycle"] == "active"
    assert successor_history["active"] is True
    assert successor_history["contested"] is False
    contests = cast(tuple[dict[str, object], ...], lifecycle_row["contests"])
    assert contests and contests[0]["grade_id"] == grade_id
    assert contests[0]["disposition"] == "contested"
    hidden = json.dumps(lifecycle_row, sort_keys=True)
    assert all(
        marker not in hidden
        for marker in (
            "evaluation_criteria",
            "criterion_results",
            "rationale",
            "provenance",
            "content_fingerprint",
        )
    )
    evidence = app.get("/api/v1/evidence")
    assert evidence["through_sequence"] == app.get("/api/v1/bootstrap")[
        "high_water_sequence"
    ]
    estimates = cast(tuple[dict[str, object], ...], evidence["estimates"])
    assert estimates
    for estimate in estimates:
        assert isinstance(estimate["numerator"], int)
        assert isinstance(estimate["denominator"], int)
        references = cast(tuple[dict[str, object], ...], estimate["references"])
        assert references
        assert all(
            {"grade_id", "event_sequence", "disposition", "numerator", "denominator"}
            <= set(reference)
            for reference in references
        )
    all_references = [
        reference
        for estimate in estimates
        for reference in cast(tuple[dict[str, object], ...], estimate["references"])
    ]
    assert any(reference["grade_id"] == grade_id for reference in all_references)
    assert any(reference["grade_id"] == successor_id for reference in all_references)
    assert any(
        reference["grade_id"] == grade_id
        and reference["disposition"] in {"contested", "superseded"}
        for reference in references
    )
    evidence_sequence = cast(int, evidence["through_sequence"])
    with LocalRepository.open(root) as repository:
        _record_interaction(
            repository,
            SESSION,
            InteractionId("external-evidence-writer"),
            "external-evidence-writer",
        )
    refreshed_evidence = app.get("/api/v1/evidence")
    assert cast(int, refreshed_evidence["through_sequence"]) > evidence_sequence
    assert refreshed_evidence["through_sequence"] == app.get("/api/v1/bootstrap")[
        "high_water_sequence"
    ]
    assert all(
        estimate["through_sequence"] == refreshed_evidence["through_sequence"]
        for estimate in cast(
            tuple[dict[str, object], ...], refreshed_evidence["estimates"]
        )
    )
    prior_estimates = tuple(
        {**estimate, "through_sequence": None}
        for estimate in cast(tuple[dict[str, object], ...], evidence["estimates"])
    )
    refreshed_estimates = tuple(
        {**estimate, "through_sequence": None}
        for estimate in cast(
            tuple[dict[str, object], ...], refreshed_evidence["estimates"]
        )
    )
    assert refreshed_estimates == prior_estimates
