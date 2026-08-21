from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest

from cardine.cli import (
    LocalRepository,
    LocalRepositoryConfig,
    ModelAdapterBuilder,
    ModelAdapterConfig,
    ModelAdapterRegistry,
    initialize_local_repository,
)
from cardine.integrations.study_agent.course_policy import ProviderConsentRequiredError
from cardine.materials import MaterialGenerationStage
from cardine.materials.planning import UnitManifest
from study_agent.adapters.model import GPT_5_6_LUNA_ADAPTER_ID
from study_agent.artifacts import LessonMaterialContent
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    RunId,
    SessionId,
    SourceId,
)
from study_agent.domain._validation import JsonObject
from study_agent.ingestion import TextIngestionResult
from study_agent.ports import (
    CancellationToken,
    ModelCapabilities,
    ModelFinishReason,
    ModelInvocation,
    ModelPort,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelUsage,
)
from tests.course_fixtures import create_canonical_course

COURSE = CourseId("course-material-repository")
SESSION = SessionId("session-material-repository")
SOURCE = SourceId("source-material-repository")


class ScriptedLuna:
    capabilities = ModelCapabilities(structured_output=True)

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        invocation = ModelInvocation(
            GPT_5_6_LUNA_ADAPTER_ID,
            "1.0.0",
            "gpt-5.6-luna",
            f"material-response-{self.calls}",
        )
        if request.structured_output is not None and request.structured_output.name == (
            "material_boundaries_v1"
        ):
            manifest = UnitManifest.from_bytes(
                request.messages[1].content.split("unit_manifest=", 1)[1].encode()
            )
            structured: JsonObject = {
                "schema_version": 1,
                "manifest_fingerprint": manifest.fingerprint,
                "segments": (
                    {"start_unit": 0, "end_unit": len(manifest.units), "title": "Lesson"},
                ),
            }
        else:
            structured = {
                "markdown": "# Lesson\n\nLecture 12 is important; this may be uncertain.",
                "limitations": ("Transcript uncertainty is preserved.",),
            }
        return ModelResponse(
            "",
            ModelUsage(12, 6),
            ModelFinishReason.STOP,
            invocation,
            structured_output=structured,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise AssertionError(request)
        yield  # pragma: no cover

    async def cancel(self, token: CancellationToken) -> None:
        raise AssertionError(token)


def _config() -> LocalRepositoryConfig:
    return LocalRepositoryConfig(
        ModelAdapterConfig(
            GPT_5_6_LUNA_ADAPTER_ID,
            {"timeout_seconds": 10},
            "OPENAI_API_KEY",
        )
    )


def _service_context() -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "material-repository-service",
        COURSE,
        CorrelationId("material-repository-run"),
        session_id=SESSION,
    )


def _prepare(repository: LocalRepository, *, consent: bool) -> TextIngestionResult:
    create_canonical_course(repository.events, COURSE)
    admitted = repository.for_course(COURSE).ingestion.ingest(
        filename="lecture.md",
        content=b"# Lesson\n\nLecture 12 is important; this may be uncertain.\n",
        source_id=SOURCE,
        title="Lesson",
        trust_level=100,
        source_role="lesson",
        context=ExecutionContext(
            PrincipalKind.SERVICE,
            "material-repository-ingestion",
            COURSE,
            CorrelationId("material-repository-ingestion"),
        ),
    )
    repository.session_service.start(
        ExecutionContext(
            PrincipalKind.HUMAN,
            "learner",
            COURSE,
            CorrelationId("material-repository-session"),
            session_id=SESSION,
        )
    )
    if consent:
        repository.provider_consent_service.grant(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "learner",
                COURSE,
                CorrelationId("material-repository-consent"),
            ),
            "material-repository-consent",
        )
    return admitted


def test_repository_rejects_missing_consent_before_constructing_luna(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, _config())
    builds: list[int] = []

    def build(_config: ModelAdapterConfig, _credential: str | None) -> ModelPort:
        builds.append(1)
        return ScriptedLuna()

    registry = ModelAdapterRegistry(
        {GPT_5_6_LUNA_ADAPTER_ID: cast(ModelAdapterBuilder, build)}
    )
    with LocalRepository.open(
        root, model_adapters=registry, environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        admitted = _prepare(repository, consent=False)
        pin = repository.material_transcript_pin(
            COURSE, SESSION, SOURCE, admitted.source.revision_id
        )
        with pytest.raises(ProviderConsentRequiredError):
            repository.material_generation(pin, _service_context())
    assert builds == []


def test_repository_generation_commits_one_atomic_pair_and_recovers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, _config())
    models: list[ScriptedLuna] = []

    def build(_config: ModelAdapterConfig, _credential: str | None) -> ModelPort:
        model = ScriptedLuna()
        models.append(model)
        return model

    registry = ModelAdapterRegistry(
        {GPT_5_6_LUNA_ADAPTER_ID: cast(ModelAdapterBuilder, build)}
    )
    environment = {"OPENAI_API_KEY": "fixture"}
    with LocalRepository.open(
        root, model_adapters=registry, environment=environment
    ) as repository:
        admitted = _prepare(repository, consent=True)
        pin = repository.material_transcript_pin(
            COURSE, SESSION, SOURCE, admitted.source.revision_id
        )
        service = repository.material_generation(pin, _service_context())
        requested = service.request_pair(COURSE, SESSION, pin, "material-request-1")
        proposed = asyncio.run(
            service.reconcile(requested.job_id, bounded_budget=8, context=_service_context())
        )
        assert proposed.stage is MaterialGenerationStage.PROPOSED
        snapshot = repository.artifacts.get(COURSE)
        matching = tuple(
            batch for batch in snapshot.batches if batch.run_id == RunId(requested.job_id)
        )
        assert len(matching) == 1
        assert len(matching[0].revision_ids) == 2
        materials = tuple(
            revision.content.content
            for revision in snapshot.revisions
            if revision.id in matching[0].revision_ids
        )
        assert all(isinstance(material, LessonMaterialContent) for material in materials)
        by_variant = {
            material.variant.value: material
            for material in materials
            if isinstance(material, LessonMaterialContent)
        }
        assert set(by_variant) == {"complete", "study"}
        complete = by_variant["complete"]
        study = by_variant["study"]
        assert repository.blobs.get(complete.markdown_blob).decode().startswith("# Lesson")
        assert repository.blobs.get(study.markdown_blob).decode().startswith("# Lesson")
        assert study.direct_parent_blob_sha256 == complete.markdown_blob.checksum_sha256
        assert models[0].calls == 4
        proposal_events = tuple(
            event
            for event in repository.events.read(COURSE)
            if event.event_type == "study_artifact.proposal_batch_recorded"
        )
        assert len(proposal_events) == 1

    with LocalRepository.open(
        root, model_adapters=registry, environment=environment
    ) as reopened:
        pin = reopened.material_transcript_pin(
            COURSE, SESSION, SOURCE, admitted.source.revision_id
        )
        service = reopened.material_generation(pin, _service_context())
        same = service.request_pair(COURSE, SESSION, pin, "material-request-1")
        recovered = asyncio.run(
            service.reconcile(same.job_id, bounded_budget=8, context=_service_context())
        )
        assert recovered.stage is MaterialGenerationStage.PROPOSED
        assert models[-1].calls == 0
        assert len(
            tuple(
                event
                for event in reopened.events.read(COURSE)
                if event.event_type == "study_artifact.proposal_batch_recorded"
            )
        ) == 1
