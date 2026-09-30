from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from cardine.cli import (
    LocalRepository,
    ModelAdapterBuilder,
    ModelAdapterConfig,
    ModelAdapterRegistry,
    initialize_local_repository,
)
from cardine.materials.product import MaterialProduct
from study_agent.adapters.model import GPT_5_6_LUNA_ADAPTER_ID
from study_agent.domain import ContentOrigin, PrincipalKind
from study_agent.domain._validation import JsonObject
from study_agent.ports import ModelPort
from tests.integration.test_material_generation_repository_composition import (
    ScriptedLuna,
    _config,
    _prepare,
    _service_context,
)


def _registry() -> ModelAdapterRegistry:
    def build(_config: ModelAdapterConfig, _credential: str | None) -> ModelPort:
        return ScriptedLuna()

    return ModelAdapterRegistry({GPT_5_6_LUNA_ADAPTER_ID: cast(ModelAdapterBuilder, build)})


def test_product_requires_explicit_decisions_and_publishes_parent_first(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    context = replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        admitted = _prepare(repository, consent=True)
        product = MaterialProduct(repository, context)
        job = product.start(
            str(admitted.source.source_id), str(admitted.source.revision_id), "job-1"
        )
        assert (
            product.start(
                str(admitted.source.source_id), str(admitted.source.revision_id), "job-1"
            )["job_id"]
            == job["job_id"]
        )
        job_id = str(job["job_id"])
        product.advance(job_id)
        view = product.status(job_id)
        assert view["stage"] == "proposed"
        outputs = cast(tuple[JsonObject, ...], view["outputs"])
        assert len(outputs) == 2
        assert all(item["publication"] == "pending" for item in outputs)
        study = next(item for item in outputs if item["variant"] == "study")
        complete = next(item for item in outputs if item["variant"] == "complete")
        result = product.decide(
            job_id,
            str(study["revision_id"]),
            "accept",
            int(cast(int, view["high_water_sequence"])),
            "accept-study",
        )
        assert not any(
            item["published_source_id"] for item in cast(tuple[JsonObject, ...], result["outputs"])
        )
        result = product.decide(
            job_id,
            str(complete["revision_id"]),
            "accept",
            int(cast(int, result["high_water_sequence"])),
            "accept-complete",
        )
        assert all(
            item["publication"] == "published"
            for item in cast(tuple[JsonObject, ...], result["outputs"])
        )
        before = len(tuple(repository.events.read(context.course_id)))
        product.publish(job_id)
        assert len(tuple(repository.events.read(context.course_id))) == before
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        product = MaterialProduct(repository, context)
        assert product.jobs()[0]["job_id"] == job_id
        assert all(
            item["publication"] == "published"
            for item in cast(tuple[JsonObject, ...], product.status(job_id)["outputs"])
        )


def test_extracted_audio_keeps_binary_source_and_replays_lineage(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    context = replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        _prepare(repository, consent=True)
        product = MaterialProduct(repository, context)
        original = b"\xff\x00audio-fixture"
        admitted = product.admit_extraction(
            original=original,
            text="# Lesson\n\nLecture 12 is important; this may be uncertain.",
            title="Recorded lecture",
            manifest={"spans": ({"start_ms": 0, "end_ms": 1000},)},
            adapter="fixture-speech@1",
            media_type="audio/wav",
            limitations=("ASR is uncertain.",),
        )
        assert admitted.source.content_origin is ContentOrigin.EXTRACTED
        assert repository.blobs.get(admitted.source.blob) == original
        job = product.start(
            str(admitted.source.source_id), str(admitted.source.revision_id), "audio-notes"
        )
        product.advance(str(job["job_id"]))
        assert product.status(str(job["job_id"]))["stage"] == "proposed"
    with LocalRepository.open(root) as repository:
        record = next(
            item
            for item in repository.for_course(context.course_id).content.catalog()
            if item.source.source_id == admitted.source.source_id
        )
        assert record.source.extraction_provenance is not None
        assert record.text.startswith("# Lesson")


def test_foreign_job_cannot_be_read_or_decided(tmp_path: Path) -> None:
    from study_agent.domain import SessionId

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        admitted = _prepare(repository, consent=True)
        product = MaterialProduct(
            repository, replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
        )
        job = product.start(
            str(admitted.source.source_id), str(admitted.source.revision_id), "job-1"
        )
        foreign = MaterialProduct(
            repository, replace(_service_context(), session_id=SessionId("other-session"))
        )
        with pytest.raises(ValueError, match="non trovata"):
            foreign.status(str(job["job_id"]))


def test_pdf_lessons_require_exact_page_coverage_and_parent_stays_current(tmp_path: Path) -> None:
    from hashlib import sha256

    from study_agent.domain import SourceId
    from study_agent.domain.provenance import DocumentConversionProvenance, DocumentPageSpan

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    context = replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        _prepare(repository, consent=True)
        text = "# Lezione 12A\n\nLecture 12 is important; this may be uncertain.\n"
        full = text + text.replace("Lezione 12A", "Lezione 12B")
        original = b"%PDF-fixture"
        provenance = DocumentConversionProvenance(
            sha256(original).hexdigest(),
            sha256(full.encode()).hexdigest(),
            "fixture-pdf@1",
            "1",
            sha256(b"manifest").hexdigest(),
            "fixture-normalizer@1",
            ("No images",),
            page_count=2,
            page_spans=(
                DocumentPageSpan(1, 0, len(text)),
                DocumentPageSpan(2, len(text), len(full)),
            ),
        )
        admitted = repository.for_course(context.course_id).ingestion.ingest(
            filename="pdf.md",
            content=full.encode(),
            original_content=original,
            source_id=SourceId("pdf-source"),
            title="PDF lessons",
            trust_level=0,
            source_role="lesson",
            content_origin=ContentOrigin.EXTRACTED,
            conversion_provenance=provenance,
            context=context,
        )
        product = MaterialProduct(repository, context)
        prepared = product.lessons(str(admitted.source.source_id), str(admitted.source.revision_id))
        assert len(cast(tuple[JsonObject, ...], prepared["lessons"])) == 2
        before = len(tuple(repository.events.read(context.course_id)))
        with pytest.raises(ValueError, match="coprire"):
            product.start_lessons(
                str(admitted.source.source_id),
                str(admitted.source.revision_id),
                [
                    {"title": "First", "start_page": 1, "end_page": 2},
                    {"title": "Second", "start_page": 2, "end_page": 2},
                ],
                "bad-ranges",
            )
        assert len(tuple(repository.events.read(context.course_id))) == before
        jobs = product.start_lessons(
            str(admitted.source.source_id),
            str(admitted.source.revision_id),
            [
                {"title": "First", "start_page": 1, "end_page": 1},
                {"title": "Second", "start_page": 2, "end_page": 2},
            ],
            "pdf-notes",
        )
        assert len(jobs) == 2
        product.advance(str(jobs[0]["job_id"]))
        assert product.status(str(jobs[0]["job_id"]))["stage"] == "proposed"
        # Replacing the parent PDF invalidates the unfinished extracted lesson.
        repository.for_course(context.course_id).ingestion.ingest(
            filename="new.md",
            content=b"New PDF revision",
            source_id=admitted.source.source_id,
            title="Replaced",
            trust_level=0,
            source_role="lesson",
            context=context,
        )
        product.advance(str(jobs[1]["job_id"]))
        assert product.status(str(jobs[1]["job_id"]))["stage"] == "stale"


def test_audio_resume_reuses_completed_chunks_and_joins_the_same_pipeline(tmp_path: Path) -> None:
    from collections.abc import Callable

    from cardine.adapters.audio.groq import GroqAudioTranscriber

    class FixtureAudio(GroqAudioTranscriber):
        def __init__(self) -> None:
            super().__init__("fixture")
            self.calls = 0

        def transcribe(
            self,
            data: bytes,
            extension: str,
            recovered: list[JsonObject],
            save: Callable[[list[JsonObject]], None],
            preflight: Callable[[], None],
        ) -> tuple[str, JsonObject]:
            preflight()
            self.calls += 1
            if not recovered:
                recovered.append({"text": "First chunk", "spans": ()})
                save(recovered)
                raise ValueError("fixture interrupted")
            assert recovered[0]["text"] == "First chunk"
            return "# Lesson\n\nLecture 12 is important; this may be uncertain.", {"spans": ()}

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    context = replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
    fixture = FixtureAudio()
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        _prepare(repository, consent=True)
        product = MaterialProduct(repository, context)
        job = product.start_audio(b"audio-original", "lecture.mp3", "Audio", "audio-upload")
        with pytest.raises(ValueError, match="interrupted"):
            product.advance_audio(str(job["job_id"]), fixture)
        assert product.audio_status(str(job["job_id"]))["stage"] == "retryable"
        assert product.audio_status(str(job["job_id"]))["transcribed_chunks"] == 1
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        product = MaterialProduct(repository, context)
        product.advance_audio(str(job["job_id"]), fixture)
        view = product.audio_status(str(job["job_id"]))
        assert view["stage"] == "proposed"
        assert len(cast(tuple[JsonObject, ...], view["outputs"])) == 2
        assert fixture.calls == 2


def test_missing_runtime_configuration_is_resumable_not_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cardine.cli.repository import ModelAdapterConfigurationError

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    context = replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        admitted = _prepare(repository, consent=True)
        product = MaterialProduct(repository, context)
        job_id = str(
            product.start(
                str(admitted.source.source_id), str(admitted.source.revision_id), "config-retry"
            )["job_id"]
        )
        before = product.status(job_id)["stage"]

        def unavailable(*args: object, **kwargs: object) -> object:
            raise ModelAdapterConfigurationError("credential unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(LocalRepository, "material_generation", unavailable)
            with pytest.raises(ModelAdapterConfigurationError):
                product.advance(job_id)
        assert product.status(job_id)["stage"] == before
        product.worker_error(job_id)
        assert product.status(job_id)["stage"] == "retryable"
        product.clear_worker_error(job_id)
        product.advance(job_id)
        assert product.status(job_id)["stage"] == "proposed"
