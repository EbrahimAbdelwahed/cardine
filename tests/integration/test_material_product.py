from __future__ import annotations

import json
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


def test_superseded_source_keeps_human_decision_and_stales_unpublished_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from study_agent.domain import SourceId

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
                str(admitted.source.source_id),
                str(admitted.source.revision_id),
                "stale-before-publish",
            )["job_id"]
        )
        product.advance(job_id)
        view = product.status(job_id)
        complete = next(
            item
            for item in cast(tuple[JsonObject, ...], view["outputs"])
            if item["variant"] == "complete"
        )
        publish = product.publish
        with monkeypatch.context() as defer_publication:
            defer_publication.setattr(product, "publish", lambda _job_id: None)
            accepted = product.decide(
                job_id,
                str(complete["revision_id"]),
                "accept",
                int(cast(int, view["high_water_sequence"])),
                "accept-before-source-change",
            )
        assert next(
            item
            for item in cast(tuple[JsonObject, ...], accepted["outputs"])
            if item["revision_id"] == complete["revision_id"]
        )["status"] == "accepted"
        repository.for_course(context.course_id).ingestion.ingest(
            filename="replacement.md",
            content=b"A newer canonical source revision.",
            source_id=SourceId(str(admitted.source.source_id)),
            title="Replacement",
            trust_level=100,
            source_role="lesson",
            context=context,
        )
        publish(job_id)
        result = product.status(job_id)

        accepted = next(
            item
            for item in cast(tuple[JsonObject, ...], result["outputs"])
            if item["revision_id"] == complete["revision_id"]
        )
        assert result["stage"] == "stale"
        assert accepted["status"] == "accepted"
        assert accepted["publication"] == "approved_blocked"
        assert accepted["published_source_id"] is None
        assert not product._publication_pending(job_id)
        assert all(
            item.source.content_origin is not ContentOrigin.GENERATED
            for item in repository.for_course(context.course_id).content.catalog()
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


def test_pdf_lessons_require_exact_page_coverage_and_parent_stays_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        full = text + text.replace("Lezione 12A", "Lezione 12B") * 2
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
        with pytest.raises(ValueError, match="confini"):
            product.start(
                str(admitted.source.source_id), str(admitted.source.revision_id), "whole-pdf"
            )
        assert len(tuple(repository.events.read(context.course_id))) == before
        assert product.jobs() == []
        with monkeypatch.context() as bounds:
            bounds.setattr("cardine.materials.product.MAX_TRANSCRIPT_CHARACTERS", len(text) + 1)
            with pytest.raises(ValueError, match="troppo lunga"):
                product.start_lessons(
                    str(admitted.source.source_id),
                    str(admitted.source.revision_id),
                    [
                        {"title": "First", "start_page": 1, "end_page": 1},
                        {"title": "Second", "start_page": 2, "end_page": 2},
                    ],
                    "oversized-split",
                )
        assert len(tuple(repository.events.read(context.course_id))) == before
        assert product.jobs() == []
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


def test_audio_resume_reuses_completed_chunks_and_joins_the_same_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        from study_agent.ingestion import IngestionErrorCode, TextIngestionError

        def conflict(**kwargs: object) -> None:
            raise TextIngestionError(IngestionErrorCode.SEQUENCE_CONFLICT, "race", retryable=True)

        with monkeypatch.context() as admission:
            admission.setattr(product, "admit_extraction", conflict)
            product.advance_audio(str(job["job_id"]), fixture)
        assert product.audio_status(str(job["job_id"]))["stage"] == "transcribing"
        product.advance_audio(str(job["job_id"]), fixture)
        view = product.audio_status(str(job["job_id"]))
        assert view["stage"] == "proposed"
        assert len(cast(tuple[JsonObject, ...], view["outputs"])) == 2
        assert fixture.calls == 3


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


def test_job_dispatch_keeps_original_scope_when_workspace_selection_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cardine.demo.ui_application import RepositoryUiApplication
    from study_agent.domain import CourseId, SessionId

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repository:
        admitted = _prepare(repository, consent=True)
    context = _service_context()
    app = RepositoryUiApplication(
        root,
        context.course_id,
        cast(SessionId, context.session_id),
        model_adapters=_registry(),
        environment={"OPENAI_API_KEY": "fixture"},
    )
    dispatched: list[tuple[CourseId, SessionId]] = []
    original_start = MaterialProduct.start

    def change_selection_after_start(
        product: MaterialProduct,
        source_id: str,
        revision_id: str,
        request_id: str,
    ) -> JsonObject:
        view = original_start(product, source_id, revision_id, request_id)
        # Simulate another selection request between admission and dispatch.
        app._course_id = CourseId("another-course")
        app._session_id = SessionId("another-session")
        return view

    def capture(job_id: str, course: CourseId, session: SessionId) -> None:
        dispatched.append((course, session))

    monkeypatch.setattr(MaterialProduct, "start", change_selection_after_start)
    monkeypatch.setattr(app, "_start_material_worker", capture)
    app.post(
        "/api/v1/material-generations",
        {
            "schema_version": 1,
            "request_id": "scope-race",
            "expected_sequence": 0,
            "payload": {
                "source_id": str(admitted.source.source_id),
                "revision_id": str(admitted.source.revision_id),
            },
        },
    )
    assert dispatched == [(context.course_id, context.session_id)]


@pytest.mark.parametrize("conflicts", [1, 4])
def test_publication_conflicts_retry_without_repeating_human_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    conflicts: int,
) -> None:
    from collections.abc import Mapping, Sequence
    from typing import Any

    from study_agent.artifacts.events import DECISION_RECORDED
    from study_agent.domain import CourseId, DomainEvent, SourceId

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
                str(admitted.source.source_id), str(admitted.source.revision_id), "publish-race"
            )["job_id"]
        )
        product.advance(job_id)
        view = product.status(job_id)
        complete = next(
            item
            for item in cast(tuple[JsonObject, ...], view["outputs"])
            if item["variant"] == "complete"
        )
        append = type(repository.events).append
        races = 0

        def concurrent_append(
            store: Any,
            course_id: CourseId,
            expected_sequence: int,
            events: Sequence[DomainEvent],
        ) -> int:
            nonlocal races
            source = events[0].payload.get("source")
            if (
                store is repository.events
                and isinstance(source, Mapping)
                and source.get("content_origin") == "generated"
                and races < conflicts
            ):
                races += 1
                repository.for_course(context.course_id).ingestion.ingest(
                    filename="other.md",
                    content=f"Other source {races}".encode(),
                    source_id=SourceId(f"concurrent-{races}"),
                    title="Another source",
                    trust_level=0,
                    source_role="lesson",
                    context=context,
                )
            return append(store, course_id, expected_sequence, events)

        with monkeypatch.context() as patch:
            patch.setattr(type(repository.events), "append", concurrent_append)
            result = product.decide(
                job_id,
                str(complete["revision_id"]),
                "accept",
                int(cast(int, view["high_water_sequence"])),
                "accept-complete",
            )
        assert races == conflicts
        assert result["stage"] == ("proposed" if conflicts == 1 else "publication_retryable")
        result_outputs = cast(tuple[JsonObject, ...], result["outputs"])
        accepted = next(item for item in result_outputs if item["variant"] == "complete")
        assert accepted["status"] == "accepted"
        assert accepted["publication"] == ("published" if conflicts == 1 else "approved_blocked")
    # The pending publication receipt survives reopening, without provider access.
    with LocalRepository.open(root) as repository:
        product = MaterialProduct(repository, context)
        if conflicts == 4:
            assert product.status(job_id)["stage"] == "publication_retryable"
        product.publish(job_id)
        assert product.status(job_id)["stage"] == "proposed"
        outputs = cast(tuple[JsonObject, ...], product.status(job_id)["outputs"])
        assert (
            next(item for item in outputs if item["variant"] == "complete")["publication"]
            == "published"
        )
        assert next(item for item in outputs if item["variant"] == "study")["status"] == "proposed"
        decisions = [
            event
            for event in repository.events.read(context.course_id)
            if event.event_type == DECISION_RECORDED
        ]
        assert len(decisions) == 1


