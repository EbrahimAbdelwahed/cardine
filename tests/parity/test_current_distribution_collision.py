from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from tests.parity.derive_collision import derive

ROOT = Path(__file__).parents[2]
ASSETS = (
    ROOT / "tests/parity/current_collision.json",
    ROOT / "specs/harness-adoption/assets/current-collision.json",
)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    assert isinstance(value, Mapping), label
    return value


def test_current_wheel_collision_is_explicitly_expected_red_and_derived() -> None:
    expected = derive()
    for path in ASSETS:
        assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert expected["expected_red"] is True
    assert expected["install_policy"] == "inspect-independent-zips-never-coinstall"
    collisions = _mapping(expected["collisions"], "collision metadata must be an object")
    assert collisions["regular_packages"] == ["study_agent"]
    entry_points = collisions["console_entry_points"]
    assert isinstance(entry_points, list)
    assert set(entry_points) == {
        "study-agent",
        "study-agent-demo",
        "study-agent-shell",
        "study-agent-shell-web",
    }


def test_pinned_artifacts_have_metadata_entry_points_and_member_hashes() -> None:
    expected = derive()
    artifacts = _mapping(expected["artifacts"], "artifact metadata must be an object")
    for raw_artifact in artifacts.values():
        artifact = _mapping(raw_artifact, "artifact metadata must be an object")
        assert artifact["version"] == "0.2.0"
        digest = artifact["sha256"]
        assert isinstance(digest, str)
        assert len(digest) == 64
        metadata_member = artifact["metadata_member"]
        assert isinstance(metadata_member, str)
        assert metadata_member.endswith("/METADATA")
        entry_points_member = artifact["entry_points_member"]
        assert isinstance(entry_points_member, str)
        assert entry_points_member.endswith("/entry_points.txt")
        member_digests = artifact["member_sha256"]
        assert isinstance(member_digests, Mapping)
        assert member_digests
