from __future__ import annotations

import importlib
import json
import os
import signal

from cardine.cli.main import build_parser, color_enabled, main
from cardine.cli.output import CommandOutcome
from study_agent.sessions import RetryableSessionConflictError


def test_parser_exposes_only_approved_top_level_commands() -> None:
    help_text = build_parser().format_help()
    assert help_text.startswith("usage: cardine ")
    assert (
        "{init,course,source,consent,pageindex,lesson,artifact,ask,session,export,doctor,operator,manifest,describe,tool}"
        in help_text
    )


def test_no_color_disables_color(monkeypatch: object) -> None:
    monkeypatch.setenv("NO_COLOR", "1")  # type: ignore[attr-defined]
    assert color_enabled() is False


def test_json_global_help_is_one_success_document(capsys: object) -> None:
    assert main(("--json", "--help")) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    document = json.loads(captured.out)
    assert document["ok"] is True
    assert document["command"] == "help"
    assert "Local event-sourced Cardine study repository" in document["data"]["text"]


def test_json_subcommand_help_is_one_success_document_with_flag_anywhere(
    capsys: object,
) -> None:
    assert main(("source", "add", "--help", "--json")) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    document = json.loads(captured.out)
    assert document["ok"] is True
    assert "source add" in document["data"]["text"]


def test_auto_session_sigint_after_execute_still_emits_one_success(
    capsys: object, monkeypatch: object
) -> None:
    module = importlib.import_module("cardine.cli.main")

    async def committed(_request: object) -> CommandOutcome:
        os.kill(os.getpid(), signal.SIGINT)
        return CommandOutcome("ask", {"answer_id": "answer-1"})

    monkeypatch.setattr(module, "execute", committed)  # type: ignore[attr-defined]
    assert main(("ask", "course-1", "question", "--json")) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out)["ok"] is True


def test_auto_session_sigint_during_emit_still_emits_one_success(
    capsys: object, monkeypatch: object
) -> None:
    module = importlib.import_module("cardine.cli.main")
    real_emit = module.emit_success

    async def committed(_request: object) -> CommandOutcome:
        return CommandOutcome("ask", {"answer_id": "answer-1"})

    def interrupted_emit(*args: object, **kwargs: object) -> None:
        os.kill(os.getpid(), signal.SIGINT)
        real_emit(*args, **kwargs)

    monkeypatch.setattr(module, "execute", committed)  # type: ignore[attr-defined]
    monkeypatch.setattr(module, "emit_success", interrupted_emit)  # type: ignore[attr-defined]
    assert main(("--json", "ask", "course-1", "question")) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out)["ok"] is True


def test_retryable_session_conflict_has_machine_clean_cli_error(
    capsys: object, monkeypatch: object
) -> None:
    module = importlib.import_module("cardine.cli.main")

    async def raced(_request: object) -> CommandOutcome:
        raise RetryableSessionConflictError("sensitive internal conflict detail")

    monkeypatch.setattr(module, "execute", raced)  # type: ignore[attr-defined]
    assert main(("--json", "session", "start", "course-1", "--session-id", "s-1")) == 4
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.err == ""
    assert captured.out == (
        '{"error":{"code":"retryable_conflict","message":'
        '"session state changed concurrently; retry with the same host-supplied identity"},'
        '"ok":false}\n'
    )
    assert "Traceback" not in captured.out
