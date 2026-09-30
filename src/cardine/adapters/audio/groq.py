"""Server-side Groq speech adapter and bounded local audio preparation."""

from __future__ import annotations

import json
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import uuid4

from study_agent.domain._validation import JsonObject

MAX_AUDIO_BYTES = 128 * 1024 * 1024
MAX_AUDIO_SECONDS = 8 * 60 * 60
CHUNK_SECONDS = 600
MODEL = "whisper-large-v3-turbo"
AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "mp4", "ogg", "webm", "flac", "aac"}


class AudioError(ValueError):
    """A bounded, safe error; never contains provider responses or credentials."""


def transcribe_chunk(audio: bytes, credential: str) -> JsonObject:
    if not credential:
        raise AudioError("Configura GROQ_API_KEY sul server per trascrivere l'audio.")
    if not 0 < len(audio) <= 24 * 1024 * 1024:
        raise AudioError("Blocco audio troppo grande.")
    boundary = "cardine-" + uuid4().hex
    parts: list[bytes] = []
    for key, value in (
        ("model", MODEL),
        ("language", "it"),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "segment"),
        ("temperature", "0"),
    ):
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
            ).encode()
        )
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="chunk.flac"\r\nContent-Type: audio/flac\r\n\r\n'
        ).encode()
    )
    parts.extend((audio, f"\r\n--{boundary}--\r\n".encode()))
    request = urllib.request.Request(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        data=b"".join(parts),
        headers={
            "Authorization": "Bearer " + credential,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise AudioError("Risposta di trascrizione troppo grande.")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise AudioError("Risposta di trascrizione non valida.")
        text = payload["text"].strip()
        if not text:
            raise AudioError("Il blocco non contiene una trascrizione utilizzabile.")
        spans: list[JsonObject] = []
        for item in payload.get("segments", []):
            start, end = item["start"], item["end"]
            if not (isinstance(start, (int, float)) and isinstance(end, (int, float))):
                raise AudioError("Intervalli di trascrizione non validi.")
            if not 0 <= start < end <= CHUNK_SECONDS + 1:
                raise AudioError("Intervalli di trascrizione non validi.")
            spans.append(
                {
                    "start_ms": round(start * 1000),
                    "end_ms": round(end * 1000),
                    "text": str(item["text"]),
                }
            )
        return {"text": text, "spans": tuple(spans), "model": MODEL}
    except urllib.error.HTTPError as error:
        raise AudioError(f"Groq ha rifiutato la trascrizione (HTTP {error.code}).") from None
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, AudioError):
            raise
        raise AudioError(
            "Trascrizione non disponibile; puoi riprendere i blocchi mancanti."
        ) from None


class GroqAudioTranscriber:
    def __init__(
        self, credential: str, call: Callable[[bytes, str], JsonObject] = transcribe_chunk
    ):
        self._credential = credential
        self._call = call

    def transcribe(
        self,
        data: bytes,
        extension: str,
        recovered: list[JsonObject],
        save: Callable[[list[JsonObject]], None],
        preflight: Callable[[], None],
    ) -> tuple[str, JsonObject]:
        if extension not in AUDIO_EXTENSIONS or not 0 < len(data) <= MAX_AUDIO_BYTES:
            raise AudioError("Formato o dimensione audio non supportati.")
        if not self._credential:
            raise AudioError("Configura GROQ_API_KEY sul server per trascrivere l'audio.")
        with tempfile.TemporaryDirectory(prefix="cardine-audio-") as root:
            input_path = Path(root) / ("input." + extension)
            input_path.write_bytes(data)
            try:
                probe = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        str(input_path),
                    ],
                    capture_output=True,
                    timeout=30,
                    check=True,
                )
                duration = float(probe.stdout)
                if not 0 < duration <= MAX_AUDIO_SECONDS:
                    raise AudioError("La registrazione deve durare meno di otto ore.")
                subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-i",
                        str(input_path),
                        "-map",
                        "0:a:0",
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-f",
                        "segment",
                        "-segment_time",
                        str(CHUNK_SECONDS),
                        "-reset_timestamps",
                        "1",
                        str(Path(root) / "chunk-%03d.flac"),
                    ],
                    capture_output=True,
                    timeout=300,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                if isinstance(error, AudioError):
                    raise
                raise AudioError(
                    "Preparazione audio fallita. Il server richiede ffmpeg e ffprobe."
                ) from None
            chunks = sorted(Path(root).glob("chunk-*.flac"))
            if not chunks or len(chunks) > 49 or len(recovered) > len(chunks):
                raise AudioError("Numero di blocchi audio non valido.")
            for chunk in chunks[len(recovered) :]:
                preflight()
                result = self._call(chunk.read_bytes(), self._credential)
                if not str(result.get("text", "")).strip():
                    raise AudioError("Blocco audio incompleto.")
                recovered.append(result)
                save(recovered)
            preflight()
            text = "\n\n".join(str(item["text"]) for item in recovered)
            spans: list[JsonObject] = []
            for index, item in enumerate(recovered):
                for span in cast(tuple[JsonObject, ...], item.get("spans", ())):
                    spans.append(
                        {
                            **span,
                            "start_ms": int(cast(int, span["start_ms"]))
                            + index * CHUNK_SECONDS * 1000,
                            "end_ms": int(cast(int, span["end_ms"])) + index * CHUNK_SECONDS * 1000,
                        }
                    )
            return text, {
                "model": MODEL,
                "duration_seconds": duration,
                "spans": tuple(spans),
                "chunk_count": len(chunks),
            }
