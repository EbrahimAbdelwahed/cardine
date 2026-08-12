from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

import pytest

from cardine.cli.repository import LocalRepository
from cardine.demo.ui_application import RepositoryUiApplication, UiRequestError
from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.artifacts import (
    AnswerBlock,
    HumanAuthoredArtifactProvenance,
    HybridFlashcardContent,
    StudyArtifactEnvelope,
)
from study_agent.domain import (
    Actor,
    ArtifactReadDependency,
    ArtifactRevisionId,
    CorrelationId,
    CourseId,
    CourseProfile,
    DomainEvent,
    EventId,
    ExecutionContext,
    HybridFlashcardRole,
    InteractionId,
    InteractionKind,
    PrincipalKind,
    RetrievalForm,
    SessionId,
    SourceCommitment,
    SourceId,
    StudyArtifactKind,
    StudyStatementInput,
    StudyStatementKind,
)
from study_agent.ingestion import decode_source_revision_ingested
from study_agent.repository_config import EMPTY_CONFIG
from study_agent.sessions import (
    SESSION_INTERACTION_RECORDED,
    SESSION_SCHEMA_VERSION,
    interaction_recorded_payload,
)

COURSE = CourseId("cardine-c2-course")
SESSION = "cardine-c2-session"
ORIGIN = InteractionId("cardine-c2-origin")


def _content(answer: str) -> StudyArtifactEnvelope:
    return StudyArtifactEnvelope(
        kind=StudyArtifactKind.FLASHCARD,
        content=HybridFlashcardContent(
            RetrievalForm.DIRECT_RECALL,
            "How many cusps does the aortic valve have?",
            (AnswerBlock("Answer", answer),),
            HybridFlashcardRole.DETAIL,
            "A fragile count benefits from direct recall.",
            (0,),
        ),
    )


def _context(
    key: str | None = None,
    *,
    actor: PrincipalKind = PrincipalKind.SERVICE,
) -> ExecutionContext:
    return ExecutionContext(
        actor,
        "c2-test",
        COURSE,
        CorrelationId(f"c2-{key or 'setup'}"),
        session_id=SessionId(SESSION),
        idempotency_key=key,
    )


def _repository(root: Path) -> tuple[Path, object]:
    initialize_local_repository(root, EMPTY_CONFIG)
    with LocalRepository.open(root) as repository:
        repository.course_service.create(
            CourseProfile(COURSE, "Cardine C2", "en", learning_goals=("Study",)),
            ExecutionContext(
                PrincipalKind.SERVICE,
                "c2-course",
                COURSE,
                CorrelationId("c2-course-create"),
            ),
        )
        repository.for_course(COURSE).ingestion.ingest(
            filename="notes.md",
            content=b"Canonical C2 source.",
            source_id=SourceId("c2-source"),
            title="C2 notes",
            trust_level=90,
            source_role="primary",
            context=_context("ingest"),
        )
        repository.session_service.start(_context("session-start"))
        sequence = repository.events.read(COURSE)[-1].course_sequence
        repository.events.append(
            COURSE,
            sequence,
            (
                DomainEvent(
                    EventId("c2-origin-event"),
                    COURSE,
                    sequence + 1,
                    SESSION_INTERACTION_RECORDED,
                    SESSION_SCHEMA_VERSION,
                    Actor(PrincipalKind.HUMAN, "learner"),
                    repository.clock.now(),
                    CorrelationId("c2-origin"),
                    interaction_recorded_payload(
                        ORIGIN, InteractionKind.HUMAN, "C2 study request"
                    ),
                    _context("origin").session_id,
                ),
            ),
        )
        source = decode_source_revision_ingested(repository.events.read(COURSE)[1].payload)
        chunk = source.chunks[0]
        commitment = SourceCommitment(
            chunk.source_id,
            chunk.revision_id,
            chunk.chunk_id,
            chunk.start_offset,
            chunk.end_offset,
        )
        artifact = repository.artifact_service.record_human_revision(
            _content("bounded answer"),
            HumanAuthoredArtifactProvenance(
                PrincipalKind.HUMAN,
                ORIGIN,
                (commitment,),
                (
                    ArtifactReadDependency(
                        "source_revision",
                        str(chunk.source_id),
                        str(chunk.revision_id),
                    ),
                ),
                None,
            ),
            None,
            _context("artifact-proposal", actor=PrincipalKind.HUMAN),
            repository.events.read(COURSE)[-1].course_sequence,
        )
        revision_id = artifact.revisions[0].id
    return root, revision_id


