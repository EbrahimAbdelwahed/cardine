"""Real-browser closure journey for the repository-backed Cardine UI.

The test uses Chrome's DevTools protocol directly so the project does not need
to add a browser automation dependency.  It is skipped only when no supported
local Chromium browser is installed.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from typing import cast
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest

from cardine.cli.repository import LocalRepository, ModelAdapterRegistry
from cardine.demo.browser import create_server
from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.domain import (
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    PrincipalKind,
    SessionId,
    SourceId,
)
from study_agent.domain._validation import JsonObject
from study_agent.ports import (
    CancellationToken,
    ModelCapabilities,
    ModelError,
    ModelErrorCode,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
)
from study_agent.repository_config import LocalRepositoryConfig, ModelAdapterConfig

COURSE = CourseId("browser-closure-course")
SESSION = SessionId("browser-closure-session")
ROUTES = (
    "oggi",
    "sessione",
    "fonti",
    "proposte",
    "verifiche",
    "evidenze",
    "ripasso",
    "piano",
    "conflitti",
)
API_PATHS = (
    "/api/v1/bootstrap",
    "/api/v1/session",
    "/api/v1/materials",
    "/api/v1/artifacts",
    "/api/v1/assessments",
    "/api/v1/evidence",
    "/api/v1/recall/due",
    "/api/v1/plan",
    "/api/v1/context/conflicts",
)


class _BrowserModel:
    capabilities = ModelCapabilities(structured_output=True)

    def __init__(self, *, fail_grounding_once: bool = False) -> None:
        self.requests: list[ModelRequest] = []
        self._fail_grounding_once = fail_grounding_once

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.metadata.get("prompt_id") == "explain_concept.v1":
            if self._fail_grounding_once:
                self._fail_grounding_once = False
                raise ModelError(ModelErrorCode.TIMEOUT, "browser fixture timeout")
            rendered = "\n".join(message.content for message in request.messages)
            evidence = re.search(r'"evidence_id":"([^"]+)"', rendered)
            assert evidence is not None
            structured_output: JsonObject = {
                "status": "answered",
                "segments": (
                    {
                        "kind": "supported_claim",
                        "text": "The aortic valve has three cusps.",
                        "evidence_ids": (evidence.group(1),),
                    },
                ),
                "unsupported_information_note": None,
            }
        else:
            structured_output = {
                "decision": {
                    "kind": "assistant_message",
                    "message": "ok",
                }
            }
        return ModelResponse(
            "",
            None,
            ModelFinishReason.STOP,
            ModelInvocation("browser-fixture", "1.0.0", "fixture", "browser"),
            structured_output=structured_output,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        del request
        if False:  # pragma: no cover - protocol-only async generator
            yield cast(ModelStreamEvent, None)
        raise AssertionError("browser fixture does not stream")

    async def cancel(self, token: CancellationToken) -> None:
        del token
        raise AssertionError("browser fixture cancellation is not supported")


def _repository(
    tmp_path: Path,
    *,
    with_source: bool = True,
    fail_grounding_once: bool = False,
) -> tuple[Path, ModelAdapterRegistry, _BrowserModel]:
    root = tmp_path / "repository"
    initialize_local_repository(
        root,
        LocalRepositoryConfig(ModelAdapterConfig("browser-fixture", {}, None)),
    )
    model = _BrowserModel(fail_grounding_once=fail_grounding_once)
    adapters = ModelAdapterRegistry(
        {"browser-fixture": lambda _config, _credential: model},
        versions={"browser-fixture": "1.0.0"},
    )
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        repository.course_service.create(
            CourseProfile(COURSE, "Browser closure", "en", learning_goals=("Study",)),
            ExecutionContext(
                PrincipalKind.SERVICE,
                "browser-course",
                COURSE,
                CorrelationId("browser-course-create"),
            ),
        )
        if with_source:
            repository.for_course(COURSE).ingestion.ingest(
                filename="valves.md",
                content=b"The aortic valve has three cusps.",
                source_id=SourceId("browser-source"),
                title="Valve notes",
                trust_level=90,
                source_role="primary",
                context=ExecutionContext(
                    PrincipalKind.SERVICE,
                    "browser-ingest",
                    COURSE,
                    CorrelationId("browser-source-ingest"),
                ),
            )
        repository.session_service.start(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "browser-session",
                COURSE,
                CorrelationId("browser-session-start"),
                session_id=SESSION,
            )
        )
        repository.provider_consent_service.grant(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "browser-session",
                COURSE,
                CorrelationId("browser-provider-consent"),
            ),
            "browser-provider-consent",
        )
    return root, adapters, model


@contextmanager
def _serve(*, application: RepositoryUiApplication) -> Iterator[str]:
    server = create_server(
        "127.0.0.1",
        0,
        ui_application=application,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = cast(tuple[str, int], server.server_address)
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _chrome_binary() -> str | None:
    configured = os.environ.get("CARDINE_CHROME_BINARY")
    candidates = (
        configured,
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    )
    return next((item for item in candidates if item and Path(item).is_file()), None)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return cast(int, probe.getsockname()[1])


def _read_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ConnectionError("DevTools websocket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class _DevTools:
    def __init__(self, websocket_url: str) -> None:
        parsed = urlsplit(websocket_url)
        self._socket = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._socket.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            response += self._socket.recv(4096)
        if not response.startswith(b"HTTP/1.1 101"):
            raise ConnectionError(response.decode("latin-1", errors="replace"))
        self._next_id = 0
        self.events: list[dict[str, object]] = []

    def close(self) -> None:
        self._socket.close()

    def _send(self, payload: dict[str, object]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        if len(data) < 126:
            header.append(0x80 | len(data))
        elif len(data) < 65_536:
            header.extend((0x80 | 126, *struct.pack("!H", len(data))))
        else:
            header.extend((0x80 | 127, *struct.pack("!Q", len(data))))
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(data))
        self._socket.sendall(bytes(header) + mask + masked)

    def _receive(self) -> dict[str, object]:
        first, second = _read_exact(self._socket, 2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", _read_exact(self._socket, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _read_exact(self._socket, 8))[0]
        masked = bool(second & 0x80)
        mask = _read_exact(self._socket, 4) if masked else b""
        data = _read_exact(self._socket, length)
        if masked:
            data = bytes(value ^ mask[index % 4] for index, value in enumerate(data))
        if opcode == 0x9:
            self._send({"method": "Runtime.ping"})
            return self._receive()
        if opcode != 0x1:
            return self._receive()
        return cast(dict[str, object], json.loads(data))

    def call(self, method: str, **params: object) -> dict[str, object]:
        self._next_id += 1
        command_id = self._next_id
        self._send({"id": command_id, "method": method, "params": params})
        while True:
            message = self._receive()
            if message.get("id") == command_id:
                if "error" in message:
                    raise AssertionError(message["error"])
                return cast(dict[str, object], message.get("result", {}))
            self.events.append(message)

    def evaluate(self, expression: str, *, await_promise: bool = False) -> object:
        response = self.call(
            "Runtime.evaluate",
            expression=expression,
            awaitPromise=await_promise,
            returnByValue=True,
        )
        if response.get("exceptionDetails"):
            raise AssertionError(response["exceptionDetails"])
        result = cast(dict[str, object], response["result"])
        return result.get("value")

    def wait_for(self, expression: str, *, timeout: float = 10) -> object:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.evaluate(expression)
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError(f"browser condition did not become true: {expression}")

    def navigate(self, url: str) -> None:
        self.call("Page.navigate", url=url)
        self.wait_for("document.readyState === 'complete'")


@contextmanager
def _real_browser(url: str) -> Iterator[_DevTools]:
    binary = _chrome_binary()
    if binary is None:
        pytest.skip("Chrome/Chromium is not installed; real-browser evidence is unavailable")
    port = _free_port()
    with TemporaryDirectory(prefix="cardine-browser-") as profile:
        process = subprocess.Popen(
            [
                binary,
                "--headless=new",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-extensions",
                "--disable-sync",
                "--window-size=1440,1000",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            endpoint = f"http://127.0.0.1:{port}/json/list"
            deadline = time.monotonic() + 10
            targets: list[dict[str, object]] = []
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail("Chrome exited before exposing the DevTools endpoint")
                try:
                    with urlopen(endpoint, timeout=0.5) as response:
                        targets = cast(list[dict[str, object]], json.load(response))
                    if targets:
                        break
                except OSError:
                    time.sleep(0.05)
            if not targets:
                pytest.fail("Chrome did not expose a page target")
            target = next(item for item in targets if item.get("type") == "page")
            browser = _DevTools(cast(str, target["webSocketDebuggerUrl"]))
            browser.call("Page.enable")
            browser.call("Runtime.enable")
            browser.call("Log.enable")
            browser.call("Network.enable")
            browser.call(
                "Page.addScriptToEvaluateOnNewDocument",
                source=(
                    "window.__cardineErrors=[];"
                    "addEventListener('error',e=>__cardineErrors.push(String(e.message)));"
                    "addEventListener('unhandledrejection',"
                    "e=>__cardineErrors.push(String(e.reason)));"
                ),
            )
            browser.call("Page.reload", ignoreCache=True)
            browser.wait_for("document.readyState === 'complete'")
            yield browser
            browser.close()
        finally:
            if hasattr(os, "killpg"):
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if hasattr(os, "killpg"):
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=5)


def _press(browser: _DevTools, key: str, code: int) -> None:
    browser.call(
        "Input.dispatchKeyEvent",
        type="rawKeyDown",
        key=key,
        code=key,
        windowsVirtualKeyCode=code,
        nativeVirtualKeyCode=code,
    )
    browser.call(
        "Input.dispatchKeyEvent",
        type="keyUp",
        key=key,
        code=key,
        windowsVirtualKeyCode=code,
        nativeVirtualKeyCode=code,
    )


def _assert_no_browser_errors(
    browser: _DevTools, *, allowed_error_suffixes: tuple[str, ...] = ()
) -> None:
    assert browser.evaluate("window.__cardineErrors") == []
    severe = [
        event
        for event in browser.events
        if event.get("method") == "Runtime.exceptionThrown"
        or (
            (
                event.get("method") == "Log.entryAdded"
                and cast(
                    dict[str, object],
                    cast(dict[str, object], event.get("params", {})).get("entry", {}),
                ).get("level")
                == "error"
            )
            and not any(
                cast(
                    str,
                    cast(
                        dict[str, object],
                        cast(dict[str, object], event.get("params", {})).get("entry", {}),
                    ).get("url", ""),
                ).endswith(suffix)
                for suffix in allowed_error_suffixes
            )
        )
    ]
    assert severe == [], json.dumps(severe, sort_keys=True)


def test_repository_ui_full_route_keyboard_reload_and_process_restart(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(tmp_path, fail_grounding_once=True)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    with _serve(application=app) as url, _real_browser(url) as browser:
        browser.wait_for("Boolean(document.querySelector('[data-study-setup]'))")
        browser.evaluate("document.querySelector('[data-route=\"sessione\"]').click()")
        browser.wait_for("Boolean(document.querySelector('#session-entry-text:not([disabled])'))")
        browser.evaluate("document.querySelector('#session-entry-text').focus()")
        assert browser.evaluate("document.activeElement.id") == "session-entry-text"

        browser.call("Input.insertText", text="Read Valve notes")
        _press(browser, "Enter", 13)
        browser.wait_for(
            "!document.querySelector('[data-optimistic-turn]')"
            " && document.querySelector('#global-alert-title').textContent"
            " === 'Messaggio salvato, risposta non completata'"
        )
        assert len(model.requests) == 1
        assert browser.evaluate(
            "document.querySelectorAll('.thread-message--learner').length"
        ) == 1
        assert browser.evaluate(
            "document.querySelectorAll('.thread-message--assistant').length"
        ) == 0
        assert (
            browser.evaluate("document.querySelector('#view-root').dataset.scrollOwner")
            == "conversation"
        )
        assert "Trascrizione compatta" not in cast(
            str, browser.evaluate("document.querySelector('#view-root').innerText")
        )
        assert set(
            cast(
                list[str],
                browser.evaluate(
                    "Array.from(document.querySelectorAll('#global-alert-actions button'))"
                    ".map(button => button.textContent)"
                ),
            )
        ) == {"Riprova", "Apri trace"}

        browser.evaluate(
            "Array.from(document.querySelectorAll('#global-alert-actions button'))"
            ".find(button => button.textContent === 'Riprova').click()"
        )
        browser.wait_for(
            "!document.querySelector('[data-optimistic-turn]')"
            " && document.querySelectorAll('.thread-message--assistant').length === 1"
        )
        assert len(model.requests) == 2
        assert browser.evaluate(
            "document.querySelectorAll('.thread-message--learner').length"
        ) == 1
        assert browser.evaluate("document.activeElement.id") == "session-entry-text"
        browser.evaluate("document.querySelector('[data-open-turn-trace]').click()")
        browser.wait_for(
            "document.querySelector('[data-route=impostazioni].is-active')"
            " && Boolean(document.querySelector('[data-turn-trace][data-highlighted=true]'))"
        )
        trace_text = cast(
            str,
            browser.evaluate(
                "document.querySelector('[data-turn-trace][data-highlighted=true]').innerText"
            ),
        )
        assert "start_capability" in trace_text
        for excluded in (
            "model.grounding",
            "timeout",
            "ui.retry",
            "completed",
            "persistito: sì",
        ):
            assert excluded not in trace_text
        for excluded in (
            "Read Valve notes",
            "The aortic valve has three cusps",
            "browser fixture timeout",
        ):
            assert excluded not in trace_text
        browser.evaluate("document.querySelector('[data-route=sessione]').click()")
        browser.wait_for("Boolean(document.querySelector('#session-entry-text'))")

        browser.call("Page.reload", ignoreCache=True)
        browser.wait_for("Boolean(document.querySelector('#entry:not([disabled])'))")
        browser.evaluate("document.querySelector('[data-route=\"sessione\"]').click()")
        browser.wait_for(
            "document.querySelectorAll('.thread-message--assistant').length === 1"
        )
        assert "three cusps" in cast(
            str, browser.evaluate("document.querySelector('#view-root').innerText")
        )

        for route in ROUTES:
            browser.evaluate(
                f"document.querySelector('[data-route={json.dumps(route)}]').click()"
            )
            browser.wait_for(
                f"document.querySelector('[data-route={json.dumps(route)}].is-active')"
                " && !document.querySelector('.loading-state')"
            )
            assert browser.evaluate(
                "!Boolean(document.querySelector("
                f"'[data-route={json.dumps(route)}]'"
                ").disabled)"
            ) is True

        browser.evaluate("document.querySelector('#trust-mini').click()")
        browser.wait_for("document.querySelector('#trust-drawer').open")
        _press(browser, "Escape", 27)
        browser.wait_for("!document.querySelector('#trust-drawer').open")

        browser.call(
            "Emulation.setDeviceMetricsOverride",
            width=390,
            height=844,
            deviceScaleFactor=2,
            mobile=True,
        )
        browser.call(
            "Emulation.setEmulatedMedia",
            features=[{"name": "prefers-reduced-motion", "value": "reduce"}],
        )
        browser.call("Page.reload", ignoreCache=True)
        browser.wait_for("Boolean(document.querySelector('#entry:not([disabled])'))")
        assert browser.evaluate(
            "matchMedia('(prefers-reduced-motion: reduce)').matches"
        ) is True
        browser.evaluate("document.querySelector('#rail-toggle').click()")
        browser.wait_for("document.querySelector('#rail').classList.contains('is-open')")
        _press(browser, "Escape", 27)
        browser.wait_for("!document.querySelector('#rail').classList.contains('is-open')")
        assert browser.evaluate("document.activeElement.id") == "rail-toggle"
        assert cast(
            int,
            browser.evaluate(
                "Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)"
                " - innerWidth"
            ),
        ) <= 1
        screenshot = browser.call("Page.captureScreenshot", format="png")
        png = base64.b64decode(cast(str, screenshot["data"]))
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        _assert_no_browser_errors(
            browser, allowed_error_suffixes=("/api/v1/session/turns",)
        )

    restarted = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    with _serve(application=restarted) as restarted_url, _real_browser(
        restarted_url
    ) as browser:
        browser.wait_for("Boolean(document.querySelector('#entry:not([disabled])'))")
        browser.evaluate("document.querySelector('[data-route=\"sessione\"]').click()")
        browser.wait_for(
            "document.querySelectorAll('.thread-message--assistant').length === 1"
        )
        assert "three cusps" in cast(
            str, browser.evaluate("document.querySelector('#view-root').innerText")
        )
        assert len(model.requests) == 2
        _assert_no_browser_errors(browser)


def test_repository_browser_retry_binds_original_request_across_interleaved_turns(
    tmp_path: Path,
) -> None:
    """A detached retry action retains its original immutable request identity.

    The first two envelopes model transient provider failures from two
    interleaved submissions.  Clicking the detached retry action from the
    first submission must resend its original request id, even after the
    second submission replaced the shell's current command.
    """

    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    with _serve(application=app) as url, _real_browser(url) as browser:
        browser.wait_for("Boolean(document.querySelector('[data-study-setup]'))")
        browser.evaluate("document.querySelector('[data-route=\"sessione\"]').click()")
        browser.wait_for("Boolean(document.querySelector('#session-entry-text:not([disabled])'))")
        browser.evaluate("document.querySelector('#session-entry-text').focus()")
        browser.evaluate(
            "(async()=>{"
            "const original=window.fetch;"
            "window.__terminalCanonical=await original('/api/v1/session')"
            ".then(response=>response.json());"
            "window.__terminalRequests=[];"
            "window.fetch=(input,options={})=>{"
            "if(!String(input).endsWith('/api/v1/session/turns')) return original(input,options);"
            "const body=JSON.parse(options.body);"
            "window.__terminalRequests.push(body.request_id);"
            "if(window.__terminalRequests.length<=2) return Promise.resolve(new Response("
            "JSON.stringify({code:'tutor_timeout',command_committed:true,request_id:body.request_id,trace_id:'trace-transient'}),"
            "{status:504,headers:{'Content-Type':'application/json'}}));"
            "const base=window.__terminalCanonical;"
            "const terminal={...base,status:'active',shell_status:'ready',"
            "learner_entry:'first learner',timeline:["
            "{role:'assistant',content:'Non sono riuscito a completare questa risposta.',"
            "course_sequence:2}"
            "]};"
            "return Promise.resolve(new Response(JSON.stringify({"
            "schema_version:1,request_id:body.request_id,status:'failed',high_water_sequence:base.high_water_sequence,"
            "result:terminal"
            "}),{status:200,headers:{'Content-Type':'application/json'}}));"
            "};"
            "})()",
            await_promise=True,
        )

        browser.call("Input.insertText", text="first learner")
        _press(browser, "Enter", 13)
        browser.wait_for("window.__terminalRequests.length===1")
        browser.wait_for(
            "Array.from(document.querySelectorAll('#global-alert-actions button'))"
            ".some(button=>button.textContent==='Riprova')"
        )
        browser.evaluate(
            "window.__firstRetry=document.querySelector("
            "'#global-alert-actions button:first-child')"
        )
        first_request = browser.evaluate("window.__terminalRequests[0]")

        browser.call("Input.insertText", text="second learner")
        _press(browser, "Enter", 13)
        browser.wait_for(
            "window.__terminalRequests.length===2"
            " && Array.from(document.querySelectorAll('#global-alert-actions button'))"
            ".some(button=>button.textContent==='Riprova')"
        )

        browser.evaluate("window.__firstRetry.click()")
        browser.wait_for("window.__terminalRequests.length===3")
        assert browser.evaluate("window.__terminalRequests[2]") == first_request


@pytest.mark.parametrize("failure_reason", ("authentication", "protocol_error", None))
def test_repository_browser_terminal_fallback_gets_new_request_id(
    tmp_path: Path,
    failure_reason: str | None,
) -> None:
    """A settled HTTP-200 fallback cannot turn a later submission into a retry."""

    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    with _serve(application=app) as url, _real_browser(url) as browser:
        browser.wait_for("Boolean(document.querySelector('[data-study-setup]'))")
        browser.evaluate("document.querySelector('[data-route=\"sessione\"]').click()")
        browser.wait_for("Boolean(document.querySelector('#session-entry-text:not([disabled])'))")
        browser.evaluate("document.querySelector('#session-entry-text').focus()")
        browser.evaluate(
            "(async()=>{"
            "const original=window.fetch;"
            "window.__terminalCanonical=await original('/api/v1/session')"
            ".then(response=>response.json());"
            "window.__terminalRequests=[];"
            "window.fetch=(input,options={})=>{"
            "if(!String(input).endsWith('/api/v1/session/turns')) return original(input,options);"
            "const body=JSON.parse(options.body);"
            "window.__terminalRequests.push(body.request_id);"
            "const base=window.__terminalCanonical;"
            "const terminal={...base,status:'active',shell_status:'ready',"
            "learner_entry:'same learner',timeline:["
            "{role:'assistant',content:'Non sono riuscito a completare questa risposta.',"
            "course_sequence:2}]};"
            "return Promise.resolve(new Response(JSON.stringify({"
            "schema_version:1,request_id:body.request_id,status:'failed',"
            f"failure_reason:{json.dumps(failure_reason)},"
            "high_water_sequence:base.high_water_sequence,result:terminal"
            "}),{status:200,headers:{'Content-Type':'application/json'}}));"
            "};"
            "})()",
            await_promise=True,
        )

        for _attempt in range(2):
            browser.call("Input.insertText", text="same learner")
            _press(browser, "Enter", 13)
            browser.wait_for(f"window.__terminalRequests.length === {_attempt + 1}")
            browser.wait_for("!document.querySelector('[data-optimistic-turn]')")
            assert browser.evaluate(
                "document.querySelectorAll('#global-alert-actions button').length"
            ) == 0

        assert browser.evaluate("window.__terminalRequests[0]") != browser.evaluate(
            "window.__terminalRequests[1]"
        )


def test_repository_browser_source_first_setup_uploads_a_text_source(tmp_path: Path) -> None:
    root, adapters, _model = _repository(tmp_path, with_source=False)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    with _serve(application=app) as url, _real_browser(url) as browser:
        browser.wait_for("Boolean(document.querySelector('[data-source-upload]'))")
        browser.evaluate(
            "document.querySelector('[data-source-upload] textarea').value="
            "'# Emodinamica\\n\\nLa gittata cardiaca contribuisce alla pressione arteriosa.';"
            "document.querySelector('[data-source-upload] input[name=title]').value='Emodinamica';"
            "document.querySelector('[data-source-upload]')"
            ".dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))"
        )
        browser.wait_for("Boolean(document.querySelector('[data-study-setup]'))")
        view_text = cast(str, browser.evaluate("document.querySelector('#view-root').innerText"))
        assert "Emodinamica" in view_text
        _assert_no_browser_errors(browser)


def test_browser_has_no_stateless_demo_routes(tmp_path: Path) -> None:
    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    with _serve(application=app) as url, _real_browser(url) as browser:
        statuses = browser.evaluate(
            "(async () => JSON.stringify(await Promise.all("
            "['/api/state','/api/entry'].map(async path => ({"
            "path,status:(await fetch(path,{method:'POST'})).status})))))()",
            await_promise=True,
        )
        assert '\"status\":404' in cast(str, statuses)
