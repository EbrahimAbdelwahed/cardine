"""Real offline browser journeys for source structure and exact lesson selection."""

from pathlib import Path
from typing import cast

import pytest

from cardine.cli import LocalRepository, initialize_local_repository
from cardine.demo.ui_application import RepositoryUiApplication
from cardine.materials.product import MaterialProduct
from study_agent.domain._validation import JsonObject
from tests.e2e.test_cardine_repository_browser_journey import _real_browser, _serve
from tests.integration.test_material_generation_repository_composition import (
    COURSE,
    SESSION,
    _config,
    _service_context,
)
from tests.integration.test_material_product import _registry
from tests.integration.test_structure_lesson_notes import CONTENT, prepare_structure


@pytest.mark.parametrize("pdf", [False, True])
def test_dropdown_generates_exact_chosen_lesson(tmp_path: Path, pdf: bool) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repo:
        prepare_structure(repo, pdf=pdf)
    app = RepositoryUiApplication(
        root, COURSE, SESSION, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    )
    with _serve(application=app) as url, _real_browser(url) as browser:
        browser.wait_for("Boolean(document.querySelector('[data-route=fonti]'))")
        browser.evaluate("document.querySelector('[data-route=fonti]').click()")
        browser.wait_for("document.querySelectorAll('[data-generate-notes]').length === 2")
        browser.evaluate(
            "Array.from(document.querySelectorAll('[data-generate-notes]')).find("
            "button => JSON.parse(button.dataset.generateNotes).source_id === "
            "'structured-lessons').click()"
        )
        browser.wait_for("Boolean(document.querySelector('#notes-structure-lesson'))")
        assert browser.evaluate(
            "document.querySelector('[data-notes-lessons] [type=submit]').disabled"
        )
        browser.evaluate(
            "var select = document.querySelector('#notes-structure-lesson');"
            "select.value = Array.from(select.options).find(option => "
            "option.textContent.endsWith('Prima lezione')).value;"
            "select.dispatchEvent(new Event('change', {bubbles: true}));"
        )
        assert browser.evaluate(
            "document.querySelector('[data-notes-boundary]').textContent.includes('Prima lezione')"
        )
        browser.evaluate("document.querySelector('[data-notes-lessons] [type=submit]').click()")
        browser.wait_for(
            "document.querySelectorAll('[data-note-decision=accept]').length === 2", timeout=20
        )
        assert (
            browser.evaluate("document.querySelectorAll('#material-jobs .notes-job').length") == 1
        )
    with LocalRepository.open(root) as repo:
        product = MaterialProduct(repo, _service_context())
        jobs = product.jobs()
        assert len(jobs) == 1 and jobs[0]["title"] == "Prima lezione"
        extracted = product.source(str(jobs[0]["source_id"]), str(jobs[0]["revision_id"]))
        assert extracted.text == CONTENT[CONTENT.index("## Prima") : CONTENT.index("## Seconda")]
        assert all(
            output["publication"] == "pending"
            for output in cast(
                tuple[JsonObject, ...], product.status(str(jobs[0]["job_id"]))["outputs"]
            )
        )


def test_structure_error_loading_refresh_and_retry_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repo:
        prepare_structure(repo, pdf=True)
    app = RepositoryUiApplication(
        root, COURSE, SESSION, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    )
    with _serve(application=app) as url, _real_browser(url) as browser:
        browser.wait_for("Boolean(document.querySelector('[data-route=fonti]'))")
        browser.evaluate("document.querySelector('[data-route=fonti]').click()")
        browser.wait_for("document.querySelectorAll('[data-generate-notes]').length === 2")
        browser.evaluate(
            "window.prepareAttempts = 0; window.noteRequests = [];"
            "const originalFetch = window.fetch; window.fetch = (url, options) => {"
            "if (url === '/api/v1/material-generations/prepare') {"
            "window.prepareAttempts++; if (window.prepareAttempts === 1) {"
            "return new Promise((resolve, reject) => { window.failPrepare = reject; }); }"
            "if (window.prepareAttempts === 2) return Promise.resolve(new Response("
            "JSON.stringify({structure_status:'queued',structure:[],lessons:[],page_count:1}),"
            "{status:200,headers:{'Content-Type':'application/json'}})); }"
            "if (url === '/api/v1/material-generations' && options?.method === 'POST') {"
            "window.noteRequests.push(JSON.parse(options.body));"
            "return Promise.reject(new Error('Risposta persa')); }"
            "return originalFetch(url, options); };"
            "Array.from(document.querySelectorAll('[data-generate-notes]')).find("
            "button => JSON.parse(button.dataset.generateNotes).source_id === "
            "'structured-lessons').click()"
        )
        browser.wait_for("Boolean(document.querySelector('[data-notes-lessons][aria-busy=true]'))")
        browser.evaluate("window.failPrepare(new Error('Struttura irraggiungibile'))")
        browser.wait_for(
            "document.querySelector('#material-jobs').textContent.includes('irraggiungibile')"
        )
        browser.evaluate("document.querySelector('[data-notes-reload]').click()")
        browser.wait_for("Boolean(document.querySelector('#notes-structure-lesson'))")
        assert browser.evaluate(
            "document.querySelector('#material-jobs').textContent"
            ".includes('ancora in elaborazione')"
        )
        assert browser.evaluate(
            "document.querySelector('[data-notes-lessons] [type=submit]').disabled"
        )
        browser.evaluate("document.querySelector('[data-notes-reload]').click()")
        browser.wait_for("document.querySelectorAll('#notes-structure-lesson option').length > 2")
        browser.evaluate(
            "var select = document.querySelector('#notes-structure-lesson');"
            "select.value = Array.from(select.options).find(option => "
            "option.textContent.endsWith('Prima lezione')).value;"
            "select.dispatchEvent(new Event('change', {bubbles:true}));"
        )
        for count in range(1, 4):
            browser.evaluate("document.querySelector('[data-notes-lessons] [type=submit]').click()")
            browser.wait_for(
                f"window.noteRequests.length === {count} && "
                "!document.querySelector('[data-notes-lessons] [type=submit]').disabled"
            )
            browser.evaluate(
                "var select = document.querySelector('#notes-structure-lesson');"
                + (
                    "select.value = Array.from(select.options).find(option => "
                    "option.textContent.endsWith('Seconda lezione')).value;"
                    if count == 2
                    else ""
                )
                + "select.dispatchEvent(new Event('change', {bubbles:true}));"
            )
        assert browser.evaluate(
            "JSON.stringify(window.noteRequests[0]) === JSON.stringify(window.noteRequests[1])"
        )
        assert browser.evaluate(
            "window.noteRequests[0].request_id !== window.noteRequests[2].request_id"
        )
        assert browser.evaluate(
            "window.noteRequests[0].payload.structure_lesson.title === 'Prima lezione'"
        )
        browser.evaluate("document.querySelector('[data-notes-cancel]').click()")
        browser.wait_for("!document.querySelector('[data-notes-lessons]')")


