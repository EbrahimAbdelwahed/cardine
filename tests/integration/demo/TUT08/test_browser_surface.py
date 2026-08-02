from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from http.client import HTTPConnection
from typing import cast

from study_agent.demo.browser import create_server
from study_agent.domain._validation import JsonObject


class _PathRecordingApplication:
    mode = "local_repository"

    def __init__(self) -> None:
        self.path = ""

    def get(self, path: str) -> JsonObject:
        self.path = path
        return {"schema_version": 1, "status": "ready"}

    def post(self, path: str, command: Mapping[str, object]) -> JsonObject:
        del command
        self.path = path
        return {"schema_version": 1, "status": "committed"}


def test_browser_transport_decodes_opaque_identifier_path_segments() -> None:
    application = _PathRecordingApplication()
    server = create_server("127.0.0.1", 0, ui_application=application)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        host, port = cast(tuple[str, int], server.server_address)
        connection = HTTPConnection(host, port, timeout=2)
        connection.request(
            "GET",
            "/api/v1/bootstrap",
            headers={"Host": f"attacker.example:{port}"},
        )
        rebound_get = connection.getresponse()
        rebound_get.read()
        connection.close()
        assert rebound_get.status == 421
        assert application.path == ""

        body = json.dumps(
            {
                "schema_version": 1,
                "request_id": "encoded-path",
                "expected_sequence": 1,
                "payload": {},
            }
        ).encode()
        connection = HTTPConnection(host, port, timeout=2)
        connection.request(
            "POST",
            "/api/v1/assessments/presentation-sha256%3Aopaque/attempts",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Host": f"attacker.example:{port}",
                "Origin": f"http://attacker.example:{port}",
            },
        )
        rebound_post = connection.getresponse()
        rebound_post.read()
        connection.close()
        assert rebound_post.status == 421
        assert application.path == ""

        connection = HTTPConnection(host, port, timeout=2)
        connection.request(
            "POST",
            "/api/v1/assessments/presentation-sha256%3Aopaque/attempts",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        response.read()
        connection.close()

        assert response.status == 200
        assert application.path == (
            "/api/v1/assessments/presentation-sha256:opaque/attempts"
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