def test_oversized_audio_stops_before_admission_and_is_not_retried(tmp_path: Path) -> None:
    from collections.abc import Callable

    from cardine.adapters.audio.groq import GroqAudioTranscriber
    from cardine.materials.generation_contracts import MAX_TRANSCRIPT_CHARACTERS

    class OversizedAudio(GroqAudioTranscriber):
        calls = 0

        def __init__(self) -> None:
            super().__init__("fixture")

        def transcribe(
            self,
            data: bytes,
            extension: str,
            recovered: list[JsonObject],
            save: Callable[[list[JsonObject]], None],
            preflight: Callable[[], None],
        ) -> tuple[str, JsonObject]:
            self.calls += 1
            preflight()
            text = "x" * (MAX_TRANSCRIPT_CHARACTERS + 1)
            save([{"text": text, "spans": ()}])
            return text, {"spans": ()}

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    context = replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
    transcriber = OversizedAudio()
    with LocalRepository.open(root) as repository:
        _prepare(repository, consent=True)
        product = MaterialProduct(repository, context)
        sequence = repository.events.projection(context.course_id).sequence
        job_id = str(
            product.start_audio(b"recording", "lecture.wav", "Audio", "oversized")["job_id"]
        )
        product.advance_audio(job_id, transcriber)
        status = product.audio_status(job_id)
        assert status["stage"] == "failed_terminal"
        assert "troppo lunga" in str(status["error"])
        assert not status["outputs"]
        assert repository.events.projection(context.course_id).sequence == sequence
        assert len(repository.for_course(context.course_id).content.catalog()) == 1
    with LocalRepository.open(root) as repository:
        product = MaterialProduct(repository, context)
        product.advance_audio(job_id, transcriber)
        assert transcriber.calls == 1
        assert product.audio_status(job_id)["stage"] == "failed_terminal"


