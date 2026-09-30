from __future__ import annotations

import json
import subprocess
import urllib.request
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from cardine.adapters.audio import groq
from study_agent.domain._validation import JsonObject


def test_groq_request_uses_turbo_segment_timestamps_and_sanitizes_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []

    def respond(request: Any, *, timeout: int) -> BytesIO:
        requests.append(request)
        assert timeout == 180
        return BytesIO(
            json.dumps(
                {
                    "text": " Lezione uno. ",
                    "segments": [{"start": 0.5, "end": 2, "text": "Lezione uno."}],
                }
            ).encode()
        )

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    result = groq.transcribe_chunk(b"flac-fixture", "private-fixture")
    assert result["text"] == "Lezione uno."
    assert result["spans"] == ({"start_ms": 500, "end_ms": 2000, "text": "Lezione uno."},)
    request = requests[0]
    assert request.full_url == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert request.get_header("Authorization") == "Bearer private-fixture"
    assert b"whisper-large-v3-turbo" in request.data
    assert b"timestamp_granularities[]" in request.data
    assert b"\r\nsegment\r\n" in request.data
    assert b"private-fixture" not in request.data

    def fail(request: Any, *, timeout: int) -> BytesIO:
        raise HTTPError(
            request.full_url, 429, "private-fixture", Message(), BytesIO(b"raw provider")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(groq.AudioError, match="HTTP 429") as caught:
        groq.transcribe_chunk(b"flac-fixture", "private-fixture")
    assert "private-fixture" not in str(caught.value)
    assert "raw provider" not in str(caught.value)


def test_audio_retry_reuses_receipts_and_offsets_second_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def prepare(arguments: list[str], **kwargs: Any) -> Any:
        if arguments[0] == "ffprobe":
            return subprocess.CompletedProcess(arguments, 0, b"620\n")
        pattern = Path(arguments[-1])
        pattern.with_name("chunk-000.flac").write_bytes(b"first")
        pattern.with_name("chunk-001.flac").write_bytes(b"second")
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(subprocess, "run", prepare)
    calls: list[bytes] = []
    saves: list[int] = []
    preflights: list[bool] = []

    def call(audio: bytes, credential: str) -> JsonObject:
        calls.append(audio)
        return {"text": "Seconda parte.", "spans": ({"start_ms": 0, "end_ms": 20000},)}

    first: JsonObject = {"text": "Prima parte.", "spans": ({"start_ms": 0, "end_ms": 1000},)}
    text, manifest = groq.GroqAudioTranscriber("fixture", call=call).transcribe(
        b"input",
        "wav",
        [first],
        lambda rows: saves.append(len(rows)),
        lambda: preflights.append(True),
    )
    assert calls == [b"second"]
    assert saves == [2]
    assert len(preflights) == 2
    assert text == "Prima parte.\n\nSeconda parte."
    assert manifest["spans"] == (
        {"start_ms": 0, "end_ms": 1000},
        {"start_ms": 600000, "end_ms": 620000},
    )
