"""Offline browser journey over real material jobs and canonical decisions."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cardine.cli import LocalRepository, initialize_local_repository
from cardine.demo.ui_application import RepositoryUiApplication
from tests.e2e.test_cardine_repository_browser_journey import _real_browser, _serve
from tests.integration.test_material_generation_repository_composition import (
    COURSE,
    SESSION,
    _config,
    _prepare,
)
from tests.integration.test_material_product import _registry


def test_source_notes_can_be_generated_reviewed_and_published(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repository:
        _prepare(repository, consent=True)
    app = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        model_adapters=_registry(),
        environment={"OPENAI_API_KEY": "fixture"},
    )
    with _serve(application=app) as url, _real_browser(url) as browser:
        browser.wait_for("Boolean(document.querySelector('[data-route=fonti]'))")
        browser.evaluate("document.querySelector('[data-route=fonti]').click()")
        browser.wait_for("Boolean(document.querySelector('[data-generate-notes]'))")
        browser.evaluate("document.querySelector('[data-generate-notes]').click()")
        browser.wait_for(
            "document.querySelectorAll('[data-note-decision=accept]').length === 2", timeout=20
        )
        browser.evaluate("document.querySelector('.notes-output').open = true")
        browser.wait_for(
            "document.querySelector('.notes-markdown').textContent.includes('Lecture 12')"
        )
        if visual_dir := os.environ.get("CARDINE_NOTES_VISUAL_DIR"):
            output = Path(visual_dir)
            output.mkdir(parents=True, exist_ok=True)
            browser.evaluate(
                "document.querySelector('#material-jobs').scrollIntoView({block:'start'})"
            )
            browser.evaluate("new Promise(resolve => setTimeout(resolve, 400))", await_promise=True)
            shot = browser.call("Page.captureScreenshot", format="png")
            output.joinpath("desktop.png").write_bytes(base64.b64decode(str(shot["data"])))
            browser.call(
                "Emulation.setDeviceMetricsOverride",
                width=390,
                height=844,
                deviceScaleFactor=1,
                mobile=True,
            )
            browser.evaluate(
                "document.querySelector('#material-jobs').scrollIntoView({block:'start'})"
            )
            browser.evaluate("new Promise(resolve => setTimeout(resolve, 400))", await_promise=True)
            shot = browser.call("Page.captureScreenshot", format="png")
            output.joinpath("mobile.png").write_bytes(base64.b64decode(str(shot["data"])))
            assert browser.evaluate("document.documentElement.scrollWidth <= 390")
            browser.call("Emulation.clearDeviceMetricsOverride")
        assert (
            browser.evaluate("getComputedStyle(document.querySelector('#source-viewer')).display")
            == "none"
        )
        # No proposal becomes a canonical source through preview/polling alone.
        assert browser.evaluate("document.querySelectorAll('[data-generate-notes]').length") == 1
        browser.evaluate("document.querySelector('[data-note-decision=accept]').click()")
        browser.wait_for("document.querySelectorAll('[data-note-decision=accept]').length === 1")
        browser.evaluate("document.querySelector('[data-note-decision=accept]').click()")
        browser.wait_for(
            "document.querySelector('#material-jobs').textContent.includes('Salvato come fonte')"
        )
        browser.wait_for("document.querySelectorAll('[data-note-decision=accept]').length === 0")
        browser.navigate(url + "/#fonti")
        browser.wait_for(
            "document.querySelector('#material-jobs').textContent.includes('Salvato come fonte')"
        )
        assert not browser.evaluate(
            "document.querySelector('[data-note-decision=accept]') !== null"
        )
        assert browser.evaluate("document.querySelectorAll('[data-generate-notes]').length") == 1