def test_repository_c2_materials_and_artifact_retry_are_bounded_and_session_scoped(
    tmp_path: Path,
) -> None:
    root, revision_id = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)

    materials = app.get("/api/v1/materials")
    material = cast(tuple[dict[str, object], ...], materials["items"])[0]
    assert material["revision_id"]
    assert material["kind"] == "markdown"
    assert material["checksum_sha256"]
    assert material["chunk_count"] == 1
    assert "filename" not in material

    artifacts = app.get("/api/v1/artifacts")
    row = cast(tuple[dict[str, object], ...], artifacts["items"])[0]
    assert row["revision_id"] == str(revision_id)
    assert row["status"] == "proposed"
    assert "content" not in row
    assert "answer" not in row
    assert "raw_output" not in row

    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    command = {
        "schema_version": 1,
        "request_id": "c2-artifact-decision",
        "expected_sequence": sequence,
        "payload": {"decision": "accepted"},
    }
    committed = app.post(f"/api/v1/artifacts/{revision_id}/decisions", command)
    retry = app.post(f"/api/v1/artifacts/{revision_id}/decisions", command)
    assert retry == committed
    assert cast(int, retry["high_water_sequence"]) == cast(
        int, committed["high_water_sequence"]
    )
    result = cast(dict[str, object], retry["result"])
    rows = cast(tuple[dict[str, object], ...], result["items"])
    assert rows[0]["status"] == "accepted"

    restarted = RepositoryUiApplication(root, COURSE, SESSION)
    reloaded_rows = cast(
        tuple[dict[str, object], ...], restarted.get("/api/v1/artifacts")["items"]
    )
    assert reloaded_rows[0]["status"] == "accepted"

    stale = {
        **command,
        "request_id": "c2-artifact-stale",
        "expected_sequence": sequence,
    }
    with pytest.raises(UiRequestError) as stale_error:
        restarted.post(f"/api/v1/artifacts/{revision_id}/decisions", stale)
    assert stale_error.value.status_code == 409
    assert restarted.get("/api/v1/bootstrap")["high_water_sequence"] == committed[
        "high_water_sequence"
    ]


def test_repository_c2_revision_retry_recovers_superseded_predecessor(
    tmp_path: Path,
) -> None:
    root, revision_id = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)
    v1_sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    app.post(
        f"/api/v1/artifacts/{revision_id}/decisions",
        {
            "schema_version": 1,
            "request_id": "c2-v1-accept",
            "expected_sequence": v1_sequence,
            "payload": {"decision": "accepted"},
        },
    )

    with LocalRepository.open(root) as repository:
        predecessor = repository.artifacts.get(COURSE).revision(
            cast(ArtifactRevisionId, revision_id)
        )
        commitment = predecessor.provenance.source_commitments
        sequence = repository.events.read(COURSE)[-1].course_sequence
        v2 = repository.artifact_service.record_human_revision(
            _content("bounded revised answer"),
            HumanAuthoredArtifactProvenance(
                PrincipalKind.HUMAN,
                ORIGIN,
                commitment,
                tuple(
                    ArtifactReadDependency(
                        "source_revision",
                        str(item.source_id),
                        str(item.revision_id),
                    )
                    for item in commitment
                ),
                predecessor.id,
            ),
            predecessor.artifact_id,
            _context("artifact-revision", actor=PrincipalKind.HUMAN),
            sequence,
        )
        v2_id = v2.revisions[-1].id

    proposal_sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    command = {
        "schema_version": 1,
        "request_id": "c2-v2-accept",
        "expected_sequence": proposal_sequence,
        "payload": {"decision": "accepted"},
    }
    accepted = app.post(f"/api/v1/artifacts/{v2_id}/decisions", command)
    restarted = RepositoryUiApplication(root, COURSE, SESSION)
    retry = restarted.post(f"/api/v1/artifacts/{v2_id}/decisions", command)
    assert retry == accepted

    stale = {
        **command,
        "request_id": "c2-v2-stale",
    }
    with pytest.raises(UiRequestError) as stale_error:
        restarted.post(f"/api/v1/artifacts/{v2_id}/decisions", stale)
    assert stale_error.value.status_code == 409
    assert restarted.get("/api/v1/bootstrap")["high_water_sequence"] == accepted[
        "high_water_sequence"
    ]


def test_repository_c2_context_uses_statement_ids_and_reloads_resolution(
    tmp_path: Path,
) -> None:
    root, _revision_id = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)
    with LocalRepository.open(root) as repository:
        origin_context = _context("context-origin", actor=PrincipalKind.HUMAN)
        first = repository.study_context_service.record(
            StudyStatementInput(StudyStatementKind.DEADLINE, date(2026, 9, 1)),
            ORIGIN,
            origin_context,
            repository.events.read(COURSE)[-1].course_sequence,
        )
        second = repository.study_context_service.record(
            StudyStatementInput(StudyStatementKind.DEADLINE, date(2026, 9, 2)),
            ORIGIN,
            _context("context-second", actor=PrincipalKind.HUMAN),
            first.sequence,
        )
        selected = str(second.conflicts[0].statement_ids[0])

    conflicts = app.get("/api/v1/context/conflicts")
    conflict_rows = cast(tuple[dict[str, object], ...], conflicts["items"])
    candidates = cast(tuple[dict[str, object], ...], conflict_rows[0]["candidates"])
    candidate = candidates[0]
    assert candidate["statement_id"] == selected
    assert candidate["provenance"]

    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    command = {
        "schema_version": 1,
        "request_id": "c2-context-resolution",
        "expected_sequence": sequence,
        "payload": {"selected_statement_id": selected},
    }
    committed = app.post("/api/v1/context/conflicts/deadline/resolve", command)
    assert cast(dict[str, object], committed["result"])["status"] == "empty"
    assert app.get("/api/v1/context/conflicts")["status"] == "empty"

    # A display value is not a valid resolution identity.
    bad = {
        **command,
        "request_id": "c2-context-display-value",
        "expected_sequence": committed["high_water_sequence"],
        "payload": {"selected_statement_id": "2026-09-01"},
    }
    with pytest.raises(UiRequestError) as bad_error:
        app.post("/api/v1/context/conflicts/deadline/resolve", bad)
    assert bad_error.value.status_code == 409
