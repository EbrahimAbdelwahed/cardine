"""Browser surface for the conversation-first product shell.

The browser surface is intentionally a very small composition layer.  It
serves a packaged, dependency-free HTML page and delegates the deterministic
journey to :func:`study_agent.demo.product_shell.run_offline_shell_demo`.
The HTTP server owns only the latest bounded input for the page; it does not
own tutor state, persistence, capability execution, or provider credentials.

Run ``study-agent-shell-web`` for the localhost compatibility surface.  The
explicit ``--public-demo`` mode may bind all interfaces, but exposes only the
stateless, sanitized versioned demo API.  Neither mode makes a model call.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from socket import socket
from threading import BoundedSemaphore
from typing import cast
from urllib.parse import urlsplit

from study_agent.domain._validation import JsonObject

from .product_shell import MAX_LEARNER_ENTRY_CHARS, run_offline_shell_demo
from .ui_application import DemoUiApplication, UiRequestError

DEFAULT_BROWSER_HOST = "127.0.0.1"
DEFAULT_BROWSER_PORT = 8765
STATE_PATH = "/api/state"
ENTRY_PATH = "/api/entry"
HEALTH_PATH = "/health"
API_PREFIX = "/api/v1/"
MAX_API_BODY_BYTES = 32_768
MAX_CONCURRENT_REQUESTS = 32
REQUEST_SOCKET_TIMEOUT_SECONDS = 10.0

BrowserJourney = Callable[[str], Mapping[str, object]]
SocketRequest = socket | tuple[bytes, socket]


class BrowserSurface:
    """Adapt one product-shell journey to a stable browser JSON payload."""

    def __init__(self, journey: BrowserJourney = run_offline_shell_demo) -> None:
        self._journey = journey
        self._ui = DemoUiApplication(journey)

    def state(self, learner_entry: str) -> JsonObject:
        """Return the presentation payload for one bounded learner entry."""

        entry = _bounded_entry(learner_entry)
        result = self._journey(entry)
        if not isinstance(result, Mapping):
            raise TypeError("product-shell journey must return a mapping")
        return _browser_payload(result, entry)

    def page(self) -> bytes:
        """Return the packaged page bytes without filesystem or network access."""

        return resources.files("study_agent.demo").joinpath("browser.html").read_bytes()

    def asset(self, name: str) -> bytes:
        """Return one allowlisted packaged browser asset."""

        if name not in {"browser.css", "browser.js"}:
            raise ValueError("unknown browser asset")
        return resources.files("study_agent.demo").joinpath(name).read_bytes()

    def api_get(self, path: str) -> JsonObject:
        """Delegate one versioned read without giving transport code authority."""

        return self._ui.get(path)

    def api_post(self, path: str, command: Mapping[str, object]) -> JsonObject:
        """Delegate one versioned command without retaining canonical state."""

        return self._ui.post(path, command)


class _BrowserServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = MAX_CONCURRENT_REQUESTS

    def __init__(
        self,
        address: tuple[str, int],
        surface: BrowserSurface,
        *,
        public_demo: bool,
    ) -> None:
        super().__init__(address, _BrowserRequestHandler)
        self.surface = surface
        self.public_demo = public_demo
        self._request_slots = BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        self.learner_entry = "I have ten minutes. Help me understand heart valves."

    def get_request(self) -> tuple[socket, tuple[str, int]]:
        request, address = super().get_request()
        request.settimeout(REQUEST_SOCKET_TIMEOUT_SECONDS)
        return request, cast(tuple[str, int], address)

    def process_request(
        self,
        request: SocketRequest,
        client_address: tuple[str, int],
    ) -> None:
        if not self._request_slots.acquire(blocking=False):
            (request[1] if isinstance(request, tuple) else request).close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(
        self,
        request: SocketRequest,
        client_address: tuple[str, int],
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class _BrowserRequestHandler(BaseHTTPRequestHandler):
    server: _BrowserServer

    # The browser is a local reference surface.  Suppress request logging so a
    # learner's free-form text is not copied to a terminal log by default.
    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", self.server.surface.page())
            return
        if path == "/browser.css":
            self._send(
                HTTPStatus.OK,
                "text/css; charset=utf-8",
                self.server.surface.asset("browser.css"),
            )
            return
        if path == "/browser.js":
            self._send(
                HTTPStatus.OK,
                "text/javascript; charset=utf-8",
                self.server.surface.asset("browser.js"),
            )
            return
        if path == HEALTH_PATH:
            mode = "public_demo" if self.server.public_demo else "offline"
            self._send_json(HTTPStatus.OK, {"status": "ok", "mode": mode})
            return
        if path == STATE_PATH:
            if self.server.public_demo:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self._send_state()
            return
        if path.startswith(API_PREFIX):
            try:
                payload = self.server.surface.api_get(path)
            except UiRequestError as error:
                self._send_json(HTTPStatus(error.status_code), {"error": str(error)})
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith(API_PREFIX):
            self._post_api(path)
            return
        if self.server.public_demo:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if path != ENTRY_PATH:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        content_length = self.headers.get("Content-Length")
        try:
            length = int(content_length or "-1")
        except ValueError:
            length = -1
        if length < 0 or length > MAX_LEARNER_ENTRY_CHARS * 4:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "request body is too large"})
            return
        try:
            raw = json.loads(self.rfile.read(length))
            if not isinstance(raw, Mapping) or set(raw) != {"learner_entry"}:
                raise ValueError
            entry = raw["learner_entry"]
            if not isinstance(entry, str):
                raise ValueError
            payload = self.server.surface.state(entry)
        except (
            UnicodeDecodeError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "learner_entry is invalid"})
            return
        self.server.learner_entry = cast(str, payload["learner_entry"])
        self._send_json(HTTPStatus.OK, payload)

    def _post_api(self, path: str) -> None:
        content_length = self.headers.get("Content-Length")
        try:
            length = int(content_length or "-1")
        except ValueError:
            length = -1
        if length < 0 or length > MAX_API_BODY_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "request body is too large"})
            return
        try:
            raw = json.loads(self.rfile.read(length))
            if not isinstance(raw, Mapping):
                raise UiRequestError("command must be an object")
            payload = self.server.surface.api_post(path, raw)
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "request JSON is invalid"})
            return
        except UiRequestError as error:
            self._send_json(HTTPStatus(error.status_code), {"error": str(error)})
            return
        self._send_json(HTTPStatus.OK, payload)

    def _send_state(self) -> None:
        try:
            payload = self.server.surface.state(self.server.learner_entry)
        except (TypeError, ValueError):
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "state unavailable"})
            return
        self._send_json(HTTPStatus.OK, payload)

    def _send_json(self, status: HTTPStatus, payload: JsonObject) -> None:
        try:
            body = _json_bytes(payload)
        except (TypeError, ValueError, UnicodeEncodeError):
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            body = b'{"error":"response unavailable"}'
        self._send(status, "application/json; charset=utf-8", body)

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'; object-src 'none'",
        )
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        self.end_headers()
        self.wfile.write(body)


def create_server(
    host: str = DEFAULT_BROWSER_HOST,
    port: int = DEFAULT_BROWSER_PORT,
    *,
    journey: BrowserJourney = run_offline_shell_demo,
    public_demo: bool = False,
) -> ThreadingHTTPServer:
    """Create a local server or an explicitly stateless public demo server."""

    _require_bind_host(host, public_demo=public_demo)
    if public_demo and journey is not run_offline_shell_demo:
        raise ValueError("public-demo mode requires the fixed sanitized journey")
    if type(port) is not int or not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    return _BrowserServer(
        (host, port),
        BrowserSurface(journey),
        public_demo=public_demo,
    )


def serve(
    host: str = DEFAULT_BROWSER_HOST,
    port: int = DEFAULT_BROWSER_PORT,
    *,
    journey: BrowserJourney = run_offline_shell_demo,
    public_demo: bool = False,
) -> None:
    """Serve the browser surface until interrupted."""

    server = create_server(host, port, journey=journey, public_demo=public_demo)
    bound_host, bound_port = cast(tuple[str, int], server.server_address)
    print(f"Study Agent product shell: http://{bound_host}:{bound_port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="study-agent-shell-web",
        description="Serve Cardine locally or as a stateless sanitized public demo.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_BROWSER_HOST,
        help="bind address; 0.0.0.0 requires --public-demo",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_BROWSER_PORT)
    parser.add_argument(
        "--public-demo",
        action="store_true",
        help="disable legacy mutable routes and permit a public bind",
    )
    args = parser.parse_args()
    try:
        serve(args.host, args.port, public_demo=args.public_demo)
    except ValueError as error:
        parser.error(str(error))


def _browser_payload(result: Mapping[str, object], learner_entry: str) -> JsonObject:
    """Project the existing shell result; no tutor behavior is implemented here."""

    material = _mapping(result.get("material")) or _mapping(result.get("source_state"))
    context = _mapping(result.get("context_state"))
    timeline = _sequence_of_mappings(result.get("status_trace"))
    conflict = _mapping(result.get("conflict"))
    if conflict is None:
        conflict = {
            "status": "clear",
            "items": (),
            "message": "No context conflict reported by this snapshot.",
        }
    due_review = _mapping(result.get("due_review"))
    if due_review is None:
        due_review = {
            "status": "unavailable",
            "items": (),
            "message": "Optional recall capability is not installed; continuing safely.",
        }
    return cast(
        JsonObject,
        {
            "surface": "study-agent-product-shell",
            "mode": "offline",
            "learner_entry": learner_entry,
            "status": str(result.get("status", "degraded")),
            "conversation": {"status_trace": timeline},
            "material": material or {"fixture": "unavailable", "evidence": ()},
            "evidence": {
                "sequence": result.get(
                    "evidence_sequence", result.get("evidence_refresh_sequence")
                ),
                "context": context or {},
            },
            "conflict": conflict,
            "due_review": due_review,
            "capabilities": tuple(_strings(result.get("capabilities"))),
            "parity": result.get("parity") is True,
            "offline_proof": "No network, credentials, model SDK, or provider call.",
        },
    )


def _bounded_entry(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("learner_entry must be a string")
    entry = value.strip()
    if not entry:
        raise ValueError("learner_entry must be non-empty")
    if len(entry) > MAX_LEARNER_ENTRY_CHARS:
        raise ValueError("learner_entry exceeds the shell text bound")
    return entry


def _mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _sequence_of_mappings(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    return tuple(item for item in (_mapping(candidate) for candidate in value) if item is not None)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _json_bytes(payload: JsonObject) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=list,
    ).encode("utf-8")


def _require_bind_host(host: str, *, public_demo: bool) -> None:
    if host in {"127.0.0.1", "localhost"}:
        return
    if host == "0.0.0.0":
        if public_demo:
            return
        raise ValueError("0.0.0.0 requires explicit --public-demo mode")
    raise ValueError("bind host must be localhost or 0.0.0.0 in public-demo mode")


__all__ = [
    "API_PREFIX",
    "DEFAULT_BROWSER_HOST",
    "DEFAULT_BROWSER_PORT",
    "ENTRY_PATH",
    "HEALTH_PATH",
    "MAX_API_BODY_BYTES",
    "STATE_PATH",
    "BrowserJourney",
    "BrowserSurface",
    "create_server",
    "main",
    "serve",
]


if __name__ == "__main__":
    main()
