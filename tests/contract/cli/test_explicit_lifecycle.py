from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cardine.cli.main import main
from cardine.cli.repository import LocalRepository
from study_agent.domain import CourseId


def _run(capsys: Any, *arguments: str) -> tuple[int, dict[str, Any]]:
    code = main((*arguments, "--json"))
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    return code, json.loads(captured.out)


def _create_course(capsys: Any, root: Path, course_id: str, title: str) -> None:
    code, document = _run(
        capsys,
        "--repository",
        str(root),
        "course",
        "create",
        "--course-id",
        course_id,
        "--title",
        title,
        "--learning-goal",
        f"Learn {title}",
    )
    assert code == 0
    assert document["data"]["id"] == course_id


def test_course_list_is_empty_then_projection_sorted_and_deterministic(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "study"
    assert _run(capsys, "init", str(root))[0] == 0
    base = ("--repository", str(root), "course", "list")

    code, empty = _run(capsys, *base)
    assert code == 0
    assert empty["data"] == {"courses": []}

    _create_course(capsys, root, "course-z", "Zoology")
    _create_course(capsys, root, "course-a", "Anatomy")
    first = _run(capsys, *base)
    second = _run(capsys, *base)
    assert first == second
    assert [item["id"] for item in first[1]["data"]["courses"]] == [
        "course-a",
        "course-z",
    ]


def test_explicit_session_start_is_course_scoped_idempotent_and_readable_after_restart(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "study"
    assert _run(capsys, "init", str(root))[0] == 0
    _create_course(capsys, root, "course-a", "Anatomy")
    _create_course(capsys, root, "course-b", "Biology")
    base = ("--repository", str(root))
    start_a = (*base, "session", "start", "course-a", "--session-id", "session-1")

    code, first = _run(capsys, *start_a)
    assert code == 0
    assert first["data"] == {
        "course_id": "course-a",
        "session_id": "session-1",
        "status": "active",
        "started_at": first["data"]["started_at"],
        "suspended_at": None,
        "resumed_at": None,
        "ended_at": None,
        "interaction_count": 0,
        "answer_count": 0,
    }
    with LocalRepository.open(root) as repository:
        sequence_after_first = len(repository.events.read(CourseId("course-a")))

    assert _run(capsys, *start_a) == (0, first)
    with LocalRepository.open(root) as repository:
        assert len(repository.events.read(CourseId("course-a"))) == sequence_after_first

    code, read_back = _run(
        capsys, *base, "session", "get", "course-a", "session-1"
    )
    assert code == 0
    assert read_back["data"] == first["data"]
    with LocalRepository.open(root) as repository:
        assert len(repository.events.read(CourseId("course-a"))) == sequence_after_first

    code, second_course = _run(
        capsys,
        *base,
        "session",
        "start",
        "course-b",
        "--session-id",
        "session-1",
    )
    assert code == 0
    assert second_course["data"]["course_id"] == "course-b"
    assert second_course["data"]["session_id"] == "session-1"
    assert _run(
        capsys, *base, "session", "get", "course-a", "session-1"
    )[1]["data"] == first["data"]


def test_discovery_marks_only_explicit_host_identities_as_agent_safe(
    capsys: Any,
) -> None:
    code, document = _run(capsys, "describe")
    assert code == 0
    commands = {item["name"]: item for item in document["data"]["commands"]}

    assert "convenience-only" in commands["ask"]["summary"]
    assert "agent-safe only" in commands["ask"]["idempotency"]
    assert "not crash-retry-safe" in commands["ask"]["retry"]
    assert commands["session.start"]["idempotency"].startswith("stable host-supplied")
    session_id = next(
        item for item in commands["session.start"]["arguments"] if item["name"] == "session_id"
    )
    assert session_id["kind"] == "option"
    assert session_id["required"] is True

    grounding = next(
        item
        for item in document["data"]["study_tools"]
        if item["manifest"]["name"] == "grounding.ask"
    )["manifest"]["input_schema"]
    assert set(grounding["properties"]) == {"question"}
    assert grounding["additionalProperties"] is False


def test_session_start_requires_a_host_supplied_identity(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "study"
    assert _run(capsys, "init", str(root))[0] == 0
    _create_course(capsys, root, "course-a", "Anatomy")

    code, document = _run(
        capsys, "--repository", str(root), "session", "start", "course-a"
    )
    assert code == 2
    assert document["error"]["code"] == "invalid_request"
