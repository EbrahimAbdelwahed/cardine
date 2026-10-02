from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from typing import cast

from cardine.demo.browser import create_server
from study_agent.domain._validation import JsonObject
from tests.unit.demo.test_browser import _RepositoryApplication


class AudioApplication(_RepositoryApplication):
    def __init__(self) -> None:
        self.uploads: list[tuple[bytes, str, str, str]] = []

    def import_audio(
        self,
        *,
        input_path: Path,
        filename: str,
        title: str,
        request_id: str,
    ) -> JsonObject:
        self.uploads.append((input_path.read_bytes(), filename, title, request_id))
        return {"schema_version": 1, "job_id": "audio-fixture"}


def test_raw_audio_requires_exact_origin_and_decodes_bounded_upload_headers() -> None:
    app = AudioApplication()
    server = create_server("127.0.0.1", 0, ui_application=app)
    host, port = cast(tuple[str, int], server.server_address)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for origin, expected in (
            (None, 403),
            ("https://foreign.example", 403),
            (f"http://{host}:{port}", 200),
        ):
            connection = HTTPConnection(host, port, timeout=2)
            headers = {
                "Content-Type": "application/octet-stream",
                "X-File-Name": "Lezione%20uno.wav",
                "X-Source-Title": "Lezione%20uno",
                "Idempotency-Key": "recording-1",
            }
            if origin is not None:
                headers["Origin"] = origin
            connection.request(
                "POST", "/api/v1/sources/import/audio", body=b"audio", headers=headers
            )
            response = connection.getresponse()
            assert response.status == expected
            payload = json.loads(response.read())
            if expected == 200:
                assert payload["job_id"] == "audio-fixture"
            connection.close()
        assert app.uploads == [(b"audio", "Lezione uno.wav", "Lezione uno", "recording-1")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_private_audio_upload_requires_valid_session_csrf(tmp_path: Path) -> None:
    import pytest

    from cardine.demo.browser import BrowserSurface
    from cardine.demo.private_access import PrivateAccessController, hash_password
    from cardine.demo.ui_application import UiRequestError

    password = "correct horse battery staple"
    access = PrivateAccessController(hash_password(password))
    session = access.login(password, client_id="audio-test")
    app = AudioApplication()
    surface = BrowserSurface(app, private_access=access)
    recording = tmp_path / "recording.wav"
    recording.write_bytes(b"audio")
    assert surface.mode == "private"
    with pytest.raises(UiRequestError, match="csrf"):
        surface.api_post_audio(
            input_path=recording,
            filename="recording.wav",
            title="Recording",
            request_id="upload",
            session_token=session.session_token,
            csrf_token="wrong",
        )
    assert not app.uploads
    result = surface.api_post_audio(
        input_path=recording,
        filename="recording.wav",
        title="Recording",
        request_id="upload",
        session_token=session.session_token,
        csrf_token=session.csrf_token,
    )
    assert result["job_id"] == "audio-fixture"
    assert len(app.uploads) == 1
