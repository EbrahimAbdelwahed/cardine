from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from typing import cast

from study_agent.demo.browser import create_server


def _journey(entry: str) -> dict[str, object]:
    return {
        "learner_entry": entry,
        "status": "recovered",
        "status_trace": ({"step": 1, "status": "completed", "detail": "Grounded"},),
        "source_state": {"fixture": "heart-valves.md", "evidence": ("A fact",)},
        "evidence_refresh_sequence": 2,
        "discovered_capabilities": ("explain_concept",),
        "parity": True,
    }


def test_local_browser_journey_serves_page_state_and_free_form_entry() -> None:
    server = create_server("127.0.0.1", 0, journey=_journey)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        host, port = cast(tuple[str, int], server.server_address)
        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/")
        page_response = connection.getresponse()
        page = page_response.read()
        assert page_response.status == 200
        assert b"Start anywhere" in page
        assert b"Context conflicts" in page
        connection.close()

        for path, content_type, marker in (
            ("/browser.css", "text/css; charset=utf-8", b"--paper"),
            ("/browser.js", "text/javascript; charset=utf-8", b"/api/v1/bootstrap"),
        ):
            connection = HTTPConnection(host, port, timeout=2)
            connection.request("GET", path)
            asset_response = connection.getresponse()
            asset = asset_response.read()
            assert asset_response.status == 200
            assert asset_response.getheader("Content-Type") == content_type
            assert marker in asset
            connection.close()

        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/api/v1/bootstrap")
        bootstrap_response = connection.getresponse()
        bootstrap = json.loads(bootstrap_response.read())
        assert bootstrap_response.status == 200
        assert bootstrap["mode"] == "public_demo"
        assert bootstrap["high_water_sequence"] == 2
        connection.close()

        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/api/state")
        state_response = connection.getresponse()
        state_response.read()
        assert state_response.status == 200
        assert state_response.getheader("Content-Type") == "application/json; charset=utf-8"
        connection.close()

        body = json.dumps({"learner_entry": "  Explain the aortic valve  "}).encode()
        connection = HTTPConnection(host, port, timeout=2)
        connection.request(
            "POST",
            "/api/entry",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        entry_response = connection.getresponse()
        updated = entry_response.read()
        assert entry_response.status == 200
        assert json.loads(updated)["learner_entry"] == "Explain the aortic valve"
        connection.close()

        # Equivalent payloads are byte-stable for deterministic offline checks.
        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/api/state")
        assert connection.getresponse().read() == updated
        connection.close()

        command = json.dumps(
            {
                "schema_version": 1,
                "request_id": "browser-integration-request",
                "expected_sequence": 2,
                "payload": {"content": "Explain the pulmonary valve"},
            }
        ).encode()
        connection = HTTPConnection(host, port, timeout=2)
        connection.request(
            "POST",
            "/api/v1/session/turns",
            body=command,
            headers={"Content-Type": "application/json", "Content-Length": str(len(command))},
        )
        command_response = connection.getresponse()
        receipt = json.loads(command_response.read())
        assert command_response.status == 200
        assert receipt["status"] == "demo_completed"
        assert receipt["result"]["learner_entry"] == "Explain the pulmonary valve"
        connection.close()

        connection = HTTPConnection(host, port, timeout=2)
        connection.request("POST", "/api/entry", body=b'{"learner_entry":"   "}')
        invalid_response = connection.getresponse()
        assert invalid_response.status == 400
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_public_demo_disables_legacy_mutable_routes_and_sends_security_headers() -> None:
    server = create_server("127.0.0.1", 0, public_demo=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        host, port = cast(tuple[str, int], server.server_address)
        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/api/state")
        state_response = connection.getresponse()
        state_response.read()
        assert state_response.status == 404
        assert state_response.getheader("X-Content-Type-Options") == "nosniff"
        assert state_response.getheader("Content-Security-Policy") == (
            "default-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'; object-src 'none'"
        )
        connection.close()

        body = json.dumps({"learner_entry": "shared mutable state"}).encode()
        connection = HTTPConnection(host, port, timeout=2)
        connection.request(
            "POST",
            "/api/entry",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        entry_response = connection.getresponse()
        entry_response.read()
        assert entry_response.status == 404
        connection.close()

        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/health")
        health_response = connection.getresponse()
        health = json.loads(health_response.read())
        assert health_response.status == 200
        assert health == {"mode": "public_demo", "status": "ok"}
        connection.close()

        malformed = b'{"value":' + (b"9" * 5_000) + b"}"
        connection = HTTPConnection(host, port, timeout=2)
        connection.request(
            "POST",
            "/api/v1/session/turns",
            body=malformed,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(malformed)),
            },
        )
        malformed_response = connection.getresponse()
        assert malformed_response.status == 400
        assert json.loads(malformed_response.read()) == {
            "error": "request JSON is invalid"
        }
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
