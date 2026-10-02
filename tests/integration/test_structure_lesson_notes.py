"""PageIndex navigation selects exact canonical lessons without widening evidence."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from cardine.cli import LocalRepository, initialize_local_repository
from cardine.demo.ui_application import RepositoryUiApplication, UiRequestError
from cardine.knowledge import PageIndexStatus
from cardine.materials.product import MaterialProduct
from study_agent.domain import ContentOrigin, PrincipalKind, SourceId
from study_agent.domain._validation import JsonObject
from study_agent.domain.provenance import DocumentConversionProvenance, DocumentPageSpan
from study_agent.ingestion import TextIngestionResult
from tests.integration.test_material_generation_repository_composition import (
    COURSE,
    SESSION,
    _config,
    _prepare,
    _service_context,
)
from tests.integration.test_material_product import _registry

CONTENT = (
    "# Lezioni\nIntroduzione generale.\n"
    "## Prima lezione\nLecture 12 is important; this may be uncertain.\n"
    "### Dettaglio\nUn dettaglio della prima lezione.\n"
    "## Seconda lezione\nCONTENUTO ESTRANEO: non deve entrare nella prima.\n"
)


def prepare_structure(repo: LocalRepository, *, pdf: bool = False) -> TextIngestionResult:
    _prepare(repo, consent=True)
    original = b"%PDF-single-page-fixture" if pdf else None
    conversion = (
        DocumentConversionProvenance(
            sha256(cast(bytes, original)).hexdigest(),
            sha256(CONTENT.encode()).hexdigest(),
            "fixture-pdf@1",
            "1",
            sha256(b"manifest").hexdigest(),
            "fixture-normalizer@1",
            ("No images",),
            page_count=1,
            page_spans=(DocumentPageSpan(1, 0, len(CONTENT)),),
        )
        if pdf
        else None
    )
    result = repo.for_course(COURSE).ingestion.ingest(
        filename="lessons.md",
        content=CONTENT.encode(),
        original_content=original,
        source_id=SourceId("structured-lessons"),
        title="Lezioni",
        trust_level=0,
        source_role="lesson",
        context=_service_context(),
        content_origin=ContentOrigin.EXTRACTED if pdf else ContentOrigin.ORIGINAL,
        conversion_provenance=conversion,
    )
    repo.reconcile_pageindex(COURSE, budget=8)
    return result


def first_lesson(product: MaterialProduct, result: TextIngestionResult) -> dict[str, object]:
    prepared = product.lessons(str(result.source.source_id), str(result.source.revision_id))
    assert prepared["structure_status"] == "ready"
    lessons = cast(tuple[JsonObject, ...], prepared["structure"])
    assert all(
        set(item) == {"title", "start_offset", "end_offset", "content_sha256"} for item in lessons
    )
    return dict(next(item for item in lessons if item["title"] == "Prima lezione"))


@pytest.mark.parametrize("pdf", [False, True])
def test_structure_generation_preserves_exact_scope_and_retry(tmp_path: Path, pdf: bool) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repo:
        result = prepare_structure(repo, pdf=pdf)
        product = MaterialProduct(repo, _service_context())
        lesson = first_lesson(product, result)
        jobs = product.start_structure_lesson(
            str(result.source.source_id), str(result.source.revision_id), lesson, "chosen"
        )
        extracted = product.source(str(jobs[0]["source_id"]), str(jobs[0]["revision_id"]))
        assert extracted.text == CONTENT[CONTENT.index("## Prima") : CONTENT.index("## Seconda")]
        assert "Dettaglio" in extracted.text and "ESTRANEO" not in extracted.text
        assert all(chunk.end_offset <= len(extracted.text) for chunk in extracted.chunks)
        assert extracted.source.extraction_provenance is not None
        product.advance(str(jobs[0]["job_id"]))
        assert product.status(str(jobs[0]["job_id"]))["stage"] == "proposed"
        before = tuple(repo.events.read(COURSE))
        retry = product.start_structure_lesson(
            str(result.source.source_id), str(result.source.revision_id), lesson, "chosen"
        )
        assert retry[0]["job_id"] == jobs[0]["job_id"]
        assert tuple(repo.events.read(COURSE)) == before
        # A retry cannot redirect the original request to another section.
        other = {
            **lesson,
            "title": "Seconda lezione",
            "start_offset": CONTENT.index("## Seconda"),
            "end_offset": len(CONTENT),
        }
        with pytest.raises(ValueError, match="riutilizzata"):
            product.start_structure_lesson(
                str(result.source.source_id), str(result.source.revision_id), other, "chosen"
            )
        assert tuple(repo.events.read(COURSE)) == before
        repo.source_lifetime_service.retire(
            replace(_service_context(), session_id=None, principal_kind=PrincipalKind.HUMAN),
            result.source.source_id,
            "retire-parent",
            expected_sequence=repo.events.projection(COURSE).sequence,
        )
        with pytest.raises(ValueError):
            repo.material_transcript_pin(
                COURSE, SESSION, extracted.source.source_id, extracted.source.revision_id
            )
    # Derived navigation is available after restart without invoking a model or extractor.
    with LocalRepository.open(root) as repo, pytest.raises(ValueError, match="rimossa"):
        MaterialProduct(repo, _service_context()).lessons(
            str(result.source.source_id), str(result.source.revision_id)
        )


@pytest.mark.parametrize(
    "change",
    [
        {"start_offset": True},
        {"end_offset": len(CONTENT) + 1},
        {"content_sha256": "0" * 64},
        {"title": "Titolo inventato"},
        {"node_id": "0001"},
        {"start_offset": 0},
    ],
)
def test_modified_structure_selection_has_no_side_effects(
    tmp_path: Path, change: dict[str, object]
) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repo:
        result = prepare_structure(repo)
        product = MaterialProduct(repo, _service_context())
        lesson = first_lesson(product, result)
        before = tuple(repo.events.read(COURSE))
        with pytest.raises(ValueError):
            product.start_structure_lesson(
                str(result.source.source_id),
                str(result.source.revision_id),
                {**lesson, **change},
                "invalid",
            )
        assert tuple(repo.events.read(COURSE)) == before
        assert not product.jobs()


@pytest.mark.parametrize("status", list(PageIndexStatus))
def test_unavailable_structure_is_explicit_and_cannot_generate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: PageIndexStatus
) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repo:
        result = prepare_structure(repo)
        product = MaterialProduct(repo, _service_context())
        lesson = first_lesson(product, result)
        projection = next(
            item
            for item in repo.pageindex_status(COURSE)
            if item.source_id == str(result.source.source_id)
        )
        unavailable = replace(projection, status=status, candidates=(), document_index=None)
        monkeypatch.setattr(repo, "pageindex_status", lambda _: (unavailable,))
        prepared = product.lessons(str(result.source.source_id), str(result.source.revision_id))
        assert prepared["structure_status"] == status.value
        assert prepared["structure"] == ()
        before = tuple(repo.events.read(COURSE))
        with pytest.raises(ValueError, match="non è più disponibile"):
            product.start_structure_lesson(
                str(result.source.source_id), str(result.source.revision_id), lesson, "invalid"
            )
        assert tuple(repo.events.read(COURSE)) == before


def test_structure_api_dispatch_and_reject_mixed_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repo:
        result = prepare_structure(repo)
        lesson = first_lesson(MaterialProduct(repo, _service_context()), result)
    app = RepositoryUiApplication(
        root, COURSE, SESSION, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    )
    dispatched: list[str] = []
    monkeypatch.setattr(
        app, "_start_material_worker", lambda job, course, session: dispatched.append(job)
    )
    command = {
        "schema_version": 1,
        "request_id": "api-structure",
        "expected_sequence": 0,
        "payload": {
            "source_id": str(result.source.source_id),
            "revision_id": str(result.source.revision_id),
            "structure_lesson": lesson,
        },
    }
    response = app.post("/api/v1/material-generations", command)
    assert len(dispatched) == len(cast(tuple[JsonObject, ...], response["items"])) == 1
    invalid_modes: tuple[dict[str, object], ...] = (
        {"selected_lessons": []},
        {"lessons": []},
        {"structure_lesson": None},
    )
    for extra in invalid_modes:
        invalid = {**command, "payload": {**cast(dict[str, object], command["payload"]), **extra}}
        with pytest.raises(UiRequestError):
            app.post("/api/v1/material-generations", invalid)
    assert len(dispatched) == 1


def test_structure_survives_restart_and_requires_consent(tmp_path: Path) -> None:
    from cardine.integrations.study_agent.course_policy import ProviderConsentRequiredError

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repo:
        result = prepare_structure(repo)
        lesson = first_lesson(MaterialProduct(repo, _service_context()), result)
    with LocalRepository.open(root) as repo:
        product = MaterialProduct(repo, _service_context())
        assert first_lesson(product, result) == lesson
        repo.provider_consent_service.revoke(
            replace(_service_context(), principal_kind=PrincipalKind.HUMAN, session_id=None),
            "revoke-structure",
        )
        before = tuple(repo.events.read(COURSE))
        with pytest.raises(ProviderConsentRequiredError):
            product.start_structure_lesson(
                str(result.source.source_id), str(result.source.revision_id), lesson, "no-consent"
            )
        assert tuple(repo.events.read(COURSE)) == before
        assert not product.jobs()


def test_absent_and_wrong_digest_structure_cannot_admit_a_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repo:
        result = prepare_structure(repo)
        product = MaterialProduct(repo, _service_context())
        lesson = first_lesson(product, result)
        projection = next(
            item
            for item in repo.pageindex_status(COURSE)
            if item.source_id == str(result.source.source_id)
        )
        before = tuple(repo.events.read(COURSE))
        monkeypatch.setattr(repo, "pageindex_status", lambda _: ())
        prepared = product.lessons(str(result.source.source_id), str(result.source.revision_id))
        assert prepared["structure_status"] == "absent" and prepared["structure"] == ()
        with pytest.raises(ValueError):
            product.start_structure_lesson(
                str(result.source.source_id), str(result.source.revision_id), lesson, "absent"
            )
        corrupt = replace(projection, content_sha256="0" * 64, candidates=(), document_index=None)
        monkeypatch.setattr(repo, "pageindex_status", lambda _: (corrupt,))
        with pytest.raises(ValueError, match="non corrisponde"):
            product.lessons(str(result.source.source_id), str(result.source.revision_id))
        assert tuple(repo.events.read(COURSE)) == before
