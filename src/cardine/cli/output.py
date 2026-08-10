"""Stable terminal and machine output for the reference CLI."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TextIO

from study_agent.domain._validation import JsonObject


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    command: str
    data: JsonObject


def emit_success(
    outcome: CommandOutcome, *, json_mode: bool, stream: TextIO | None = None
) -> None:
    stream = sys.stdout if stream is None else stream
    if json_mode:
        _json_line({"ok": True, "command": outcome.command, "data": outcome.data}, stream)
        return
    for key, value in outcome.data.items():
        if isinstance(value, tuple):
            if not value:
                stream.write(f"{key}: none\n")
            else:
                stream.write(f"{key}:\n")
                for item in value:
                    stream.write(f"  {json.dumps(item, ensure_ascii=True, sort_keys=True)}\n")
        else:
            stream.write(f"{key}: {value}\n")


def emit_error(
    code: str,
    message: str,
    *,
    json_mode: bool,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    details: JsonObject | None = None,
) -> None:
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    error: JsonObject = (
        {"code": code, "message": message}
        if details is None
        else {"code": code, "message": message, "details": details}
    )
    document = {"ok": False, "error": error}
    if json_mode:
        _json_line(document, stdout)
    else:
        stderr.write(f"error: {message}\n")


def progress(message: str, *, stream: TextIO | None = None) -> None:
    stream = sys.stderr if stream is None else stream
    stream.write(f"{message}\n")


def _json_line(value: object, stream: TextIO) -> None:
    stream.write(json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    stream.write("\n")


__all__ = ["CommandOutcome", "emit_error", "emit_success", "progress"]