def test_picker_survives_same_scope_background_bootstrap_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from threading import Event

    root = tmp_path / "repo"
    initialize_local_repository(root, _config())
    with LocalRepository.open(root) as repo:
        prepare_structure(repo)
    app = RepositoryUiApplication(
        root, COURSE, SESSION, model_adapters=_registry(), environment={"OPENAI_API_KEY": "fixture"}
    )
    original_get, original_post = app.get, app.post
    release_index = [Event(), Event()]
    release_prepare = Event()
    counts = {"bootstrap": 0, "index": 0, "enabled": 0}

    def get(path: str) -> JsonObject:
        if path == "/api/v1/indexing/status" and counts["enabled"]:
            index = counts["index"]
            counts["index"] += 1
            assert release_index[index].wait(10)
        payload = original_get(path)
        if path == "/api/v1/bootstrap" and counts["enabled"]:
            counts["bootstrap"] += 1
            return {
                **payload,
                "course": {**cast(JsonObject, payload["course"]),
                           "title": f"Refresh {counts['bootstrap']}"},
                "indexing": {"status": "queued" if counts["bootstrap"] < 3 else "ready"},
            }
        return payload

    def post(path: str, command: dict[str, object]) -> JsonObject:
        response = original_post(path, command)
        if path == "/api/v1/material-generations/prepare":
            assert release_prepare.wait(10)
        return response

    monkeypatch.setattr(app, "get", get)
    monkeypatch.setattr(app, "post", post)
    try:
        with _serve(application=app) as url, _real_browser(url) as browser:
            browser.wait_for("Boolean(document.querySelector('[data-route=fonti]'))")
            counts["enabled"] = 1
            browser.navigate(url + "/?scope-refresh-regression")
            browser.wait_for(
                "document.querySelector('#rail-course').textContent.includes('Refresh 1')"
            )
            browser.evaluate("document.querySelector('[data-route=fonti]').click()")
            browser.wait_for("document.querySelectorAll('[data-generate-notes]').length === 2")
            browser.evaluate(
                "Array.from(document.querySelectorAll('[data-generate-notes]')).find("
                "b=>JSON.parse(b.dataset.generateNotes).source_id==='structured-lessons').click()"
            )
            browser.wait_for("Boolean(document.querySelector('[data-notes-lessons][aria-busy=true]'))")
            release_index[0].set()
            browser.wait_for(
                "document.querySelector('#rail-course').textContent.includes('Refresh 2')"
            )
            release_prepare.set()
            browser.wait_for("Boolean(document.querySelector('#notes-structure-lesson'))")
            release_index[1].set()
            browser.wait_for(
                "document.querySelector('#rail-course').textContent.includes('Refresh 3')"
            )
            browser.evaluate(
                "const select=document.querySelector('#notes-structure-lesson');"
                "select.value=Array.from(select.options).find("
                "o=>o.textContent.endsWith('Prima lezione')).value;"
                "select.dispatchEvent(new Event('change',{bubbles:true}));"
                "document.querySelector('[data-notes-lessons] [type=submit]').click();"
            )
            browser.wait_for("document.querySelectorAll('[data-note-decision=accept]').length===2")
    finally:
        release_prepare.set()
        for event in release_index:
            event.set()
