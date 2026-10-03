"""Selective PDF generation preserves canonical provenance and retry identity."""

import json
from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from cardine.cli import LocalRepository, initialize_local_repository
from cardine.demo.ui_application import RepositoryUiApplication
from cardine.materials.product import MaterialProduct
from study_agent.domain import BlobId, BlobRef, ContentOrigin, PrincipalKind, SourceId
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


def prepare_pdf(repository: LocalRepository) -> TextIngestionResult:
    _prepare(repository, consent=True)
    pages = [
        f"# Lezione 12{letter}\n\nLecture 12 is important; this may be uncertain.\n"
        for letter in "ABCD"
    ]
    full = "".join(pages)
    original = b"%PDF-fixture"
    offset = 0
    spans = []
    for index, page in enumerate(pages, 1):
        spans.append(DocumentPageSpan(index, offset, offset + len(page)))
        offset += len(page)
    provenance = DocumentConversionProvenance(
        sha256(original).hexdigest(),
        sha256(full.encode()).hexdigest(),
        "fixture-pdf@1",
        "1",
        sha256(b"manifest").hexdigest(),
        "fixture-normalizer@1",
        ("No images",),
        page_count=4,
        page_spans=tuple(spans),
    )
    return repository.for_course(COURSE).ingestion.ingest(
        filename="pdf.md",
        content=full.encode(),
        original_content=original,
        source_id=SourceId("selected-pdf"),
        title="PDF lessons",
        trust_level=0,
        source_role="lesson",
        content_origin=ContentOrigin.EXTRACTED,
        conversion_provenance=provenance,
        context=replace(_service_context(), principal_kind=PrincipalKind.HUMAN),
    )


