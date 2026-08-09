from __future__ import annotations

import json
from pathlib import Path

from tests.parity.derive_collision import derive

ROOT = Path(__file__).parents[2]
ASSETS = (
    ROOT / "tests/parity/current_collision.json",
    ROOT / "specs/harness-adoption/assets/current-collision.json",
)


def test_current_wheel_collision_is_explicitly_expected_red_and_derived() -> None:
    expected = derive()
    for path in ASSETS:
        assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert expected["expected_red"] is True
    assert expected["install_policy"] == "inspect-independent-zips-never-coinstall"
    collisions = expected["collisions"]
    assert collisions["regular_packages"] == ["study_agent"]
    assert set(collisions["console_entry_points"]) == {
        "study-agent",
        "study-agent-demo",
        "study-agent-shell",
        "study-agent-shell-web",
    }


def test_pinned_artifacts_have_metadata_entry_points_and_member_hashes() -> None:
    expected = derive()
    for artifact in expected["artifacts"].values():
        assert artifact["version"] == "0.2.0"
        assert len(artifact["sha256"]) == 64
        assert artifact["metadata_member"].endswith("/METADATA")
        assert artifact["entry_points_member"].endswith("/entry_points.txt")
        assert artifact["member_sha256"]
