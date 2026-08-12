from __future__ import annotations

import importlib.metadata
from dataclasses import replace
from pathlib import Path

import pytest

from study_agent.adapters.filesystem import FilesystemExportWriter
from study_agent.adapters.scheduling import PyFsrsSchedulingPolicy
from study_agent.application import ExportBundleV3, ExportService, ExportVersion
from study_agent.artifacts import ArtifactProposalOrigin, artifact_batch_id_for
from cardine.cli.repository import LocalRepository, initialize_local_repository
from study_agent.domain import (
    Actor,
    ArtifactDecision,
    CorrelationId,
    CourseId,
    CourseProfile,
    DomainEvent,
    EventId,
    ExecutionContext,
    InteractionId,
    InteractionKind,
    PrincipalKind,
    RunId,
    SessionId,
    SourceCommitment,
    SourceId,
)
from study_agent.ingestion import decode_source_revision_ingested
from study_agent.recall import RecallRating
from study_agent.repository_config import EMPTY_CONFIG
from study_agent.sessions.events import interaction_recorded_payload
from tests.contract.export.test_artifact_export_v2 import (
    _content,
    _decision_event,
    _generated_provenance,
    _proposal_event,
    _recorded,
)

COURSE = CourseId("course-export")
SESSION = SessionId("session-export")
def _fsrs_available() -> bool:
    try:
        return importlib.metadata.version("fsrs") == "6.3.1"
    except importlib.metadata.PackageNotFoundError:
        return False


@pytest.mark.skipif(not _fsrs_available(), reason="optional fsrs==6.3.1 extra is not installed")
def test_real_fsrs_recall_lifecycle_replays_and_exports_without_scheduler_on_reopen(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    scheduler = PyFsrsSchedulingPolicy()
    with LocalRepository.open(root, recall_scheduler=scheduler) as repository:
        context = ExecutionContext(
            PrincipalKind.SERVICE,
            "e2e",
            COURSE,
            CorrelationId("e2e"),
        )
        repository.course_service.create(
            CourseProfile(COURSE, "Recall E2E", "en", learning_goals=("Replay",)),
            context,
        )
        repository.for_course(COURSE).ingestion.ingest(
            filename="notes.md",
            content=b"The aortic valve has three cusps.",
            source_id=SourceId("source-aortic"),
            title="Aortic valve",
            trust_level=90,
            source_role="primary",
            context=context,
        )
        session_context = replace(context, session_id=SESSION)
        repository.session_service.start(session_context)
        stream = tuple(repository.events.read(COURSE))
        source = decode_source_revision_ingested(stream[1].payload)
        chunk = source.chunks[0]
        commitment = SourceCommitment(
            chunk.source_id,
            chunk.revision_id,
            chunk.chunk_id,
            chunk.start_offset,
            min(chunk.start_offset + 8, chunk.end_offset),
        )
        interaction = DomainEvent(
            EventId("event-human-recall"),
            COURSE,
            len(stream) + 1,
            "session.interaction_recorded",
            1,
            Actor(PrincipalKind.HUMAN, "e2e"),
            stream[-1].occurred_at,
            CorrelationId("recall-interaction"),
            interaction_recorded_payload(
                InteractionId("interaction-recall"),
                InteractionKind.HUMAN,
                "accept card",
            ),
            SESSION,
        )
        repository.events.append(COURSE, len(stream), (interaction,))
        content = _content("Three cusps")
        old = _recorded(
            artifact_batch_id_for(COURSE, SESSION, RunId("run-recall-old"), "old"),
            0,
            content,
            _generated_provenance(content, commitment, RunId("run-recall-old")),
        )
        stream = tuple(repository.events.read(COURSE))
        repository.events.append(
            COURSE,
            len(stream),
            (
                _proposal_event(
                    len(stream) + 1,
                    "old",
                    old,
                    run_id=RunId("run-recall-old"),
                    origin=ArtifactProposalOrigin.GENERATED,
                    interaction_id=InteractionId("unused"),
                ),
                _decision_event(
                    len(stream) + 2,
                    "old-accept",
                    old.revision_id,
                    ArtifactDecision.ACCEPT,
                ),
            ),
        )
        assert repository.recall is not None and repository.recall.service is not None
        service = repository.recall.service
        stream = tuple(repository.events.read(COURSE))
        enrolled = service.enroll(
            old.revision_id,
            replace(session_context, idempotency_key="enroll"),
            len(stream),
        )
        stream = tuple(repository.events.read(COURSE))
        reviewed = service.review(
            old.revision_id,
            RecallRating.GOOD,
            replace(
                session_context,
                principal_kind=PrincipalKind.HUMAN,
                idempotency_key="review",
            ),
            len(stream),
            latency_ms=500,
            confidence_bps=9000,
        )
        assert enrolled.enrollments and reviewed.reviews
        due_at = reviewed.schedules[-1].due_at
        assert repository.recall_composition.due.due(COURSE, now=due_at)
        v3 = ExportService(repository.events).assemble(COURSE, version=ExportVersion.V3)
        assert isinstance(v3, ExportBundleV3)
        assert v3.recall
        assert all(
            set(row)
            <= {
                "schema_version",
                "receipt_type",
                "course_sequence",
                "session_id",
                "review_id",
                "revision_id",
                "rating",
                "latency_ms",
                "confidence_bps",
                "occurred_at",
                "decision_id",
                "trigger",
                "enrollment_at",
                "due_at",
                "policy_id",
                "policy_version",
                "policy_fingerprint",
                "implementation_id",
                "implementation_version",
                "history_fingerprint",
                "result_fingerprint",
            }
            for row in v3.recall
        )
        FilesystemExportWriter().write(v3, tmp_path / "export-v3")
        projection_before = repository.events.projection(COURSE).canonical_bytes()
        repository.events.rebuild_projection(COURSE)
        assert repository.events.projection(COURSE).canonical_bytes() == projection_before

        successor_content = _content("Three cusps, reconstructed")
        stream = tuple(repository.events.read(COURSE))
        successor = _recorded(
            artifact_batch_id_for(COURSE, SESSION, RunId("run-recall-new"), "new"),
            0,
            successor_content,
            _generated_provenance(
                successor_content,
                commitment,
                RunId("run-recall-new"),
                prior=old.revision_id,
            ),
            artifact_id=old.artifact_id,
            prior=old.revision_id,
        )
        repository.events.append(
            COURSE,
            len(stream),
            (
                _proposal_event(
                    len(stream) + 1,
                    "new",
                    successor,
                    run_id=RunId("run-recall-new"),
                    origin=ArtifactProposalOrigin.GENERATED,
                    interaction_id=InteractionId("unused-new"),
                ),
                _decision_event(
                    len(stream) + 2,
                    "new-accept",
                    successor.revision_id,
                    ArtifactDecision.ACCEPT,
                    supersedes=old.revision_id,
                ),
            ),
        )
        assert repository.recall_composition.due.due(COURSE, now=due_at) == ()
        expected_recall = repository.recall_composition.view.get(COURSE)

    with LocalRepository.open(root) as reopened:
        assert reopened.recall is None
        assert reopened.recall_composition.view.get(COURSE) == expected_recall
        assert reopened.recall_composition.due.due(COURSE, now=due_at) == ()
