from __future__ import annotations

import json
from io import StringIO

from cardine.cli.output import CommandOutcome, emit_error, emit_success


def test_json_success_is_exactly_one_stable_document() -> None:
    stream = StringIO()
    emit_success(CommandOutcome("doctor", {"status": "ok"}), json_mode=True, stream=stream)
    assert stream.getvalue().count("\n") == 1
    assert json.loads(stream.getvalue()) == {
        "ok": True,
        "command": "doctor",
        "data": {"status": "ok"},
    }


def test_json_error_does_not_write_diagnostics_to_stderr() -> None:
    stdout = StringIO()
    stderr = StringIO()
    emit_error(
        "not_found",
        "not found",
        json_mode=True,
        stdout=stdout,
        stderr=stderr,
    )
    assert json.loads(stdout.getvalue()) == {
        "ok": False,
        "error": {"code": "not_found", "message": "not found"},
    }
    assert stderr.getvalue() == ""
