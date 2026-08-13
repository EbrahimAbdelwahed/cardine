"""Authenticated, lock-independent transport contract for live activity."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from threading import Thread
from typing import cast

import pytest

from cardine.demo.browser import BrowserSurface, create_server
from cardine.demo.private_access import PrivateAccessController, hash_password
from cardine.diagnostics.turn_activity import TurnActivityStore
from study_agent.domain._validation import JsonObject


class _ActivityApplication:
    mode = "local_repository"

    def __init__(self) -> None:
        self.turn_activity = TurnActivityStore()
        self.lock = threading.Lock()

    def get(self, path: str) -> JsonObject:
        # The application owns this route and checks it before its normal
        # repository-read lock. BrowserSurface only supplies authentication.
        if path.startswith("/api/v1/turns/") and path.endswith("/activity"):
            request_id = path.removeprefix("/api/v1/turns/").removesuffix("/activity")
            return self.turn_activity.snapshot(request_id)
        with self.lock:
            raise AssertionError(f"unexpected locked route: {path}")

    def post(self, path: str, command: object) -> JsonObject:
        del path, command
        raise AssertionError("not used")


def _get(connection: HTTPConnection, path: str) -> tuple[int, object]:
    connection.request("GET", path, headers={"Accept": "application/json"})
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    return response.status, payload


def test_activity_route_returns_unknown_without_touching_repository_lock() -> None:
    application = _ActivityApplication()
    server = create_server("127.0.0.1", 0, ui_application=application)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = cast(tuple[str, int], server.server_address)
    try:
        application.lock.acquire()
        connection = HTTPConnection(host, port, timeout=1)
        status, payload = _get(connection, "/api/v1/turns/unknown-request/activity")
        connection.close()
        assert status == 200
        assert payload == {
            "schema_version": 1,
            "state": "unknown",
            "records": [],
            "omitted": 0,
        }
    finally:
        if application.lock.locked():
            application.lock.release()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_activity_route_remains_behind_existing_api_authentication() -> None:
    application = _ActivityApplication()
    access = PrivateAccessController(
        hash_password("correct horse battery staple"),
        canonical_origin="http://127.0.0.1:8765",
    )
    surface = BrowserSurface(application, private_access=access)

    with pytest.raises(Exception) as error:
        surface.api_get("/api/v1/turns/request/activity")
    assert getattr(error.value, "status_code", None) == 401

    session = access.login("correct horse battery staple", client_id="test")
    payload = surface.api_get("/api/v1/turns/request/activity", session_token=session.session_token)
    assert payload["state"] == "unknown"
    assert payload["records"] == []


def test_repository_activity_get_is_independent_of_mutation_lock(tmp_path) -> None:
    """The real application route is checked before normal locked reads."""
    from cardine.demo.ui_application import RepositoryUiApplication
    from tests.integration.demo.TUT08.test_repository_backed_chat import _repository

    root, adapters, _model = _repository(tmp_path)
    application = RepositoryUiApplication(
        root, "cardine-course", "cardine-session", model_adapters=adapters
    )
    application._lock.acquire()  # type: ignore[attr-defined]
    result: list[object] = []

    def read_activity() -> None:
        try:
            result.append(application.get("/api/v1/turns/lock-free/activity"))
        except Exception as error:  # pragma: no cover - assertion below reports it
            result.append(error)

    worker = Thread(target=read_activity, daemon=True)
    worker.start()
    worker.join(timeout=0.5)
    application._lock.release()  # type: ignore[attr-defined]
    assert not worker.is_alive(), "activity polling must not wait on the repository lock"
    assert result and not isinstance(result[0], Exception)
    assert result[0] == {
        "schema_version": 1,
        "state": "unknown",
        "records": [],
        "omitted": 0,
    }
