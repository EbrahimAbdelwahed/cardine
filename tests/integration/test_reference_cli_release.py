from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, cast

from study_agent.application import StudyHarness
from cardine.cli.main import main
from cardine.cli.repository import LocalRepository, ModelAdapterRegistry
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    SessionId,
)
from study_agent.ports import (
    CancellationToken,
    ModelCapabilities,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelStreamEventKind,
)
from study_agent.sessions.events import grounded_answer_manifest
from study_agent.tools import StudyEvent

_EVIDENCE_ID = re.compile(r'"evidence_id":"([^"]+)"')
_PROJECT_ROOT = Path(__file__).parents[2]


class _FixtureModel:
    """Host-registered deterministic adapter; never part of production defaults."""

    capabilities = ModelCapabilities(structured_output=True)

    def __init__(self, calls: list[ModelRequest]) -> None:
        self._calls = calls

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self._calls.append(request)
        rendered = "\n".join(message.content for message in request.messages)
        match = _EVIDENCE_ID.search(rendered)
        if match is None:
            raise AssertionError("fixture expected sufficient canonical evidence")
        return ModelResponse(
            "",
            None,
            ModelFinishReason.STOP,
            ModelInvocation("test-fixture", "1.0.0", "offline-fixture", "fixture-response"),
            structured_output={
                "status": "answered",
                "segments": (
                    {
                        "kind": "supported_claim",
                        "text": "The brachial plexus is formed by C5 to T1 roots.",
                        "evidence_ids": (match.group(1),),
                    },
                ),
                "unsupported_information_note": None,
            },
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        del request
        if False:  # pragma: no cover - makes this an async generator
            yield ModelStreamEvent(ModelStreamEventKind.CANCELLED)
        raise AssertionError("the reference flow must not stream")

    async def cancel(self, token: CancellationToken) -> None:
        del token
        raise AssertionError("the fixture does not claim cancellation support")


def _registry(calls: list[ModelRequest]) -> ModelAdapterRegistry:
    def build(config: object, credential: str | None) -> _FixtureModel:
        del config, credential
        return _FixtureModel(calls)

    return ModelAdapterRegistry({"test-fixture": build}, versions={"test-fixture": "1.0.0"})


def _run(
    capsys: Any,
    registry: ModelAdapterRegistry,
    *arguments: str,
    environment: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    code = main(
        (*arguments, "--json"),
        model_adapters=registry,
        environment={} if environment is None else environment,
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    return code, json.loads(captured.out)


def _run_in_fresh_process(
    counter: Path, *arguments: str, expected_code: int = 0
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(_PROJECT_ROOT / "src"), str(_PROJECT_ROOT))
    )
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.support.cli_fixture_driver",
            str(counter),
            "--",
            *arguments,
            "--json",
        ],
        cwd=_PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode == expected_code, process.stdout + process.stderr
    assert process.stderr == ""
    assert process.stdout.count("\n") == 1
    value = json.loads(process.stdout)
    if not isinstance(value, dict):
        raise AssertionError("subprocess output must be a JSON object")
    return cast(dict[str, Any], value)


async def _events(
    harness: StudyHarness, question: str, context: ExecutionContext
) -> tuple[StudyEvent, ...]:
    return tuple([event async for event in harness.ask(question, context)])


def test_offline_release_journey_survives_restart_and_is_deterministic(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "study"
    calls: list[ModelRequest] = []
    registry = _registry(calls)
    assert _run(
        capsys,
        registry,
        "init",
        str(root),
        "--model-adapter",
        "test-fixture",
    )[0] == 0
    base = ("--repository", str(root))
    assert _run(
        capsys,
        registry,
        *base,
        "course",
        "create",
        "--course-id",
        "course-anatomy",
        "--title",
        "Anatomy",
        "--learning-goal",
        "Explain the brachial plexus",
    )[0] == 0
    (root / "plexus.md").write_text(
        "# Brachial plexus\nThe brachial plexus is formed by C5 to T1 roots.\n",
        encoding="utf-8",
    )
    (root / "context.md").write_text(
        "# Context\nThe roots combine into trunks before forming divisions.\n",
        encoding="utf-8",
    )
    for source in ("plexus.md", "context.md"):
        assert _run(
            capsys,
            registry,
            *base,
            "source",
            "add",
            "course-anatomy",
            source,
        )[0] == 0

    ask = (
        *base,
        "ask",
        "course-anatomy",
        "brachial plexus",
        "--session-id",
        "session-release",
        "--idempotency-key",
        "ask-release-1",
    )
    counter = tmp_path / "model-calls.txt"
    started = _run_in_fresh_process(
        counter,
        *base,
        "session",
        "start",
        "course-anatomy",
        "--session-id",
        "session-release",
    )
    assert started["data"]["status"] == "active"
    assert _run(
        capsys,
        registry,
        *base,
        "session",
        "start",
        "course-anatomy",
        "--session-id",
        "session-release",
    ) == (0, started)
    code, session = _run(
        capsys,
        registry,
        *base,
        "session",
        "get",
        "course-anatomy",
        "session-release",
    )
    assert code == 0
    assert session["data"] == started["data"]
    # The first process succeeds canonically but its stdout is deliberately discarded.
    _run_in_fresh_process(counter, *ask)
    first = _run_in_fresh_process(counter, *ask)
    assert first["data"]["answer"]["status"] == "answered"
    assert counter.read_text(encoding="utf-8") == "1"

    retried = _run_in_fresh_process(counter, *ask)
    assert retried == first
    assert counter.read_text(encoding="utf-8") == "1"

    conflict = _run_in_fresh_process(
        counter,
        *base,
        "ask",
        "course-anatomy",
        "changed question",
        "--session-id",
        "session-release",
        "--idempotency-key",
        "ask-release-1",
        expected_code=4,
    )
    assert conflict["error"]["code"] == "conflict"
    assert counter.read_text(encoding="utf-8") == "1"

    with LocalRepository.open(root, model_adapters=registry, environment={}) as repository:
        tool_registry = repository.study_tools(CourseId("course-anatomy"))
        assert tuple(item.name for item in tool_registry.manifests) == (
            "citation.resolve",
            "course.get",
            "grounding.ask",
            "session.get_context",
            "session.record_note",
            "source.list",
            "source.search",
        )
        canonical_answers = repository.sessions.answers(
            CourseId("course-anatomy"), SessionId("session-release")
        )
        assert len(canonical_answers) == 1
        assert str(canonical_answers[0].id) == first["data"]["answer_id"]
        assert str(canonical_answers[0].run_id) == first["data"]["run_id"]
        canonical_answer = json.loads(
            json.dumps(grounded_answer_manifest(canonical_answers[0].answer))
        )
        assert canonical_answer == first["data"]["answer"]
        parity_context = ExecutionContext(
            PrincipalKind.HUMAN,
            "release-parity-host",
            CourseId("course-anatomy"),
            CorrelationId("correlation-release-parity"),
            frozenset({"study:ask"}),
            SessionId("session-release"),
            idempotency_key="ask-release-1",
        )
        receipt = repository.rebuild_retrieval()
        service = repository.grounding_service(
            CourseId("course-anatomy"),
            repository.course_index_receipt(CourseId("course-anatomy"), receipt),
        )
        direct = asyncio.run(service.ask("brachial plexus", parity_context))
        tool = asyncio.run(
            tool_registry.invoke(
                "grounding.ask", {"question": "brachial plexus"}, parity_context
            )
        )
        harness_events = asyncio.run(
            _events(StudyHarness(service), "brachial plexus", parity_context)
        )
        assert str(direct.answer.id) == first["data"]["answer_id"]
        assert tool.error is None and tool.value is not None
        tool_record = json.loads(str(tool.value["answer_record_json"]))
        assert tool_record["id"] == first["data"]["answer_id"]
        assert tool_record["run_id"] == first["data"]["run_id"]
        assert tool_record["answer"] == first["data"]["answer"]
        assert tuple(item.to_json() for item in harness_events) == tool.value["events"]
        assert calls == []
        repository.session_service.suspend(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "offline-host",
                CourseId("course-anatomy"),
                CorrelationId("correlation-session-suspend"),
                session_id=SessionId("session-release"),
            )
        )
    code, resumed = _run(
        capsys,
        registry,
        *base,
        "session",
        "resume",
        "course-anatomy",
        "session-release",
    )
    assert code == 0
    assert resumed["data"]["status"] == "active"

    destinations = (root / "exports" / "one", root / "exports" / "two")
    for destination in destinations:
        assert _run(
            capsys,
            registry,
            *base,
            "export",
            "course-anatomy",
            "--output",
            str(destination),
        )[0] == 0
    snapshots = tuple(
        {
            item.relative_to(destination): item.read_bytes()
            for item in destination.rglob("*")
            if item.is_file()
        }
        for destination in destinations
    )
    assert snapshots[0] == snapshots[1]
    assert b"test-fixture" not in b"".join(snapshots[0].values())

    code, doctor = _run(capsys, registry, *base, "doctor")
    assert code == 0
    assert doctor["data"]["status"] == "ok"


def test_fixture_adapter_is_not_an_implicit_cli_fallback(tmp_path: Path, capsys: Any) -> None:
    root = tmp_path / "study"
    registry = _registry([])
    assert _run(
        capsys,
        registry,
        "init",
        str(root),
        "--model-adapter",
        "test-fixture",
    )[0] == 0
    base = ("--repository", str(root))
    assert _run(
        capsys,
        registry,
        *base,
        "course",
        "create",
        "--course-id",
        "course-a",
        "--title",
        "Course",
        "--learning-goal",
        "Prove explicit composition",
    )[0] == 0
    with LocalRepository.open(root, model_adapters=registry, environment={}) as repository:
        repository.session_service.start(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "offline-host",
                CourseId("course-a"),
                CorrelationId("correlation-negative-start"),
                session_id=SessionId("session-a"),
            )
        )
    code = main(
        (
            *base,
            "ask",
            "course-a",
            "question",
            "--session-id",
            "session-a",
            "--idempotency-key",
            "negative-ask",
            "--json",
        )
    )
    captured = capsys.readouterr()
    assert code == 4
    assert json.loads(captured.out)["error"]["code"] == "model_unavailable"
