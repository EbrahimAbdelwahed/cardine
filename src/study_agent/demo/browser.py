"""Repository-backed browser surface for the conversation-first product shell.

The transport serves static assets, authentication and HTTP envelopes only.
All learner state, timelines, presentations and continuations are read from
the configured canonical repository application.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from socket import socket
from threading import BoundedSemaphore, Lock, RLock
from typing import cast
from urllib.parse import unquote, urlsplit

from study_agent.domain._validation import JsonObject

from .private_access import (
    AuthenticatedSession,
    LoginRateLimited,
    PrivateAccessController,
    PrivateAccessError,
    hash_password,
)
from .product_settings import PrivateSettingsApplication, RuntimeCredentialStore
from .ui_application import UiApplicationPort, UiRequestError

DEFAULT_BROWSER_HOST = "127.0.0.1"
DEFAULT_BROWSER_PORT = 8765
HEALTH_PATH = "/health"
# Safe, non-secret marker for distinguishing a freshly restarted preview from
# an older listener that still owns the same loopback port.
PREVIEW_RUNTIME_ID = "cardine-local-source-grounding-v2"
ICON_ASSETS = frozenset(
    {
        "book-open.svg",
        "arrow-up.svg",
        "calendar-blank.svg",
        "caret-down.svg",
        "cards.svg",
        "chart-line-up.svg",
        "chat-circle.svg",
        "exam.svg",
        "favicon.svg",
        "gear.svg",
        "note-pencil.svg",
        "magnifying-glass.svg",
        "plus.svg",
        "select-caret.svg",
        "select-caret-inverse.svg",
        "shield-check.svg",
        "sidebar-simple.svg",
        "warning-circle.svg",
    }
)
FONT_ASSETS = frozenset(
    {
        "instrument-serif-400.woff2",
        "ibm-plex-mono-400.woff2",
        "ibm-plex-mono-600.woff2",
    }
)
API_PREFIX = "/api/v1/"
DIAGNOSTICS_PATH = "/api/v1/diagnostics"
LOCAL_OWNER_SETUP_PATH = "/api/v1/auth/setup-owner"
# Source revisions are intentionally bounded by the UI application at 192 KiB.
# Leave protocol headroom for the JSON envelope while keeping generic API bodies
# small enough for the local threaded server.
MAX_API_BODY_BYTES = 262_144
MAX_CONCURRENT_REQUESTS = 32
REQUEST_SOCKET_TIMEOUT_SECONDS = 10.0
MIN_LOCAL_OWNER_PASSWORD_CHARS = 12

SocketRequest = socket | tuple[bytes, socket]


class BrowserSurface:
    """Adapt one repository application to the browser transport."""

    def __init__(
        self,
        ui_application: UiApplicationPort,
        *,
        private_access: PrivateAccessController | None = None,
        settings_application: PrivateSettingsApplication | None = None,
        runtime_credentials: RuntimeCredentialStore | None = None,
    ) -> None:
        self._ui = ui_application
        self._private_access = private_access
        self._settings = settings_application
        self._runtime_credentials = runtime_credentials
        self._local_setup_origin: str | None = None
        self._access_lock = RLock()
        self._diagnostics: deque[JsonObject] = deque(maxlen=24)
        self._diagnostics_lock = Lock()

    @property
    def repository_backed(self) -> bool:
        return getattr(self._ui, "mode", "") == "local_repository"

    @property
    def mode(self) -> str:
        if self._private_access is not None:
            return "private"
        if self._local_setup_origin is not None:
            return "setup"
        return str(getattr(self._ui, "mode", "local_repository"))

    @property
    def private_access(self) -> PrivateAccessController | None:
        return self._private_access

    @property
    def private_mode(self) -> bool:
        return self._private_access is not None

    @property
    def setup_required(self) -> bool:
        return self._private_access is None and self._local_setup_origin is not None

    def enable_local_owner_setup(self, canonical_origin: str) -> None:
        """Allow one owner to activate a loopback-only private session.

        This deliberately keeps the owner verifier in process memory.  It is
        intended for an explicit local preview: no password or verifier is
        written to the repository, logs, browser storage, or environment.
        """

        with self._access_lock:
            if self._private_access is not None or self._local_setup_origin is not None:
                raise ValueError("local owner setup is already configured")
            # ``create_server`` derives this from its bound loopback socket;
            # ``configure_local_owner`` passes it to the access controller
            # before any session or credential store is activated.
            self._local_setup_origin = canonical_origin

    def configure_local_owner(
        self, password: str, *, client_id: str
    ) -> AuthenticatedSession:
        """Turn an unconfigured loopback preview into an authenticated shell."""

        if not isinstance(password, str) or len(password) < MIN_LOCAL_OWNER_PASSWORD_CHARS:
            raise UiRequestError(
                f"choose a password of at least {MIN_LOCAL_OWNER_PASSWORD_CHARS} characters",
                status_code=400,
            )
        with self._access_lock:
            origin = self._local_setup_origin
            if origin is None:
                raise UiRequestError("owner setup is not available", status_code=404)
            access = PrivateAccessController(
                hash_password(password), canonical_origin=origin
            )
            session = access.login(password, client_id=client_id)
            self._private_access = access
            self._settings = PrivateSettingsApplication(
                self._ui,
                credentials=(
                    self._runtime_credentials
                    if self._runtime_credentials is not None
                    else RuntimeCredentialStore()
                ),
            )
            self._local_setup_origin = None
        return session

    def diagnostic(self, path: str, status_code: int, category: str) -> None:
        """Retain a small, redacted local preview diagnostic record.

        Learner text, cookies, credentials, provider bodies, and exception
        strings are deliberately excluded.  The same safe record is sent to
        stderr so the local launcher can persist it in its preview log.
        """

        if not isinstance(path, str) or not path.startswith(API_PREFIX):
            return
        if type(status_code) is not int or not 100 <= status_code <= 599:
            return
        if category not in {
            "authentication_required",
            "invalid_request",
            "model_check_invalid_credential",
            "model_check_provider_unavailable",
            "model_check_rate_limited",
            "model_check_timeout",
            "model_check_protocol_error",
            "model_check_model_unavailable",
            "model_check_endpoint_incompatible",
            "repository_runtime_unavailable",
            "source_content_unavailable",
            "stale_sequence",
            "tutor_execution_failed",
            "tutor_authentication",
            "tutor_model_unavailable",
            "tutor_endpoint_incompatible",
            "tutor_rate_limited",
            "tutor_timeout",
            "tutor_protocol_error",
            "tutor_unavailable",
            "tutor_internal_error",
        }:
            category = "invalid_request"
        entry: JsonObject = {
            "at_unix": int(time.time()),
            "path": path,
            "status_code": status_code,
            "category": category,
        }
        with self._diagnostics_lock:
            self._diagnostics.append(entry)
        print(
            "cardine_preview_diagnostic"
            f" path={path} status={status_code} category={category}",
            file=sys.stderr,
            flush=True,
        )

    def diagnostics(self) -> JsonObject:
        with self._diagnostics_lock:
            entries = tuple(dict(entry) for entry in self._diagnostics)
        return {"schema_version": 1, "entries": entries}

    def page(self) -> bytes:
        """Return the packaged page bytes without filesystem or network access."""

        return resources.files("study_agent.demo").joinpath("browser.html").read_bytes()

    def asset(self, name: str) -> bytes:
        """Return one allowlisted packaged browser asset."""

        if name in {
            "browser.css",
            "browser.js",
            "ai-primitives.css",
            "ai-primitives.js",
        }:
            return resources.files("study_agent.demo").joinpath(name).read_bytes()
        if name.startswith("icons/") and name.removeprefix("icons/") in ICON_ASSETS:
            return resources.files("study_agent.demo").joinpath(name).read_bytes()
        if name.startswith("fonts/") and name.removeprefix("fonts/") in FONT_ASSETS:
            return resources.files("study_agent.demo").joinpath(name).read_bytes()
        raise ValueError("unknown browser asset")

    def api_get(self, path: str, *, session_token: str | None = None) -> JsonObject:
        """Delegate one versioned read without giving transport code authority."""

        if (
            self._private_access is not None
            and path != "/api/v1/auth/session"
            and not self._private_access.authenticate(session_token)
        ):
            raise UiRequestError("authentication required", status_code=401)
        if path == "/api/v1/auth/session":
            if self._private_access is None:
                if self.setup_required:
                    return {
                        "schema_version": 1,
                        "mode": "setup",
                        "authenticated": False,
                        "setup_required": True,
                    }
                raise UiRequestError("route not found", status_code=404)
            session = self._private_access.session(session_token)
            return {
                "schema_version": 1,
                "mode": "private",
                "authenticated": session is not None,
                "csrf_token": None if session is None else session.csrf_token,
            }
        if path == DIAGNOSTICS_PATH:
            return self.diagnostics()
        return (self._settings or self._ui).get(path)

    def api_post(
        self,
        path: str,
        command: Mapping[str, object],
        *,
        session_token: str | None = None,
        csrf_token: str | None = None,
    ) -> JsonObject:
        """Delegate one versioned command without retaining canonical state."""

        if self._private_access is not None:
            if path == "/api/v1/auth/login":
                raise UiRequestError("login is handled by the transport", status_code=500)
            if not self._private_access.csrf_valid(session_token, csrf_token):
                raise UiRequestError("csrf token is invalid", status_code=403)
            if path == "/api/v1/auth/logout":
                self._private_access.logout(session_token)
                return {"schema_version": 1, "status": "logged_out"}
        result = (self._settings or self._ui).post(path, command)
        if path == "/api/v1/settings/model/check" and result.get("status") == "error":
            reason = result.get("reason")
            category = {
                "invalid_credential": "model_check_invalid_credential",
                "rate_limited": "model_check_rate_limited",
                "timeout": "model_check_timeout",
                "model_unavailable": "model_check_model_unavailable",
                "endpoint_incompatible": "model_check_endpoint_incompatible",
                "provider_protocol_error": "model_check_protocol_error",
            }.get(str(reason), "model_check_provider_unavailable")
            self.diagnostic(path, HTTPStatus.OK, category)
        return result


class _BrowserServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = MAX_CONCURRENT_REQUESTS

    def __init__(
        self,
        address: tuple[str, int],
        surface: BrowserSurface,
    ) -> None:
        super().__init__(address, _BrowserRequestHandler)
        self.surface = surface
        self._request_slots = BoundedSemaphore(MAX_CONCURRENT_REQUESTS)

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
    _pending_cookie: str | None = None

    # The browser is a local reference surface.  Suppress request logging so a
    # learner's free-form text is not copied to a terminal log by default.
    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:
        if not self._host_matches_server():
            self._send_json(
                HTTPStatus.MISDIRECTED_REQUEST, {"error": "host is not allowed"}
            )
            return
        path = unquote(urlsplit(self.path).path)
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
        if path == "/ai-primitives.css":
            self._send(
                HTTPStatus.OK,
                "text/css; charset=utf-8",
                self.server.surface.asset("ai-primitives.css"),
            )
            return
        if path == "/browser.js":
            self._send(
                HTTPStatus.OK,
                "text/javascript; charset=utf-8",
                self.server.surface.asset("browser.js"),
            )
            return
        if path == "/ai-primitives.js":
            self._send(
                HTTPStatus.OK,
                "text/javascript; charset=utf-8",
                self.server.surface.asset("ai-primitives.js"),
            )
            return
        if path.startswith("/icons/"):
            try:
                icon = self.server.surface.asset(path.removeprefix("/"))
            except ValueError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self._send(HTTPStatus.OK, "image/svg+xml", icon)
            return
        if path.startswith("/fonts/"):
            try:
                font = self.server.surface.asset(path.removeprefix("/"))
            except ValueError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self._send(HTTPStatus.OK, "font/woff2", font)
            return
        if path == HEALTH_PATH:
            mode = self.server.surface.mode
            self._send_json(
                HTTPStatus.OK,
                {"status": "ok", "mode": mode, "runtime_id": PREVIEW_RUNTIME_ID},
            )
            return
        if path.startswith(API_PREFIX):
            try:
                payload = self.server.surface.api_get(
                    path, session_token=self._session_token()
                )
            except UiRequestError as error:
                self.server.surface.diagnostic(
                    path, error.status_code, _diagnostic_category(error)
                )
                self._send_json(
                    HTTPStatus(error.status_code),
                    {"error": str(error), "code": error.diagnostic_code},
                )
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._host_matches_server():
            self._send_json(
                HTTPStatus.MISDIRECTED_REQUEST, {"error": "host is not allowed"}
            )
            return
        path = unquote(urlsplit(self.path).path)
        if path.startswith(API_PREFIX):
            self._post_api(path)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _post_api(self, path: str) -> None:
        if not _is_json_content_type(self.headers.get("Content-Type")):
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "Content-Type must be application/json"},
            )
            return
        private_login = self.server.surface.private_mode and path == "/api/v1/auth/login"
        local_owner_setup = (
            self.server.surface.setup_required and path == LOCAL_OWNER_SETUP_PATH
        )
        if not self._origin_matches_request(
            require_origin=self.server.surface.private_mode or local_owner_setup
        ):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
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
            if local_owner_setup:
                payload = self._setup_local_owner(cast(Mapping[str, object], raw))
            elif private_login:
                payload = self._login(cast(Mapping[str, object], raw))
            else:
                payload = self.server.surface.api_post(
                    path,
                    raw,
                    session_token=self._session_token(),
                    csrf_token=self.headers.get("X-CSRF-Token"),
                )
                if path == "/api/v1/auth/logout" and self.server.surface.private_access:
                    self._pending_cookie = self.server.surface.private_access.clear_cookie_header()
        except UiRequestError as error:
            self.server.surface.diagnostic(
                path, error.status_code, _diagnostic_category(error)
            )
            self._send_json(
                HTTPStatus(error.status_code),
                {"error": str(error), "code": error.diagnostic_code},
            )
            return
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "request JSON is invalid"})
            return
        except Exception:
            category = (
                "tutor_internal_error"
                if path == "/api/v1/session/turns"
                or path.startswith("/api/v1/session/continuations/")
                else "repository_runtime_unavailable"
            )
            self.server.surface.diagnostic(path, HTTPStatus.INTERNAL_SERVER_ERROR, category)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "request could not be completed", "code": category},
            )
            return
        self._send_json(HTTPStatus.OK, payload)

    def _origin_matches_request(self, *, require_origin: bool = False) -> bool:
        """Allow same-origin browser requests and direct clients without Origin."""

        origin = self.headers.get("Origin")
        if origin is None:
            return not require_origin
        if self.server.surface.private_access is not None:
            return self.server.surface.private_access.origin_allowed(origin)
        origin_parts = _origin_parts(origin, scheme="http")
        host = self.headers.get("Host")
        if origin_parts is None or host is None:
            return False
        request_parts = _origin_parts(f"http://{host}", scheme="http")
        return request_parts is not None and origin_parts == request_parts

    def _session_token(self) -> str | None:
        access = self.server.surface.private_access
        if access is None:
            return None
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name == access.cookie_name:
                return value
        return None

    def _login(self, command: Mapping[str, object]) -> JsonObject:
        access = self.server.surface.private_access
        if access is None:
            raise UiRequestError("route not found", status_code=404)
        if set(command) != {"password"} or not isinstance(command.get("password"), str):
            raise UiRequestError("invalid credentials", status_code=401)
        try:
            session = access.login(
                cast(str, command["password"]),
                client_id=self.client_address[0],
            )
        except LoginRateLimited:
            raise UiRequestError("login temporarily unavailable", status_code=429) from None
        except PrivateAccessError:
            raise UiRequestError("invalid credentials", status_code=401) from None
        self._pending_cookie = access.cookie_header(session.session_token)
        return {
            "schema_version": 1,
            "mode": "private",
            "status": "authenticated",
            "csrf_token": session.csrf_token,
            "expires_at": int(session.expires_at),
        }

    def _setup_local_owner(self, command: Mapping[str, object]) -> JsonObject:
        if set(command) != {"password"} or not isinstance(command.get("password"), str):
            raise UiRequestError("choose an owner password", status_code=400)
        session = self.server.surface.configure_local_owner(
            cast(str, command["password"]), client_id=self.client_address[0]
        )
        access = self.server.surface.private_access
        if access is None:  # pragma: no cover - defensive invariant
            raise UiRequestError("owner setup could not be completed", status_code=500)
        self._pending_cookie = access.cookie_header(session.session_token)
        return {
            "schema_version": 1,
            "mode": "private",
            "status": "authenticated",
            "csrf_token": session.csrf_token,
            "expires_at": int(session.expires_at),
        }

    def _host_matches_server(self) -> bool:
        """Reject DNS-rebinding Host values for this repository-backed surface."""
        host = self.headers.get("Host")
        if host is None:
            return False
        if self.server.surface.private_access is not None:
            access = self.server.surface.private_access
            return access.host_allowed(host)
        request_parts = _origin_parts(f"http://{host}", scheme="http")
        if request_parts is None:
            return False
        hostname, port = request_parts
        server_port = cast(tuple[str, int], self.server.server_address)[1]
        return hostname in {"127.0.0.1", "localhost"} and port == server_port

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
        pending_cookie = getattr(self, "_pending_cookie", None)
        if isinstance(pending_cookie, str):
            self.send_header("Set-Cookie", pending_cookie)
            self._pending_cookie = None
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        access = self.server.surface.private_access
        if access is not None and access.production:
            self.send_header(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
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
    ui_application: UiApplicationPort,
    private_access: PrivateAccessController | None = None,
    settings_application: PrivateSettingsApplication | None = None,
    local_owner_setup: bool = False,
    runtime_credentials: RuntimeCredentialStore | None = None,
) -> ThreadingHTTPServer:
    """Create one private repository-backed browser server."""

    private_production = bool(
        private_access is not None and private_access.production
    )
    _require_bind_host(
        host,
        private_production=private_production,
    )
    if settings_application is not None and private_access is None:
        raise ValueError("settings application requires private access")
    if local_owner_setup and private_access is not None:
        raise ValueError("local owner setup cannot be combined with private access")
    if runtime_credentials is not None and not (private_access or local_owner_setup):
        raise ValueError("runtime credentials require private access or local owner setup")
    if local_owner_setup and host not in {"127.0.0.1", "localhost"}:
        raise ValueError("local owner setup requires a loopback bind host")
    if type(port) is not int or not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    server = _BrowserServer(
        (host, port),
        BrowserSurface(
            ui_application,
            private_access=private_access,
            settings_application=settings_application,
            runtime_credentials=runtime_credentials,
        ),
    )
    if local_owner_setup:
        bound_host, bound_port = cast(tuple[str, int], server.server_address)
        server.surface.enable_local_owner_setup(f"http://{bound_host}:{bound_port}")
    return server


def serve(
    host: str = DEFAULT_BROWSER_HOST,
    port: int = DEFAULT_BROWSER_PORT,
    *,
    ui_application: UiApplicationPort,
    private_access: PrivateAccessController | None = None,
    settings_application: PrivateSettingsApplication | None = None,
    local_owner_setup: bool = False,
    runtime_credentials: RuntimeCredentialStore | None = None,
) -> None:
    """Serve the browser surface until interrupted."""

    server = create_server(
        host,
        port,
        ui_application=ui_application,
        private_access=private_access,
        settings_application=settings_application,
        local_owner_setup=local_owner_setup,
        runtime_credentials=runtime_credentials,
    )
    bound_host, bound_port = cast(tuple[str, int], server.server_address)
    print(f"Cardine product shell: http://{bound_host}:{bound_port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cardine-shell-web",
        description="Serve Cardine from a canonical local repository.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_BROWSER_HOST,
        help="bind address; non-loopback requires private production controls",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_BROWSER_PORT)
    parser.add_argument(
        "--repository",
        type=Path,
        required=True,
        help="local repository root",
    )
    parser.add_argument(
        "--course-id",
        default=None,
        help="optional initial course identity; must be paired with --session-id",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="optional initial session identity; must be paired with --course-id",
    )
    access_mode = parser.add_mutually_exclusive_group()
    access_mode.add_argument(
        "--private",
        action="store_true",
        help="enable single-owner access using CARDINE_OWNER_PASSWORD_HASH",
    )
    access_mode.add_argument(
        "--local-owner-setup",
        action="store_true",
        help=(
            "enable a one-time loopback-only browser setup for the owner password; "
            "the verifier and runtime credentials are cleared on restart"
        ),
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="use the production Secure __Host session cookie",
    )
    args = parser.parse_args()
    if (args.course_id is None) != (args.session_id is None):
        parser.error("--course-id and --session-id must be provided together")
    if args.production and not args.private:
        parser.error("--production requires --private")
    try:
        from .ui_application import RepositoryUiApplication

        private_access = None
        settings_application = None
        environment = None
        credentials = None
        if args.private or args.local_owner_setup:
            credentials = RuntimeCredentialStore()
            environment = credentials
        ui_application = RepositoryUiApplication(
            args.repository,
            args.course_id,
            args.session_id,
            environment=environment,
        )
        if args.private:
            from .private_access import PrivateAccessController

            password_hash = os.environ.get("CARDINE_OWNER_PASSWORD_HASH")
            canonical_origin = os.environ.get("CARDINE_PUBLIC_ORIGIN")
            if not password_hash or not canonical_origin:
                parser.error(
                    "--private requires CARDINE_OWNER_PASSWORD_HASH and CARDINE_PUBLIC_ORIGIN"
                )
            private_access = PrivateAccessController(
                password_hash,
                canonical_origin=canonical_origin,
                production=args.production,
            )
            credentials = (
                credentials if credentials is not None else RuntimeCredentialStore()
            )
            settings_application = PrivateSettingsApplication(
                ui_application,
                credentials=credentials,
            )
        serve(
            args.host,
            args.port,
            ui_application=ui_application,
            private_access=private_access,
            settings_application=settings_application,
            local_owner_setup=args.local_owner_setup,
            runtime_credentials=credentials,
        )
    except ValueError as error:
        parser.error(str(error))


def _diagnostic_category(error: UiRequestError) -> str:
    """Map known safe UI failures to opaque local-preview diagnostics."""

    if error.diagnostic_code is not None:
        return error.diagnostic_code
    message = str(error)
    if error.status_code == HTTPStatus.UNAUTHORIZED:
        return "authentication_required"
    if error.status_code == HTTPStatus.CONFLICT:
        return "stale_sequence"
    if error.status_code == HTTPStatus.SERVICE_UNAVAILABLE:
        return (
            "tutor_execution_failed"
            if message == "repository runtime is unavailable"
            else "repository_runtime_unavailable"
        )
    return "invalid_request"


def _json_bytes(payload: JsonObject) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=list,
    ).encode("utf-8")


def _is_json_content_type(value: str | None) -> bool:
    if value is None:
        return False
    media_type, _, _parameters = value.partition(";")
    return media_type.strip().lower() == "application/json"


def _is_private_endpoint(path: str) -> bool:
    return (
        path.startswith("/api/v1/auth/")
        or path.startswith("/api/v1/settings")
        or path == "/api/v1/chat/course-creation"
        or path == "/api/v1/sources/upload"
    )


def _origin_parts(value: str, *, scheme: str) -> tuple[str, int] | None:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != scheme
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or hostname is None
    ):
        return None
    return hostname.lower(), port if port is not None else 80


def _require_bind_host(
    host: str,
    *,
    private_production: bool = False,
) -> None:
    if host in {"127.0.0.1", "localhost"}:
        return
    if host == "0.0.0.0":
        if private_production:
            return
        raise ValueError("0.0.0.0 requires private production mode")
    raise ValueError("bind host must be localhost or 0.0.0.0 in private production mode")


__all__ = [
    "API_PREFIX",
    "DEFAULT_BROWSER_HOST",
    "DEFAULT_BROWSER_PORT",
    "HEALTH_PATH",
    "MAX_API_BODY_BYTES",
    "PREVIEW_RUNTIME_ID",
    "BrowserSurface",
    "create_server",
    "main",
    "serve",
]


if __name__ == "__main__":
    main()
