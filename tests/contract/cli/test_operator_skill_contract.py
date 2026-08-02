from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

import pytest

from study_agent.cli.registry import agent_operations_manifest, registration_for
from study_agent.operator_skill import (
    EXTRACTION_COMMAND,
    SKILL_ID,
    SKILL_VERSION,
    extract_skill,
    skill_bytes,
    skill_fingerprint,
    skill_metadata,
)


def test_operator_skill_has_one_canonical_closed_identity() -> None:
    content = skill_bytes()
    assert content.startswith(b"---\nname: study-agent-operator\n")
    assert sha256(content).hexdigest() == skill_fingerprint()
    assert skill_metadata() == {
        "id": SKILL_ID,
        "version": SKILL_VERSION,
        "fingerprint": skill_fingerprint(),
        "extraction_command": EXTRACTION_COMMAND,
    }
    assert agent_operations_manifest()["operator_skill"] == skill_metadata()


def test_operator_skill_frontmatter_contains_only_name_and_description() -> None:
    frontmatter = skill_bytes().decode("utf-8").split("---\n", 2)[1]
    keys = {
        line.split(":", 1)[0]
        for line in frontmatter.splitlines()
        if line and not line.startswith(" ")
    }
    assert keys == {"name", "description"}


def test_operator_skill_preserves_authority_and_recovery_boundaries() -> None:
    content = skill_bytes().decode("utf-8")
    for required in (
        "host authority",
        "model-proposed arguments as untrusted",
        "append-only event stream is canonical",
        "skills and playbooks",
        "adapters only for technical transport",
        "installed `cardine` distribution",
        "--repository .",
        "reject symlinks",
        "--session-id",
        "--idempotency-key",
        "status",
        "fresh `manifest plan`",
        "--expect-plan NEW_SHA",
        "Never blindly replay an old plan",
        "identical checksummed file tree and contents",
    ):
        assert required in content


def test_operator_skill_command_is_repository_and_network_free() -> None:
    registration = registration_for("operator.skill")
    assert registration.to_json() == {
        "name": "operator.skill",
        "version": "1.0.0",
        "summary": "Extract the versioned operator skill from the installed distribution.",
        "effect": "local_write",
        "repository": "none",
        "network": "never",
        "idempotency": "byte-identical for the same installed distribution",
        "retry": "safe to retry to an absent or byte-identical destination",
        "arguments": (
            {
                "name": "output",
                "kind": "option",
                "value_type": "path",
                "required": True,
                "repeated": False,
                "default_json": None,
                "secret": False,
            },
        ),
        "output_contract": "cli-envelope@1",
        "verification": "verify the receipt fingerprint against the extracted bytes",
    }


def test_package_resource_path_is_the_only_skill_copy() -> None:
    project = Path(__file__).parents[3]
    copies = tuple(
        path.relative_to(project).as_posix()
        for path in project.rglob("SKILL.md")
        if ".venv" not in path.parts and "build" not in path.parts
    )
    assert copies == ("src/study_agent/operator_skill/SKILL.md",)


def test_operator_skill_extraction_rejects_symlink_and_nonregular_output(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.md"
    target.write_bytes(b"do not replace")
    symlink = tmp_path / "skill.md"
    symlink.symlink_to(target)

    with pytest.raises(FileExistsError):
        extract_skill(symlink)
    assert target.read_bytes() == b"do not replace"

    with pytest.raises(FileExistsError):
        extract_skill(tmp_path)


def test_operator_skill_extraction_accepts_only_identical_racing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "skill.md"

    def identical_racer(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        **kwargs: object,
    ) -> None:
        Path(destination).write_bytes(skill_bytes())
        raise FileExistsError

    monkeypatch.setattr(os, "link", identical_racer)
    receipt = extract_skill(output)
    assert output.read_bytes() == skill_bytes()
    assert receipt["fingerprint"] == skill_fingerprint()

    output.unlink()

    def different_racer(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        **kwargs: object,
    ) -> None:
        Path(destination).write_bytes(b"racing content")
        raise FileExistsError

    monkeypatch.setattr(os, "link", different_racer)
    with pytest.raises(FileExistsError):
        extract_skill(output)
    assert output.read_bytes() == b"racing content"
