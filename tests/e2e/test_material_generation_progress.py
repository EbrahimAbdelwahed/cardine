from __future__ import annotations

import base64
import os
from pathlib import Path
from threading import Event
from typing import cast

import pytest

from cardine.cli import (
    LocalRepository,
    ModelAdapterBuilder,
    ModelAdapterRegistry,
    initialize_local_repository,
)
from cardine.demo.ui_application import RepositoryUiApplication
from cardine.materials.product import MaterialProduct
from study_agent.adapters.model import GPT_5_6_LUNA_ADAPTER_ID
from study_agent.ports import ModelRequest, ModelResponse
from tests.e2e.test_cardine_repository_browser_journey import _real_browser, _serve
from tests.integration.test_material_generation_repository_composition import (
    COURSE,
    SESSION,
    ScriptedLuna,
    _config,
    _prepare,
    _service_context,
)
from tests.integration.test_material_product import _registry


class HeldLuna(ScriptedLuna):
    def __init__(self) -> None:
        super().__init__()
        self.boundaries = Event()
        self.segment = Event()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        assert request.structured_output is not None
        if request.structured_output.name == "material_boundaries_v1":
            assert self.boundaries.wait(30)
        elif request.structured_output.name == "material_complete_segment_v1":
            assert self.segment.wait(30)
        return await super().generate(request)


@pytest.mark.parametrize("existing_job", (False, True))
def test_notes_show_lesson_and_segment_progress_before_completion(
    tmp_path: Path,
    existing_job: bool,
) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, _config())
    with LocalRepository.open(
        root, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    ) as repository:
        admitted = _prepare(repository, consent=True)
        if existing_job:
            product = MaterialProduct(repository, _service_context())
            previous = product.start(
                str(admitted.source.source_id), str(admitted.source.revision_id), "previous-notes"
            )
            product.advance(str(previous["job_id"]))
    model = HeldLuna()
    registry = ModelAdapterRegistry(
        {
            GPT_5_6_LUNA_ADAPTER_ID: cast(ModelAdapterBuilder, lambda config, credential: model),
        }
    )
    app = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        model_adapters=registry,
        environment={"OPENAI_API_KEY": "fixture"},
    )
    try:
        with _serve(application=app) as url, _real_browser(url) as browser:
            browser.wait_for("Boolean(document.querySelector('[data-provider-consent]'))")
            browser.evaluate("document.querySelector('[data-route=fonti]').click()")
            browser.wait_for("Boolean(document.querySelector('[data-generate-notes]'))")
            browser.evaluate("document.querySelector('[data-generate-notes]').click()")
            browser.wait_for("Boolean(document.querySelector('#notes-structure-lesson'))")
            browser.evaluate(
                "const select = document.querySelector('#notes-structure-lesson');"
                "select.value = 'whole';"
                "select.dispatchEvent(new Event('change', {bubbles:true}));"
                "document.querySelector('[data-notes-lessons] button[type=submit]').click()"
            )
            browser.wait_for("Boolean(document.querySelector('.notes-job[data-key]'))")
            browser.wait_for("document.activeElement.classList.contains('notes-job')")
            browser.evaluate("window.notesJob = document.querySelector('.notes-job:last-child')")
            assert browser.evaluate("document.activeElement === window.notesJob")
            browser.wait_for(
                "window.notesJob.textContent.includes('Sto suddividendo')",
                timeout=6,
            )
            if directory := os.environ.get("CARDINE_NOTES_PROGRESS_VISUAL_DIR"):
                output = Path(directory)
                output.mkdir(parents=True, exist_ok=True)
                browser.evaluate("document.querySelector('#material-jobs').scrollIntoView()")
                shot = browser.call("Page.captureScreenshot", format="png")
                output.joinpath("segmenting.png").write_bytes(base64.b64decode(str(shot["data"])))
            assert not browser.evaluate("Boolean(document.querySelector('[data-note-resume]'))")
            model.boundaries.set()
            browser.wait_for(
                "window.notesJob.textContent.includes('segmento 1 di 1')",
                timeout=8,
            )
            assert browser.evaluate("window.notesJob.querySelector('progress').max") == 1
            assert browser.evaluate("window.notesJob.querySelector('progress').value") == 0
            assert browser.evaluate("window.notesJob.textContent.includes('Lesson')")
            if directory:
                shot = browser.call("Page.captureScreenshot", format="png")
                output.joinpath("generating-desktop.png").write_bytes(
                    base64.b64decode(str(shot["data"]))
                )
                browser.call(
                    "Emulation.setDeviceMetricsOverride",
                    width=390,
                    height=844,
                    deviceScaleFactor=1,
                    mobile=True,
                )
                browser.evaluate("document.querySelector('#material-jobs').scrollIntoView()")
                browser.evaluate(
                    "new Promise(resolve => setTimeout(resolve, 400))", await_promise=True
                )
                shot = browser.call("Page.captureScreenshot", format="png")
                output.joinpath("generating-mobile.png").write_bytes(
                    base64.b64decode(str(shot["data"]))
                )
                assert browser.evaluate("document.documentElement.scrollWidth <= 390")
                browser.call("Emulation.clearDeviceMetricsOverride")
            model.segment.set()
            browser.wait_for(
                "window.notesJob.querySelectorAll('[data-note-decision=accept]').length === 2"
            )
            assert browser.evaluate("window.notesJob.querySelector('progress').value") == 1
            assert not browser.evaluate("Boolean(document.querySelector('[data-note-resume]'))")
    finally:
        model.boundaries.set()
        model.segment.set()
