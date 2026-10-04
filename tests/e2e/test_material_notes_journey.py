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
        browser.wait_for("Boolean(document.querySelector('[data-provider-consent]'))")
        browser.evaluate("document.querySelector('[data-route=fonti]').click()")
        browser.wait_for("Boolean(document.querySelector('[data-generate-notes]'))")
        browser.evaluate("document.querySelector('[data-generate-notes]').click()")
        browser.wait_for("Boolean(document.querySelector('#notes-structure-lesson'))")
        browser.evaluate(
            "const selection = document.querySelector('#notes-structure-lesson');"
            "selection.value = 'whole';"
            "selection.dispatchEvent(new Event('change', {bubbles: true}));"
            "document.querySelector('[data-notes-lessons] button[type=submit]').click()"
        )
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


def test_pdf_notes_generate_only_checked_lessons(tmp_path: Path) -> None:
    from tests.integration.test_selected_lesson_notes import prepare_pdf

    root = tmp_path / "repository"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repository:
        pdf = prepare_pdf(repository)
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
        browser.wait_for("document.querySelectorAll('[data-generate-notes]').length === 2")
        browser.evaluate(
            "Array.from(document.querySelectorAll('[data-generate-notes]')).find("
            "button => JSON.parse(button.dataset.generateNotes).source_id === "
            "'selected-pdf').click()"
        )
        browser.wait_for("Boolean(document.querySelector('[data-notes-boundaries]'))")
        browser.evaluate("document.querySelector('[data-notes-boundaries]').click()")
        browser.wait_for("Boolean(document.querySelector('[data-notes-lessons] textarea'))")
        # Corrected titles survive returning from selection to the boundary editor.
        browser.evaluate(
            "const editor = document.querySelector('[data-notes-lessons] textarea');"
            "editor.value = editor.value.replace('Lezione 12B', 'Seconda lezione');"
            "document.querySelector('[data-notes-lessons] button[type=submit]').click()"
        )
        browser.wait_for("document.querySelectorAll('input[name=lesson]').length === 4")
        assert browser.evaluate(
            "document.querySelector('[data-notes-lessons] button[type=submit]').disabled"
        )
        browser.evaluate("document.querySelector('[data-notes-edit]').click()")
        assert browser.evaluate(
            "document.querySelector('[data-notes-lessons] textarea').value"
            ".includes('Seconda lezione')"
        )
        browser.evaluate(
            "document.querySelector('[data-notes-lessons] button[type=submit]').click()"
        )
        browser.evaluate("document.querySelector('[data-notes-select-all]').click()")
        assert (
            browser.evaluate("document.querySelectorAll('input[name=lesson]:checked').length") == 4
        )
        browser.evaluate(
            "document.querySelectorAll('input[name=lesson]').forEach((input, index) => {"
            "input.checked = index === 1 || index === 3; "
            "input.dispatchEvent(new Event('change', {bubbles: true})); });"
        )
        assert (
            browser.evaluate(
                "document.querySelector('[data-notes-lessons] button[type=submit]').textContent"
            )
            == "Genera note per 2 lezioni"
        )
        if visual_dir := os.environ.get("CARDINE_NOTES_VISUAL_DIR"):
            output = Path(visual_dir)
            output.mkdir(parents=True, exist_ok=True)
            browser.evaluate(
                "document.querySelector('#material-jobs').scrollIntoView({block:'start'})"
            )
            browser.evaluate("new Promise(resolve => setTimeout(resolve, 400))", await_promise=True)
            shot = browser.call("Page.captureScreenshot", format="png")
            output.joinpath("selection-desktop.png").write_bytes(
                base64.b64decode(str(shot["data"]))
            )
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
            output.joinpath("selection-mobile.png").write_bytes(base64.b64decode(str(shot["data"])))
            assert browser.evaluate("document.documentElement.scrollWidth <= 390")
            browser.call("Emulation.clearDeviceMetricsOverride")
        browser.evaluate(
            "document.querySelector('[data-notes-lessons] button[type=submit]').click()"
        )
        browser.wait_for(
            "document.querySelectorAll('[data-note-decision=accept]').length === 4", timeout=20
        )
        assert (
            browser.evaluate("document.querySelectorAll('#material-jobs .notes-job').length") == 2
        )
        browser.navigate(url + "/#fonti")
        browser.wait_for("document.querySelectorAll('[data-note-decision=accept]').length === 4")
    with LocalRepository.open(root) as repository:
        from cardine.materials.product import MaterialProduct
        from tests.integration.test_material_generation_repository_composition import (
            _service_context,
        )

        jobs = MaterialProduct(repository, _service_context()).jobs()
        assert {job["title"] for job in jobs} == {"Seconda lezione", "Lezione 12D"}
        assert any(
            record.source.revision_id == pdf.source.revision_id and record.is_current_revision
            for record in repository.for_course(COURSE).content.catalog()
        )


def test_pdf_notes_retry_id_survives_unchanged_select_all(tmp_path: Path) -> None:
    from tests.integration.test_selected_lesson_notes import prepare_pdf

    root = tmp_path / "repository"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repository:
        prepare_pdf(repository)
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
        browser.wait_for("document.querySelectorAll('[data-generate-notes]').length === 2")
        browser.evaluate(
            "Array.from(document.querySelectorAll('[data-generate-notes]')).find("
            "button => JSON.parse(button.dataset.generateNotes).source_id === "
            "'selected-pdf').click()"
        )
        browser.wait_for("Boolean(document.querySelector('[data-notes-boundaries]'))")
        browser.evaluate("document.querySelector('[data-notes-boundaries]').click()")
        browser.wait_for("Boolean(document.querySelector('[data-notes-lessons] textarea'))")
        browser.evaluate(
            "document.querySelector('[data-notes-lessons] button[type=submit]').click()"
        )
        browser.wait_for("document.querySelectorAll('input[name=lesson]').length === 4")
        # Capture actual POST payloads and simulate a lost response without model calls.
        browser.evaluate(
            "window.noteRequests = []; const originalFetch = window.fetch;"
            "window.fetch = (url, options) => {"
            "if (url === '/api/v1/material-generations' && options?.method === 'POST') {"
            "window.noteRequests.push(JSON.parse(options.body));"
            "return Promise.reject(new Error('Lost response')); }"
            "return originalFetch(url, options); };"
            "document.querySelector('[data-notes-select-all]').click()"
        )
        for attempt in range(1, 4):
            browser.evaluate(
                "document.querySelector('[data-notes-lessons] button[type=submit]').click()"
            )
            browser.wait_for(
                f"window.noteRequests.length === {attempt} && "
                "!document.querySelector('[data-notes-lessons] button[type=submit]').disabled"
            )
            if attempt == 1:
                # Selecting every already-selected lesson must retain the failed request.
                browser.evaluate("document.querySelector('[data-notes-select-all]').click()")
            elif attempt == 2:
                browser.evaluate(
                    "const input = document.querySelector('input[name=lesson]');"
                    "input.checked = false;"
                    "input.dispatchEvent(new Event('change', {bubbles: true}))"
                )
        assert browser.evaluate(
            "JSON.stringify(window.noteRequests[0]) === JSON.stringify(window.noteRequests[1])"
        )
        assert browser.evaluate(
            "window.noteRequests[0].request_id !== window.noteRequests[2].request_id"
        )
        assert browser.evaluate("window.noteRequests[0].payload.selected_lessons.length === 4")
        assert browser.evaluate("window.noteRequests[2].payload.selected_lessons.length === 3")
