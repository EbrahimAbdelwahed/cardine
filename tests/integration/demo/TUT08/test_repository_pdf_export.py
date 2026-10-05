from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from tests.integration.demo.TUT08.test_repository_materials_artifacts_context import (
        COURSE,
        SESSION,
        _repository,
    )
else:
    try:
        from tests.integration.demo.TUT08.test_repository_materials_artifacts_context import (
            COURSE,
            SESSION,
            _repository,
        )
    except ModuleNotFoundError:
        from test_repository_materials_artifacts_context import COURSE, SESSION, _repository

from cardine.demo.ui_application import RepositoryUiApplication, UiRequestError
from cardine.documents.typst_notes import TypstRenderer


def _first_text_source(app: RepositoryUiApplication) -> tuple[str, str]:
    items = cast(Any, app.get("/api/v1/materials"))["items"]
    item = next(row for row in items if row["viewer"]["kind"] in {"markdown", "text"})
    return item["source_id"], item["revision_id"]


@pytest.mark.skipif(not TypstRenderer().available(), reason="Typst is not installed")
def test_a_text_source_exports_as_a_typst_pdf(tmp_path: Path) -> None:
    root, _revision = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)
    assert cast(Any, app.get("/api/v1/bootstrap"))["features"]["pdf_export"] is True
    document = app.read_source_pdf(*_first_text_source(app))
    assert document.media_type == "application/pdf"
    assert document.content.startswith(b"%PDF-")


def test_without_typst_the_export_is_an_explicit_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CARDINE_TYPST_BIN", str(tmp_path / "missing-typst"))
    root, _revision = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)
    assert cast(Any, app.get("/api/v1/bootstrap"))["features"]["pdf_export"] is False
    with pytest.raises(UiRequestError) as raised:
        app.read_source_pdf(*_first_text_source(app))
    assert raised.value.status_code == 503
    assert raised.value.diagnostic_code == "typst_unavailable"


def test_an_unknown_revision_is_not_found(tmp_path: Path) -> None:
    root, _revision = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)
    with pytest.raises(UiRequestError) as raised:
        app.read_source_pdf("source-missing", "revision-missing")
    assert raised.value.status_code == 404
