"""Deterministic browser-surface contracts for the Cardine product shell.

The repository intentionally has no browser-driver dependency.  These tests
exercise the packaged surface over its real HTTP boundary and run the small
keyboard event handler in Node with a minimal DOM seam.  They therefore catch
route, accessibility, and submission regressions without depending on pixels
or a particular browser installation.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from study_agent.demo.browser import create_server

DEMO_DIR = Path(__file__).parents[2] / "src" / "study_agent" / "demo"
ROUTES = {
    "oggi": "/api/v1/bootstrap",
    "sessione": "/api/v1/session",
    "fonti": "/api/v1/materials",
    "proposte": "/api/v1/artifacts",
    "verifiche": "/api/v1/assessments",
    "evidenze": "/api/v1/evidence",
    "ripasso": "/api/v1/recall/due",
    "piano": "/api/v1/plan",
    "conflitti": "/api/v1/context/conflicts",
}


class _RouteParser(HTMLParser):
    """Collect route controls and their accessible text from the packaged page."""

    def __init__(self) -> None:
        super().__init__()
        self.route_labels: dict[str, list[str]] = {}
        self.current_route: str | None = None
        self.current_text: list[str] = []
        self.details_open = False
        self.ids: dict[str, dict[str, str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if values.get("id"):
            self.ids[values["id"]] = values
        if tag == "details" and "open" in values:
            self.details_open = True
        if tag in {"button", "a"} and values.get("data-route"):
            self.current_route = values["data-route"]
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_route is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"button", "a"} and self.current_route is not None:
            self.route_labels.setdefault(self.current_route, []).append(
                " ".join("".join(self.current_text).split())
            )
            self.current_route = None
            self.current_text = []


@pytest.fixture()
def browser_url() -> Iterator[str]:
    try:
        server = create_server("127.0.0.1", 0)
    except PermissionError as error:
        pytest.skip(f"local sockets are unavailable in this test sandbox: {error}")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _get(url: str, path: str) -> tuple[int, object, str]:
    request = Request(f"{url}{path}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
            try:
                payload: object = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = raw
            return response.status, payload, raw
    except HTTPError as error:
        raw = error.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw
        return error.code, payload, raw


def _post(url: str, path: str, payload: object) -> tuple[int, object, str]:
    request = Request(
        f"{url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None, raw
    except HTTPError as error:
        raw = error.read().decode("utf-8")
        return error.code, json.loads(raw) if raw else None, raw


def test_real_http_surface_serves_every_navigation_route(browser_url: str) -> None:
    """Each visible nav destination has a bounded API read or local state."""

    page_status, page_body, _ = _get(browser_url, "/")
    assert page_status == 200
    assert isinstance(page_body, str)
    assert "<title>Cardine · Study Agent</title>" in page_body

    for route, endpoint in ROUTES.items():
        status, payload, _ = _get(browser_url, endpoint)
        assert status == 200, route
        assert isinstance(payload, dict), route
        assert payload.get("schema_version") == 1, route
        assert "status" in payload or "shell_status" in payload, route


def test_navigation_sidebar_is_expanded_and_accessible() -> None:
    page = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
    css = (DEMO_DIR / "browser.css").read_text(encoding="utf-8")
    parser = _RouteParser()
    parser.feed(page)

    assert parser.details_open
    assert set(ROUTES).issubset(parser.route_labels)
    assert any("Nuova sessione" in label for label in parser.route_labels["oggi"])
    assert parser.ids["rail"].get("aria-label") == "Navigazione del corso"
    assert parser.ids["navigation"].get("aria-label") == "Sezioni"
    assert '.rail {\n' in css
    assert "width: 244px" in css


def test_chat_home_and_session_markers_preserve_learner_tutor_boundary() -> None:
    page = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")
    css = (DEMO_DIR / "browser.css").read_text(encoding="utf-8")

    assert 'id="entry-form"' in page
    assert 'id="conversation-heading"' in page
    assert "class=\"chat-home\"" in javascript
    assert "class=\"chat-session\"" in javascript
    assert "thread-message--learner" in javascript
    assert "thread-message--assistant" in javascript
    assert "thread-message--learner" in css
    assert "thread-message--assistant" in css
    assert 'learner ? "tu"' in javascript
    assert 'role === "system" ? "sistema" : "tutor"' in javascript


def test_unavailable_routes_are_honest_and_isolated(browser_url: str) -> None:
    for endpoint in (ROUTES["proposte"], ROUTES["verifiche"], ROUTES["ripasso"], ROUTES["piano"]):
        status, payload, _ = _get(browser_url, endpoint)
        assert status == 200
        assert isinstance(payload, dict)
        assert payload["status"] == "unavailable"
        assert payload["items"] == []
        assert payload["message"]

    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")
    assert "function renderUnavailable(route)" in javascript
    assert "Nessuna degradazione globale" in javascript
    assert "Piano non disponibile" in javascript


def test_error_surface_has_retry_path_and_unknown_api_fails_closed(browser_url: str) -> None:
    status, payload, _ = _get(browser_url, "/api/v1/does-not-exist")
    assert status == 404
    assert payload == {"error": "route not found"}

    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")
    assert "function renderError(route, error)" in javascript
    assert 'data-retry-route="${escapeAttribute(route)}"' in javascript
    assert 'kind === "error" ? "error-state"' in javascript
    assert 'data-retry-command' in javascript
    assert 'message.setAttribute("role", "alert")' in javascript

    sequence_status, bootstrap, _ = _get(browser_url, ROUTES["oggi"])
    assert sequence_status == 200
    assert isinstance(bootstrap, dict)
    sequence = bootstrap["high_water_sequence"]
    assert isinstance(sequence, int) and sequence > 0
    stale_status, stale_payload, _ = _post(
        browser_url,
        "/api/v1/session/turns",
        {
            "schema_version": 1,
            "request_id": "e2e-stale-command",
            "expected_sequence": sequence - 1,
            "payload": {"content": "retry this command"},
        },
    )
    assert stale_status == 409
    assert stale_payload == {"error": "expected sequence is stale"}


def test_accessibility_contract_has_labels_focus_targets_and_live_status() -> None:
    page = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert '<a class="skip-link" href="#main-content">' in page
    assert '<main id="main-content"' in page
    assert 'id="global-status" role="status" aria-live="polite"' in page
    assert 'id="view-root" class="view-root" aria-live="polite"' in page
    assert 'id="rail-toggle" aria-expanded="false" aria-controls="rail"' in page
    assert 'id="trust-drawer" aria-labelledby="trust-heading"' in page
    assert 'id="provenance-drawer" aria-labelledby="drawer-heading"' in page
    assert '<label for="entry">Da dove vuoi iniziare?</label>' in page
    assert 'id="entry" name="learner_entry"' in page
    assert '$("#main-content").focus({ preventScroll: true })' in javascript


def test_keyboard_handler_observes_enter_shift_enter_ime_blank_and_busy_contract() -> None:
    """Run the production handler against a minimal event/form seam in Node."""

    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")
    match = re.search(
        r"function submitComposerFromKeyboard\(event, form\) \{.*?\n  \}",
        javascript,
        flags=re.DOTALL,
    )
    assert match, "keyboard handler must remain a callable behavioral seam"
    handler = match.group(0)
    script = f"""