def test_oversized_audio_manifest_is_terminal_and_retains_provenance_and_chunks(
    tmp_path: Path,
) -> None:
    from collections.abc import Callable
    from hashlib import sha256

    from cardine.adapters.audio.groq import GroqAudioTranscriber
    from study_agent.adapters.sqlite.namespaced_run_store import NamespacedSQLiteRunStore
    from study_agent.domain import BlobId, BlobRef

    class LongManifestAudio(GroqAudioTranscriber):
        calls = 0

        def __init__(self) -> None:
            super().__init__("fixture")

        def transcribe(
            self,
            data: bytes,
            extension: str,
            recovered: list[JsonObject],
            save: Callable[[list[JsonObject]], None],
            preflight: Callable[[], None],
        ) -> tuple[str, JsonObject]:
            self.calls += 1
            preflight()
            chunk: JsonObject = {"text": "Lecture 12 is important.", "spans": ()}
            recovered.append(chunk)
            save(recovered)
            return "Lecture 12 is important.", {
                "model": "whisper-large-v3-turbo",
                "duration_seconds": 600,
                "chunk_count": 1,
                "spans": tuple(
                    {"start_ms": index, "end_ms": index + 1}
                    for index in range(100_000)
                ),
            }

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    context = replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
    transcriber = LongManifestAudio()
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        _prepare(repository, consent=True)
        product = MaterialProduct(repository, context)
        job_id = str(
            product.start_audio(b"recording", "lecture.wav", "Audio", "long-manifest")[
                "job_id"
            ]
        )
        product.advance_audio(job_id, transcriber)

        status = product.audio_status(job_id)
        raw_state = NamespacedSQLiteRunStore(repository.runs, "audio-generation").load(job_id)
        stored = json.loads(raw_state)
        manifest_ref = BlobRef(
            BlobId("sha256:" + stored["manifest"]["sha256"]),
            stored["manifest"]["sha256"],
            stored["manifest"]["bytes"],
        )
        manifest_bytes = repository.blobs.get(manifest_ref)
        assert len(manifest_bytes) > 2 * 1024 * 1024
        assert sha256(manifest_bytes).hexdigest() == stored["manifest"]["sha256"]
        assert len(stored["chunks"]) == 1
        assert status["stage"] == "failed_terminal"
        assert status["transcribed_chunks"] == 1
        assert not status["outputs"]

        product.advance_audio(job_id, transcriber)
        assert transcriber.calls == 1
        assert product.audio_status(job_id)["stage"] == "failed_terminal"


