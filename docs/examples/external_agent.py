"""Provider-neutral external-agent journey through the public CLI contract."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from study_agent.cli.repository import LocalRepository
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    SessionId,
)

EXPECTED_TOOLS = (
    "citation.resolve",
    "course.get",
    "grounding.ask",
    "session.get_context",
    "session.record_note",
    "source.list",
    "source.search",
)


def _installed_executable() -> str:
    configured = os.environ.get("STUDY_AGENT_BIN")
    executable = configured or shutil.which("study-agent")
    if executable is None:
        raise RuntimeError("install Cardine and verify cardine --help first")
    return str(Path(executable).expanduser().absolute())


STUDY_AGENT_BIN = _installed_executable()


def _run(*arguments: str, cwd: Path | None = None) -> dict[str, Any]:
    process = subprocess.run(
        (STUDY_AGENT_BIN, "--json", *arguments),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stdout.strip() or process.stderr.strip())
    value = json.loads(process.stdout)
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError("study-agent returned an invalid success envelope")
    return cast(dict[str, Any], value)


def discover_and_extract_skill(destination: Path) -> dict[str, Any]:
    """Negotiate the versioned contract before allowing an agent to act."""
    described = _run("describe")["data"]
    if described["contract_version"] != "agent-operations@1":
        raise RuntimeError("unsupported study-agent operation contract")
    names = tuple(item["manifest"]["name"] for item in described["study_tools"])
    if names != EXPECTED_TOOLS:
        raise RuntimeError("unexpected StudyTool authority surface")
    skill = described["operator_skill"]
    extracted = _run("operator", "skill", "--output", str(destination))["data"]
    extracted_bytes = destination.read_bytes()
    extracted_fingerprint = sha256(extracted_bytes).hexdigest()
    if not (
        extracted["fingerprint"]
        == skill["fingerprint"]
        == extracted_fingerprint
    ):
        raise RuntimeError("operator skill fingerprint mismatch")
    return cast(dict[str, Any], described)


def populate_offline(root: Path, source_text: str) -> dict[str, Any]:
    """Complete the credential-free blank-project journey."""
    root = root.expanduser().absolute()
    _run("init", str(root))
    base = ("--repository", ".")
    _run(
        *base,
        "course",
        "create",
        "--course-id",
        "course-example",
        "--title",
        "Example course",
        "--learning-goal",
        "Explain the supplied material",
        cwd=root,
    )
    source = root / "source.md"
    _write_source_exclusively(source, source_text.encode("utf-8"))
    _run(
        *base,
        "source",
        "add",
        "course-example",
        source.name,
        "--source-id",
        "source-example",
        cwd=root,
    )
    _run(*base, "doctor", cwd=root)
    _run(
        *base,
        "session",
        "start",
        "course-example",
        "--session-id",
        "session-example",
        cwd=root,
    )
    _run("tool", "list", cwd=root)
    asyncio.run(_invoke_offline_tool(root))
    first = _run(
        *base,
        "export",
        "course-example",
        "--output",
        "export-1",
        cwd=root,
    )
    second = _run(
        *base,
        "export",
        "course-example",
        "--output",
        "export-2",
        cwd=root,
    )
    if first["data"]["high_water_sequence"] != second["data"]["high_water_sequence"]:
        raise RuntimeError("repeated export advanced the canonical event high-water mark")
    if _directory_tree(root / "export-1") != _directory_tree(root / "export-2"):
        raise RuntimeError("repeated export was not byte-identical")
    return second


def _write_source_exclusively(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


async def _invoke_offline_tool(root: Path) -> None:
    course_id = CourseId("course-example")
    session_id = SessionId("session-example")
    context = ExecutionContext(
        PrincipalKind.SERVICE,
        "external-agent-example",
        course_id,
        CorrelationId("external-agent-example-course-get"),
        frozenset({"study:read"}),
        session_id,
    )
    with LocalRepository.open(root, environment={}) as repository:
        result = await repository.study_tools(course_id).invoke("course.get", {}, context)
    if result.error is not None or result.value is None:
        raise RuntimeError("trusted offline StudyTool invocation failed")
    profile = result.value.get("profile")
    if not isinstance(profile, Mapping) or profile.get("id") != str(course_id):
        raise RuntimeError("offline StudyTool returned the wrong course")


def _directory_tree(root: Path) -> tuple[tuple[str, bytes], ...]:
    entries: list[tuple[str, bytes]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        status = path.lstat()
        if stat.S_ISDIR(status.st_mode):
            entries.append((f"{relative}/", b""))
        elif stat.S_ISREG(status.st_mode):
            entries.append((relative, path.read_bytes()))
        else:
            raise RuntimeError("export contains a non-regular filesystem entry")
    return tuple(entries)


def optional_ask(root: Path, question: str) -> dict[str, Any]:
    """Ask only after the host has configured an available generic adapter."""
    return _run(
        "--repository",
        str(root),
        "ask",
        "course-example",
        question,
        "--session-id",
        "session-example",
        "--idempotency-key",
        "ask-example-1",
    )


if __name__ == "__main__":
    repository = Path(sys.argv[1] if len(sys.argv) > 1 else "./study-repository")
    discover_and_extract_skill(repository.parent / "study-agent-operator.md")
    receipt = populate_offline(repository, "# Example\nCanonical source material.\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))
