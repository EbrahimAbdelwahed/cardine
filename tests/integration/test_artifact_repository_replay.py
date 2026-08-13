from __future__ import annotations

from pathlib import Path
from typing import cast

from cardine.cli.registry import public_study_tool_entries, registration_for
from cardine.cli.repository import LocalRepository, initialize_local_repository
from study_agent.adapters.filesystem import FilesystemExportWriter
from study_agent.application import ExportBundleV2, ExportService, ExportVersion
from study_agent.artifacts import ArtifactProposalOrigin, artifact_batch_id_for
from study_agent.domain import (
    ArtifactDecision,
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    InteractionId,
    PrincipalKind,
    RunId,
    SessionId,
    SourceCommitment,
    SourceId,
)
from study_agent.ingestion import decode_source_revision_ingested
from study_agent.repository_config import EMPTY_CONFIG
from study_agent.tools.builtin import public_study_tool_manifests
from tests.contract.export.test_artifact_export_v2 import (
    _content,
    _decision_event,
    _generated_provenance,
    _proposal_event,
    _recorded,
)

COURSE = CourseId("course-export")


def test_artifact_repository_opens_replays_and_exports_without_canonical_rewrite(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    session = SessionId("session-export")

    def context(*, session_scoped: bool = False) -> ExecutionContext:
        return ExecutionContext(
            PrincipalKind.SERVICE,
            "repository-test",
            COURSE,
            CorrelationId("repository-test"),
            session_id=session if session_scoped else None,
        )

    with LocalRepository.open(root) as repository:
        repository.course_service.create(
            CourseProfile(COURSE, "Artifact replay", "en", learning_goals=("Replay",)),
            context(),
        )
        repository.for_course(COURSE).ingestion.ingest(
            filename="notes.md",
            content=b"The aortic valve has three cusps.",
            source_id=SourceId("source-aortic"),
            title="Aortic valve",
            trust_level=90,
            source_role="primary",
            context=context(),
        )
        repository.session_service.start(context(session_scoped=True))
        source = decode_source_revision_ingested(repository.events.read(COURSE)[1].payload)
        chunk = source.chunks[0]
        commitment = SourceCommitment(
            chunk.source_id,
            chunk.revision_id,
            chunk.chunk_id,
            chunk.start_offset,
            min(chunk.start_offset + 8, chunk.end_offset),
        )
        run_id = RunId("run-repository-artifact")
        content = _content("Three cusps")
        proposal = _recorded(
            artifact_batch_id_for(COURSE, session, run_id, "repository-proposal"),
            0,
            content,
            _generated_provenance(content, commitment, run_id),
        )
        repository.events.append(
            COURSE,
            3,
            (
                _proposal_event(
                    4,
                    "repository-proposal",
                    proposal,
                    run_id=run_id,
                    origin=ArtifactProposalOrigin.GENERATED,
                    interaction_id=InteractionId("unused-generated-interaction"),
                ),
                _decision_event(
                    5,
                    "repository-accept",
                    proposal.revision_id,
                    ArtifactDecision.ACCEPT,
                ),
            ),
        )
        before = tuple(repository.events.read(COURSE))

    with LocalRepository.open(root) as repository:
        assert tuple(repository.events.read(COURSE)) == before
        assert repository.events.verify_projection(COURSE)
        bundle = ExportService(repository.events).assemble(
            COURSE, version=ExportVersion.V2
        )
        assert isinstance(bundle, ExportBundleV2)
        assert len(bundle.artifacts) == 1
        FilesystemExportWriter().write(bundle, tmp_path / "export-v2")
        assert tuple(repository.events.read(COURSE)) == before


def test_public_study_tool_surface_remains_the_same_seven_identified_tools() -> None:
    manifests = public_study_tool_manifests()
    assert tuple(item.name for item in manifests) == (
        "citation.resolve",
        "course.get",
        "grounding.ask",
        "session.get_context",
        "session.record_note",
        "source.list",
        "source.search",
    )
    assert len(manifests) == 7
    entries = public_study_tool_entries()
    assert tuple(
        str(cast(dict[str, object], item["manifest"])["name"]) for item in entries
    ) == tuple(item.name for item in manifests)


def test_export_registration_advertises_explicit_v3_without_changing_default() -> None:
    registration = registration_for("export")
    descriptor = registration.to_json()
    arguments = cast(tuple[dict[str, object], ...], descriptor["arguments"])
    version = next(item for item in arguments if item["name"] == "version")

    assert version == {
        "name": "version",
        "kind": "option",
        "value_type": "string",
        "required": False,
        "repeated": False,
        "default_json": "1",
        "secret": False,
    }
    assert descriptor["verification"] == (
        "cardine --json --repository REPOSITORY export COURSE_ID "
        "--output PATH --version 3"
    )