def test_publication_retry_is_dispatched_after_accepted_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cardine.demo.ui_application import RepositoryUiApplication
    from cardine.materials.materializer import (
        GeneratedSourceMaterializationConflictError,
        GeneratedSourceMaterializer,
    )
    from study_agent.domain import CourseId, SessionId

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repository:
        admitted = _prepare(repository, consent=True)
    context = replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
    app = RepositoryUiApplication(
        root,
        context.course_id,
        cast(SessionId, context.session_id),
        model_adapters=_registry(),
        environment={"OPENAI_API_KEY": "fixture"},
    )
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        product = MaterialProduct(repository, context)
        job_id = str(
            product.start(
                str(admitted.source.source_id), str(admitted.source.revision_id), "retry-dispatch"
            )["job_id"]
        )
        product.advance(job_id)
        view = product.status(job_id)
        complete = next(
            item
            for item in cast(tuple[JsonObject, ...], view["outputs"])
            if item["variant"] == "complete"
        )
    dispatched: list[tuple[str, CourseId, SessionId]] = []

    def conflict(*args: object, **kwargs: object) -> object:
        raise GeneratedSourceMaterializationConflictError("fixture sequence conflict")

    def capture(job_id: str, course: CourseId, session: SessionId) -> None:
        dispatched.append((job_id, course, session))

    monkeypatch.setattr(GeneratedSourceMaterializer, "materialize", conflict)
    monkeypatch.setattr(app, "_start_material_worker", capture)
    result = app.post(
        f"/api/v1/material-generations/{job_id}/decisions",
        {
            "schema_version": 1,
            "request_id": "accept-complete",
            "expected_sequence": view["high_water_sequence"],
            "payload": {"revision_id": complete["revision_id"], "decision": "accept"},
        },
    )
    assert result["stage"] == "publication_retryable"
    assert dispatched == [(job_id, context.course_id, context.session_id)]


def test_resume_during_worker_completion_is_not_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from threading import Event

    from cardine.demo.ui_application import RepositoryUiApplication
    from study_agent.domain import SessionId

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repository:
        admitted = _prepare(repository, consent=True)
    context = replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
    app = RepositoryUiApplication(
        root,
        context.course_id,
        cast(SessionId, context.session_id),
        model_adapters=_registry(),
        environment={"OPENAI_API_KEY": "fixture"},
    )
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        product = MaterialProduct(repository, context)
        job_id = str(
            product.start(
                str(admitted.source.source_id), str(admitted.source.revision_id), "wake-worker"
            )["job_id"]
        )
    entered, release, rerun, settled = Event(), Event(), Event(), Event()
    original = MaterialProduct.advance
    calls = 0

    def pause_first(product: MaterialProduct, identifier: str) -> None:
        assert product.context.principal_kind is PrincipalKind.SERVICE
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(5)
        else:
            rerun.set()
        original(product, identifier)

    monkeypatch.setattr(MaterialProduct, "advance", pause_first)
    monkeypatch.setattr(
        app, "_start_indexing_worker", lambda: settled.set() if calls == 2 else None
    )
    app._start_material_worker(job_id, context.course_id, cast(SessionId, context.session_id))
    try:
        assert entered.wait(5)
        app._start_material_worker(job_id, context.course_id, cast(SessionId, context.session_id))
    finally:
        release.set()
    assert rerun.wait(5)
    assert settled.wait(5)
    assert calls == 2


@pytest.mark.parametrize("endpoint", ["/api/v1/workspace/select", "/api/v1/workspace/sessions"])
def test_workspace_selection_recovers_jobs_outside_startup_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    from cardine.demo.ui_application import RepositoryUiApplication
    from study_agent.domain import CourseId, SessionId

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    base = _service_context()
    other = replace(base, session_id=SessionId("other-session"), principal_kind=PrincipalKind.HUMAN)
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        admitted = _prepare(repository, consent=True)
        repository.session_service.start(other)
        product = MaterialProduct(repository, other)
        job = product.start(
            str(admitted.source.source_id), str(admitted.source.revision_id), "other-job"
        )
    app = RepositoryUiApplication(
        root,
        base.course_id,
        cast(SessionId, base.session_id),
        model_adapters=_registry(),
        environment={"OPENAI_API_KEY": "fixture"},
    )
    dispatched: list[tuple[str, CourseId, SessionId]] = []
    monkeypatch.setattr(
        app,
        "_start_material_worker",
        lambda job, course, session: dispatched.append((job, course, session)),
    )
    app.post(
        endpoint,
        {
            "schema_version": 1,
            "request_id": "select-other",
            "expected_sequence": 0,
            "payload": {"course_id": str(other.course_id), "session_id": str(other.session_id)},
        },
    )
    assert dispatched == [(str(job["job_id"]), other.course_id, other.session_id)]