def test_only_selected_lessons_generate_and_retries_preserve_jobs(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repo:
        pdf = prepare_pdf(repo)
        product = MaterialProduct(
            repo, replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
        )
        selected: list[dict[str, object]] = [
            {"title": "Lezione 12B", "start_page": 2, "end_page": 2},
            {"title": "Lezione 12D", "start_page": 4, "end_page": 4},
        ]
        jobs = product.start_selected_lessons(
            str(pdf.source.source_id), str(pdf.source.revision_id), selected, "selected"
        )
        before = tuple(repo.events.read(COURSE))
        retry = product.start_selected_lessons(
            str(pdf.source.source_id),
            str(pdf.source.revision_id),
            list(reversed(selected)),
            "selected",
        )
        assert [job["job_id"] for job in retry] == [job["job_id"] for job in jobs]
        assert tuple(repo.events.read(COURSE)) == before
        assert len(product.jobs()) == 2
        for job in jobs:
            record = product.source(str(job["source_id"]), str(job["revision_id"]))
            assert record.text.startswith(f"# {job['title']}\n")
            extraction = record.source.extraction_provenance
            assert extraction is not None
            manifest = json.loads(
                repo.blobs.get(
                    BlobRef(
                        BlobId("sha256:" + extraction.manifest_sha256),
                        extraction.manifest_sha256,
                        extraction.manifest_byte_length,
                    )
                )
            )
            assert manifest["parent_source_id"] == str(pdf.source.source_id)
            assert manifest["parent_revision_id"] == str(pdf.source.revision_id)
            assert manifest["start_page"] in (2, 4)
            product.advance(str(job["job_id"]))
            view = product.status(str(job["job_id"]))
            assert view["stage"] == "proposed"
            assert all(
                output["publication"] == "pending"
                for output in cast(tuple[JsonObject, ...], view["outputs"])
            )
        with pytest.raises(ValueError, match="riutilizzata"):
            product.start_selected_lessons(
                str(pdf.source.source_id), str(pdf.source.revision_id), selected[:1], "selected"
            )


@pytest.mark.parametrize(
    "selection",
    [
        [],
        [{"title": "Invalid", "start_page": 0, "end_page": 1}],
        [{"title": "Invalid", "start_page": 2, "end_page": 5}],
        [{"title": "Invalid", "start_page": True, "end_page": 2}],
        [{"title": "Invalid", "start_page": 3, "end_page": 2}],
        [
            {"title": "A", "start_page": 2, "end_page": 3},
            {"title": "B", "start_page": 3, "end_page": 4},
        ],
        [
            {"title": "A", "start_page": 2, "end_page": 2},
            {"title": "A", "start_page": 2, "end_page": 2},
        ],
    ],
)
def test_invalid_selection_has_no_canonical_side_effects(
    tmp_path: Path, selection: list[dict[str, object]]
) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repo:
        pdf = prepare_pdf(repo)
        product = MaterialProduct(repo, _service_context())
        before = tuple(repo.events.read(COURSE))
        with pytest.raises(ValueError):
            product.start_selected_lessons(
                str(pdf.source.source_id), str(pdf.source.revision_id), selection, "invalid"
            )
        assert tuple(repo.events.read(COURSE)) == before
        assert not product.jobs()


def test_selected_lessons_api_dispatches_only_selected_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repo:
        pdf = prepare_pdf(repo)
    app = RepositoryUiApplication(
        root, COURSE, SESSION, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    )
    dispatched: list[str] = []
    monkeypatch.setattr(
        app, "_start_material_worker", lambda job, course, session: dispatched.append(job)
    )
    response = app.post(
        "/api/v1/material-generations",
        {
            "schema_version": 1,
            "request_id": "api-selection",
            "expected_sequence": 0,
            "payload": {
                "source_id": str(pdf.source.source_id),
                "revision_id": str(pdf.source.revision_id),
                "selected_lessons": [{"title": "Lezione 3", "start_page": 3, "end_page": 3}],
            },
        },
    )
    assert len(dispatched) == 1
    assert len(cast(tuple[JsonObject, ...], response["items"])) == 1


@pytest.mark.parametrize("started", [False, True])
def test_selected_lessons_reject_retired_parent(tmp_path: Path, started: bool) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repo:
        pdf = prepare_pdf(repo)
        context = replace(_service_context(), principal_kind=PrincipalKind.HUMAN)
        product = MaterialProduct(repo, context)
        selected: list[dict[str, object]] = [
            {"title": "Lezione 12B", "start_page": 2, "end_page": 2}
        ]
        jobs = (
            product.start_selected_lessons(
                str(pdf.source.source_id), str(pdf.source.revision_id), selected, "stale"
            )
            if started
            else ()
        )
        repo.source_lifetime_service.retire(
            replace(context, session_id=None),
            pdf.source.source_id,
            "retire-pdf",
            expected_sequence=repo.events.projection(COURSE).sequence,
        )
        before = tuple(repo.events.read(COURSE))
        if started:
            product.advance(str(jobs[0]["job_id"]))
            assert product.status(str(jobs[0]["job_id"]))["stage"] == "stale"
        else:
            with pytest.raises(ValueError, match="rimossa"):
                product.start_selected_lessons(
                    str(pdf.source.source_id), str(pdf.source.revision_id), selected, "stale"
                )
        assert tuple(repo.events.read(COURSE)) == before


def test_partial_selection_retry_recovers_after_reopening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    selected: list[dict[str, object]] = [
        {"title": "Lezione 12B", "start_page": 2, "end_page": 2},
        {"title": "Lezione 12D", "start_page": 4, "end_page": 4},
    ]
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repo:
        pdf = prepare_pdf(repo)
        product = MaterialProduct(repo, _service_context())
        original = product.admit_extraction
        calls = 0

        def interrupt(**kwargs: object) -> TextIngestionResult:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("interrupted")
            return cast(Callable[..., TextIngestionResult], original)(**kwargs)

        monkeypatch.setattr(product, "admit_extraction", interrupt)
        with pytest.raises(RuntimeError, match="interrupted"):
            product.start_selected_lessons(
                str(pdf.source.source_id), str(pdf.source.revision_id), selected, "restart"
            )
        first_id = product.jobs()[0]["job_id"]
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repo:
        product = MaterialProduct(repo, _service_context())
        jobs = product.start_selected_lessons(
            str(pdf.source.source_id),
            str(pdf.source.revision_id),
            list(reversed(selected)),
            "restart",
        )
        assert jobs[0]["job_id"] == first_id
        assert len(product.jobs()) == 2
        extracted = [
            record
            for record in repo.for_course(COURSE).content.catalog()
            if record.source.extraction_provenance is not None
        ]
        assert len(extracted) == 2


@pytest.mark.parametrize(
    "extra",
    [
        {"selected_lessons": None},
        {"selected_lessons": []},
        {"selected_lessons": [{"title": "Lesson", "start_page": 2, "end_page": 2}], "lessons": []},
    ],
)
def test_invalid_selection_api_never_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: dict[str, object]
) -> None:
    from cardine.demo.ui_application import UiRequestError

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repo:
        pdf = prepare_pdf(repo)
        before = tuple(repo.events.read(COURSE))
    app = RepositoryUiApplication(
        root, COURSE, SESSION, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    )
    dispatched: list[str] = []
    monkeypatch.setattr(
        app, "_start_material_worker", lambda job, course, session: dispatched.append(job)
    )
    with pytest.raises(UiRequestError):
        app.post(
            "/api/v1/material-generations",
            {
                "schema_version": 1,
                "request_id": "invalid-api",
                "expected_sequence": 0,
                "payload": {
                    "source_id": str(pdf.source.source_id),
                    "revision_id": str(pdf.source.revision_id),
                    **extra,
                },
            },
        )
    assert not dispatched
    with LocalRepository.open(root) as repo:
        assert tuple(repo.events.read(COURSE)) == before
