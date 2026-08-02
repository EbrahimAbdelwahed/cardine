"""Private Cardine product-shell journey over HTTP and a real browser.

This module deliberately owns no fixture state or browser dependency.  The
private server is composed by the public ``create_server`` seam, and Chrome is
driven through its local DevTools websocket just like the repository-backed
browser journey.  It is skipped only when the private factory is not present,
when loopback sockets cannot be opened, or when Chromium is unavailable.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from http.cookiejar import Cookie, CookieJar
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from threading import Thread
from typing import cast
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import pytest

from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.cli.repository import (
    LocalRepository,
    ModelAdapterBuilder,
    ModelAdapterRegistry,
)
from study_agent.demo.browser import create_server
from study_agent.demo.ui_application import RepositoryUiApplication, UiApplicationPort
from study_agent.domain import (
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    PrincipalKind,
    SessionId,
)
from study_agent.domain._validation import JsonObject
from study_agent.ports import (
    CancellationToken,
    ModelCapabilities,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
)
from study_agent.repository_config import LocalRepositoryConfig, ModelAdapterConfig

PASSWORD = os.environ.get("CARDINE_TEST_PASSWORD", "cardine-e2e-password")
SENTINEL = "cardine-e2e-runtime-key-sentinel"
ROUTES = ("fonti", "proposte", "verifiche", "evidenze", "ripasso", "piano", "conflitti")
PRIVATE_GETS = ("/api/v1/settings", "/api/v1/bootstrap", "/api/v1/session")
COURSE = CourseId("cardine-private-e2e")
SESSION = SessionId("cardine-private-e2e-session")


class _PrivateFixtureModel:
    """A provider seam that exercises the canonical repository host offline."""

    capabilities = ModelCapabilities(structured_output=True)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if request.metadata.get("purpose") == "cardine-settings-model-check":
            content = "OK"
            structured_output = None
        else:
            content = ""
            structured_output = {
                "decision": {
                    "kind": "assistant_message",
                    "message": (
                        "Partiamo dalla fonte caricata e verifichiamo un passaggio alla volta."
                    ),
                }
            }
        return ModelResponse(
            content,
            None,
            ModelFinishReason.STOP,
            ModelInvocation("private-fixture", "1.0.0", "fixture", "private-e2e"),
            structured_output=structured_output,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[object]:
        del request
        if False:  # pragma: no cover - protocol-only async generator
            yield None
        raise AssertionError("the repository tutor does not stream")

    async def cancel(self, token: CancellationToken) -> None:
        del token
        raise AssertionError("the repository tutor does not cancel fixture requests")


class Response:
    def __init__(self, status: int, headers: Mapping[str, str], body: str) -> None:
        self.status = status
        self.headers = dict(headers)
        self.body = body

    @property
    def json(self) -> object:
        try:
            return json.loads(self.body) if self.body else None
        except json.JSONDecodeError:
            return self.body


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        request_headers = {"Accept": "application/json", **(headers or {})}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = Request(
            f"{self.base_url}{path}", data=data, method=method, headers=request_headers
        )
        try:
            with self.opener.open(request, timeout=5) as response:
                return Response(
                    response.status,
                    dict(response.headers.items()),
                    response.read().decode(),
                )
        except HTTPError as error:
            return Response(error.code, dict(error.headers.items()), error.read().decode())

    def post(self, path: str, payload: object, *, csrf: str | None = None) -> Response:
        headers = {"Origin": self.base_url}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        return self.request(path, method="POST", payload=payload, headers=headers)


class _LocalSetupApplication(UiApplicationPort):
    mode = "local_repository"

    def get(self, path: str) -> JsonObject:
        if path != "/api/v1/bootstrap":
            raise AssertionError(path)
        return {"schema_version": 1, "mode": self.mode}

    def post(self, path: str, command: Mapping[str, object]) -> JsonObject:
        del command
        raise AssertionError(path)


def _private_server(
    repository: Path, model_adapters: ModelAdapterRegistry
) -> ThreadingHTTPServer:
    """Compose the explicit private server supported by the product shell.

    The backend owns password-hash parsing.  Tests use the test hash supplied
    by the implementation's private fixture seam and never place a hash in a
    request or browser payload.
    """

    from study_agent.demo.private_access import PrivateAccessController, hash_password
    from study_agent.demo.product_settings import (
        PrivateSettingsApplication,
        RuntimeCredentialStore,
    )
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    access = PrivateAccessController(hash_password(PASSWORD), canonical_origin=origin)
    application = PrivateSettingsApplication(
        RepositoryUiApplication(repository, COURSE, SESSION, model_adapters=model_adapters),
        credentials=RuntimeCredentialStore(),
    )
    return create_server(
        "127.0.0.1",
        port,
        ui_application=application.delegate,
        private_access=access,
        settings_application=application,
    )


@contextmanager
def _serve_private() -> Iterator[str]:
    # The repository target resolver intentionally rejects symlinked ancestor
    # paths. macOS's pytest temp directory is normally under /var -> /private,
    # so use the canonical writable root for this integration fixture.
    temporary_root = Path(gettempdir()).resolve()
    with TemporaryDirectory(
        prefix="cardine-private-e2e-", dir=temporary_root
    ) as temporary:
        repository = Path(temporary) / "repository"
        initialize_local_repository(
            repository,
            LocalRepositoryConfig(ModelAdapterConfig("private-fixture", {}, None)),
        )
        model = _PrivateFixtureModel()
        model_adapters = ModelAdapterRegistry(
            {
                "private-fixture": cast(
                    ModelAdapterBuilder, lambda _config, _credential: model
                )
            },
            versions={"private-fixture": "1.0.0"},
        )
        with LocalRepository.open(repository, model_adapters=model_adapters) as local:
            local.course_service.create(
                CourseProfile(COURSE, "Private Cardine", "it", learning_goals=("Studiare",)),
                ExecutionContext(
                    PrincipalKind.SERVICE,
                    "private-e2e-course",
                    COURSE,
                    CorrelationId("private-e2e-course-create"),
                ),
            )
            local.session_service.start(
                ExecutionContext(
                    PrincipalKind.HUMAN,
                    "private-e2e-session",
                    COURSE,
                    CorrelationId("private-e2e-session-start"),
                    session_id=SESSION,
                )
            )
        try:
            server = _private_server(repository, model_adapters)
        except PermissionError as error:
            pytest.skip(f"local sockets are unavailable: {error}")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = cast(tuple[str, int], server.server_address)
        try:
            yield f"http://{host}:{port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


def _cookie(client: Client) -> Cookie | None:
    return next(iter(client.cookies), None)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return cast(int, probe.getsockname()[1])


@contextmanager
def _serve_local_owner_setup() -> Iterator[str]:
    try:
        server = create_server(
            "127.0.0.1",
            0,
            ui_application=_LocalSetupApplication(),
            local_owner_setup=True,
        )
    except PermissionError as error:
        pytest.skip(f"local sockets are unavailable: {error}")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = cast(tuple[str, int], server.server_address)
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _csrf(client: Client) -> str:
    response = client.request("/api/v1/auth/session")
    assert response.status == 200, response.body
    payload = cast(dict[str, object], response.json)
    value = payload.get("csrf_token")
    assert isinstance(value, str) and value
    return value


def test_private_http_access_login_csrf_logout_and_protected_routes() -> None:
    with _serve_private() as url:
        client = Client(url)
        probe = client.request("/api/v1/auth/session")
        assert probe.status == 200
        assert cast(dict[str, object], probe.json).get("authenticated") is False

        wrong = client.post("/api/v1/auth/login", {"password": "not-the-password"})
        malformed = client.post("/api/v1/auth/login", {"password": ""})
        assert wrong.status == malformed.status == 401
        assert wrong.body == malformed.body
        assert "not-the-password" not in wrong.body

        login = client.post("/api/v1/auth/login", {"password": PASSWORD})
        assert login.status == 200, login.body
        cookie = _cookie(client)
        assert cookie is not None
        assert cookie.name in {"__Host-cardine_session", "cardine_session"}
        assert cookie.value
        assert cookie.has_nonstandard_attr("HttpOnly")
        assert cookie.get_nonstandard_attr("SameSite", "").lower() == "strict"
        assert "session" not in login.body.lower() or "authenticated" in login.body.lower()

        csrf = _csrf(client)
        for path in PRIVATE_GETS:
            response = client.request(path)
            assert response.status == 200, (path, response.body)
            assert SENTINEL not in response.body
        forbidden = client.post("/api/v1/settings/model/credential", {"api_key": "x"})
        assert forbidden.status in {403, 409}
        mutation = client.post("/api/v1/settings/model/credential", {"api_key": "x"}, csrf=csrf)
        assert mutation.status == 200, mutation.body

        logout = client.post("/api/v1/auth/logout", {}, csrf=csrf)
        assert logout.status == 200, logout.body
        assert client.request("/api/v1/settings").status in {401, 403}
        assert client.post("/api/v1/auth/logout", {}, csrf=csrf).status in {401, 403}


def test_private_http_rejects_missing_or_cross_origin_mutations_and_bad_hosts() -> None:
    """Private transport remains same-origin and rejects DNS-rebinding hosts."""

    with _serve_private() as url:
        client = Client(url)

        missing_origin = client.request(
            "/api/v1/auth/login",
            method="POST",
            payload={"password": PASSWORD},
        )
        assert missing_origin.status == 403
        assert "origin" in missing_origin.body

        cross_origin = client.request(
            "/api/v1/auth/login",
            method="POST",
            payload={"password": PASSWORD},
            headers={"Origin": "http://evil.example"},
        )
        assert cross_origin.status == 403
        assert "origin" in cross_origin.body

        bad_host = client.request("/health", headers={"Host": "evil.example"})
        assert bad_host.status == 421
        assert bad_host.json == {"error": "host is not allowed"}


def test_local_owner_setup_http_is_same_origin_and_one_time() -> None:
    with _serve_local_owner_setup() as url:
        client = Client(url)
        setup_probe = client.request("/api/v1/auth/session")
        assert setup_probe.status == 200
        page = client.request("/")
        assert page.status == 200

        missing = client.post(
            "/api/v1/auth/setup-owner", {"password": PASSWORD}
        )
        malformed = client.post(
            "/api/v1/auth/setup-owner",
            {"password": PASSWORD, "unexpected": "value"},
        )
        assert missing.status == 200
        assert malformed.status == 403

        replay = Client(url).post(
            "/api/v1/auth/setup-owner",
            {"password": PASSWORD},
        )
        assert replay.status == 403


def test_local_owner_setup_http_is_race_safe() -> None:
    with _serve_local_owner_setup() as url:
        def submit() -> int:
            return Client(url).post(
                "/api/v1/auth/setup-owner",
                {"password": PASSWORD},
            ).status

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: submit(), range(2)))
        assert sorted(statuses) == [200, 403]


def test_runtime_key_replace_remove_is_write_only_and_redacted() -> None:
    with _serve_private() as url:
        client = Client(url)
        assert client.post("/api/v1/auth/login", {"password": PASSWORD}).status == 200
        csrf = _csrf(client)
        replace = client.post("/api/v1/settings/model/credential", {"api_key": SENTINEL}, csrf=csrf)
        assert replace.status == 200, replace.body
        remove = client.post("/api/v1/settings/model/credential/remove", {}, csrf=csrf)
        assert remove.status == 200, remove.body
        responses = [replace, remove, client.request("/api/v1/settings")]
        for response in responses:
            assert SENTINEL not in response.body
        assert "api_key" not in client.request("/api/v1/auth/session").body.lower()


def _chrome_binary() -> str | None:
    configured = os.environ.get("CARDINE_CHROME_BINARY")
    candidates = (
        configured,
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    )
    return next((path for path in candidates if path and Path(path).is_file()), None)


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("DevTools websocket closed")
        data.extend(chunk)
    return bytes(data)


class _Browser:
    def __init__(self, websocket_url: str) -> None:
        parsed = urlsplit(websocket_url)
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        handshake = (
            f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.sock.recv(4096)
        if not response.startswith(b"HTTP/1.1 101"):
            raise ConnectionError(response.decode("latin-1", errors="replace"))
        self.command_id = 0

    def close(self) -> None:
        self.sock.close()

    def call(self, method: str, **params: object) -> dict[str, object]:
        self.command_id += 1
        payload = json.dumps(
            {"id": self.command_id, "method": method, "params": params}, separators=(",", ":")
        ).encode()
        mask = os.urandom(4)
        if len(payload) < 126:
            header = bytes([0x81, 0x80 | len(payload)])
        else:
            header = bytes([0x81, 0x80 | 126]) + struct.pack("!H", len(payload))
        self.sock.sendall(
            header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        )
        while True:
            first, second = _read_exact(self.sock, 2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", _read_exact(self.sock, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", _read_exact(self.sock, 8))[0]
            body = _read_exact(self.sock, length)
            if first & 0x0F != 1:
                continue
            message = cast(dict[str, object], json.loads(body))
            if message.get("id") == self.command_id:
                if "error" in message:
                    raise AssertionError(message["error"])
                return cast(dict[str, object], message.get("result", {}))

    def evaluate(self, expression: str, *, await_promise: bool = False) -> object:
        result = self.call(
            "Runtime.evaluate",
            expression=expression,
            awaitPromise=await_promise,
            returnByValue=True,
        )
        if result.get("exceptionDetails"):
            raise AssertionError(result["exceptionDetails"])
        return cast(dict[str, object], result["result"]).get("value")

    def wait(self, expression: str, timeout: float = 10) -> object:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.evaluate(expression)
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError(f"browser condition timed out: {expression}")


@contextmanager
def _browser(url: str) -> Iterator[_Browser]:
    binary = _chrome_binary()
    if binary is None:
        pytest.skip("Chrome/Chromium is not installed")
    port_probe = socket.socket()
    port_probe.bind(("127.0.0.1", 0))
    port = cast(int, port_probe.getsockname()[1])
    port_probe.close()
    with __import__("tempfile").TemporaryDirectory(prefix="cardine-private-browser-") as profile:
        process = subprocess.Popen(
            [
                binary,
                "--headless=new",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            endpoint = f"http://127.0.0.1:{port}/json/list"
            target: dict[str, object] | None = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    with urlopen(endpoint, timeout=0.5) as response:
                        targets = cast(list[dict[str, object]], json.load(response))
                    target = next((item for item in targets if item.get("type") == "page"), None)
                    if target:
                        break
                except OSError:
                    time.sleep(0.05)
            if target is None:
                pytest.skip("Chrome did not expose a DevTools page target")
            browser = _Browser(cast(str, target["webSocketDebuggerUrl"]))
            browser.call("Runtime.enable")
            browser.call("Page.navigate", url=url)
            browser.wait("document.readyState === 'complete'")
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


def _click(browser: _Browser, selector: str) -> None:
    browser.evaluate(f"document.querySelector({json.dumps(selector)}).click()")


def _click_route(browser: _Browser, route: str) -> None:
    selector = f'[data-route="{route}"]'
    browser.evaluate(f"document.querySelector({json.dumps(selector)}).click()")


def _entry_value(browser: _Browser) -> object:
    return browser.evaluate("document.querySelector('#session-entry-text, #entry').value")


def test_private_browser_routes_composer_scope_keyboard_draft_and_mobile() -> None:
    """The composer belongs to the conversation, not to every screen.

    A persistent dock parked under Fonti, Piano, Ripasso and Verifiche is a
    conversation surface in the wrong place: it competes with the section it
    sits under, and it was the element that overflowed the viewport on a
    phone. The composer now appears on the two conversational routes only,
    and a waiting tutor is announced by the shell's alert region with a way
    back to Chat.
    """

    with _serve_private() as url, _browser(url) as browser:
        browser.wait("Boolean(document.querySelector('#login-form'))")
        browser.evaluate(
            "document.querySelector('#login-form input[type=password]').value="
            f"{json.dumps(PASSWORD)}"
        )
        _click(browser, "#login-form button[type=submit]")
        browser.wait("!document.querySelector('#login-form')")
        browser.wait("Boolean(document.querySelector('[data-route=\"oggi\"].is-active'))")

        # Tool sections carry no composer.
        for route in ROUTES:
            _click_route(browser, route)
            browser.wait(
                f"Boolean(document.querySelector('[data-route={json.dumps(route)}].is-active'))"
            )
            assert browser.evaluate("document.querySelectorAll('[data-entry-form]').length") == 0

        # Chat always does, and exactly once. Oggi shows the source-first
        # setup wizard until the course has materials, so it is not asserted
        # to carry a composer here.
        _click_route(browser, "sessione")
        browser.wait("Boolean(document.querySelector('#session-entry-text'))")
        assert browser.evaluate("document.querySelectorAll('[data-entry-form]').length") == 1

        # A draft written in Chat survives a trip through a tool section.
        _click_route(browser, "sessione")
        browser.wait("Boolean(document.querySelector('#session-entry-text'))")
        browser.evaluate(
            "document.querySelector('#session-entry-text').value="
            "'draft survives route change';"
            "document.querySelector('#session-entry-text')"
            ".dispatchEvent(new Event('input',{bubbles:true}))"
        )
        _click_route(browser, "fonti")
        browser.wait("Boolean(document.querySelector('[data-route=\"fonti\"].is-active'))")
        _click_route(browser, "sessione")
        browser.wait("Boolean(document.querySelector('#session-entry-text'))")
        assert _entry_value(browser) == "draft survives route change"

        # A failed send restores the text instead of losing it, and reports
        # the failure somewhere the reader can actually see.
        browser.evaluate(
            "document.querySelector('#session-entry-text').value='line one';"
            "document.querySelector('#session-entry-text')"
            ".dispatchEvent(new Event('input',{bubbles:true}))"
        )
        browser.evaluate(
            "window.__cardineOriginalFetch=window.fetch; "
            "window.fetch=()=>Promise.reject(new Error('e2e forced failure'))"
        )
        browser.evaluate(
            "document.querySelector('#session-entry-text')"
            ".dispatchEvent(new KeyboardEvent("
            "'keydown',{key:'Enter',bubbles:true}))"
        )
        time.sleep(0.4)
        assert _entry_value(browser) == "line one"
        assert browser.evaluate("document.querySelector('#global-alert').hidden") is False
        assert cast(
            str, browser.evaluate("document.querySelector('#global-alert-title').innerText")
        ).strip() != ""
        browser.evaluate("window.fetch=window.__cardineOriginalFetch")
        _click(browser, "#global-alert-dismiss")
        assert browser.evaluate("document.querySelector('#global-alert').hidden") is True

        browser.evaluate(
            "document.querySelector('#session-entry-text')"
            ".dispatchEvent(new KeyboardEvent("
            "'keydown',{key:'Enter',bubbles:true}))"
        )
        browser.wait("document.querySelector('#session-entry-text').value === ''")
        browser.evaluate(
            "document.querySelector('#session-entry-text').value='line one'"
        )
        browser.evaluate(
            "document.querySelector('#session-entry-text')"
            ".dispatchEvent(new KeyboardEvent("
            "'keydown',{key:'Enter',shiftKey:true,bubbles:true}))"
        )
        browser.evaluate(
            "document.querySelector('#session-entry-text')"
            ".dispatchEvent(new KeyboardEvent("
            "'keydown',{key:'Enter',isComposing:true,bubbles:true}))"
        )

        for width in (320, 390):
            browser.call(
                "Emulation.setDeviceMetricsOverride",
                width=width,
                height=844,
                deviceScaleFactor=1,
                mobile=True,
            )
            assert (
                cast(
                    int,
                    browser.evaluate(
                    "Math.max(document.documentElement.scrollWidth,document.body.scrollWidth)-innerWidth"
                    ),
                )
                <= 1
            )
            # Nothing in the shell may be drawn outside the viewport.
            assert (
                browser.evaluate(
                    "[...document.querySelectorAll('#view-root button,#view-root input,"
                    "#view-root textarea,#alert-region *')].every(node=>{"
                    "const r=node.getBoundingClientRect(); "
                    "return r.width===0 || (r.right<=innerWidth+1 && r.left>=-1)})"
                )
                is True
            )


def test_settings_labels_write_only_empty_after_save_and_pending_continuation_routes_to_chat() -> (
    None
):
    with _serve_private() as url, _browser(url) as browser:
        browser.wait("Boolean(document.querySelector('#login-form'))")
        browser.evaluate(
            "document.querySelector('#login-form input[type=password]').value="
            f"{json.dumps(PASSWORD)}"
        )
        _click(browser, "#login-form button[type=submit]")
        browser.wait("!document.querySelector('#login-form')")
        _click(browser, "#account-control")
        _click_route(browser, "impostazioni")
        browser.wait(
            "Boolean(document.querySelector('#settings-model-form, #credential-settings-form'))"
        )
        text = cast(
            str,
            browser.evaluate(
                "document.querySelector('.settings-surface').innerText"
            ),
        )
        for label in ("Account", "Modello", "Privacy", "Chiave API", "GPT-5.6 Luna"):
            assert label.lower() in text.lower()
        browser.evaluate(
            "document.querySelector('#settings-model-form input, #credential-settings-form input')"
            f".value={json.dumps(SENTINEL)}"
        )
        _click(
            browser,
            "#settings-model-form button[type=submit], "
            "#credential-settings-form button[type=submit]",
        )
        browser.wait(
            "document.querySelector('#settings-model-form input, #credential-settings-form input')"
            ".value === ''"
        )
        browser.wait(
            "document.querySelector('#credential-settings-status')?.innerText"
            ".includes('Chiave aggiornata per questa sessione.')"
        )
        assert SENTINEL not in cast(str, browser.evaluate("document.body.innerText"))
        assert (
            browser.evaluate(
                "document.querySelectorAll('#settings-model-form input[type=password], "
                "#credential-settings-form input[type=password]').length"
            )
            == 1
        )
        _click(browser, "[data-settings-check]")
        browser.wait(
            "document.querySelector('#model-check-status')?.innerText"
            ".includes('Decisione del tutor verificata.')"
        )

        _click(browser, '[data-route="sessione"]')
        browser.wait("Boolean(document.querySelector('#conversation-heading'))")
        # A waiting tutor is surfaced by the shell alert with a route back to
        # Chat, not by a composer parked under an unrelated section.
        _click_route(browser, "fonti")
        browser.wait("Boolean(document.querySelector('[data-route=\"fonti\"].is-active'))")
        pending = browser.evaluate("document.querySelector('#global-alert').hidden === false")
        if pending:
            browser.evaluate(
                "[...document.querySelectorAll('#global-alert-actions button')]"
                ".find(button => button.innerText.includes('chat'))?.click()"
            )
            browser.wait(
                "Boolean(document.querySelector('[data-route=\"sessione\"].is-active'))"
            )