const state = {{ loading: false }};
function text(value, fallback = "") {{
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string" || typeof value === "number" ||
      typeof value === "boolean") return String(value);
  return fallback;
}}
function $(selector, root) {{ return root.querySelector(selector); }}
{handler}
function run(value, options = {{}}, loading = false, disabled = false) {{
  state.loading = loading;
  let submits = 0;
  let prevented = false;
  const textarea = {{ value, disabled }};
  const form = {{
    querySelector: () => textarea,
    requestSubmit: () => {{ submits += 1; }},
  }};
  const event = {{
    key: "Enter",
    shiftKey: false,
    isComposing: false,
    keyCode: 13,
    preventDefault: () => {{ prevented = true; }},
    ...options,
  }};
  submitComposerFromKeyboard(event, form);
  return {{ submits, prevented }};
}}
const cases = {{
  enter: run("a valid learner question"),
  shiftEnter: run("line one", {{ shiftKey: true }}),
  composing: run("候補", {{ isComposing: true }}),
  imeKeyCode: run("候補", {{ keyCode: 229 }}),
  blank: run("   "),
  busy: run("busy", {{}}, true),
  disabled: run("disabled", {{}}, false, true),
}};
console.log(JSON.stringify(cases));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=DEMO_DIR.parents[2],
    )
    cases = json.loads(result.stdout)
    assert cases["enter"] == {"submits": 1, "prevented": True}
    for name in ("shiftEnter", "composing", "imeKeyCode", "blank", "busy", "disabled"):
        assert cases[name]["submits"] == 0, name
    for name in ("shiftEnter", "composing", "imeKeyCode"):
        assert cases[name]["prevented"] is False, name
