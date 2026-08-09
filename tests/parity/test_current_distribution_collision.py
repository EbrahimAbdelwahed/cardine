from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parents[2]
COLLISION_PATHS = (
    ROOT / "tests/parity/current_collision.json",
    ROOT / "specs/harness-adoption/assets/current-collision.json",
)


def _read(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_current_wheel_collision_is_explicitly_expected_red() -> None:
    first, second = (_read(path) for path in COLLISION_PATHS)
    assert first == second
    assert first["status"] == "expected_red"
    assert first["supported_side_by_side_install"] is False
    cardine = first["cardine_distribution"]
    harness = first["harness_distribution"]
    collisions = first["collisions"]
    assert cardine["name"] == "cardine"
    assert harness["name"] == "study-agent-harness"
    assert cardine["regular_packages"] == harness["regular_packages"] == ["study_agent"]
    assert collisions["regular_packages"] == ["study_agent"]
    assert set(collisions["console_entry_points"]) <= set(cardine["console_entry_points"])
    assert set(collisions["console_entry_points"]) <= set(harness["console_entry_points"])
    assert "expected red" in first["evidence"]


def test_cardine_metadata_proves_the_regular_package_and_alias_collision() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    cardine_entry_points = set(scripts)
    expected = _read(COLLISION_PATHS[0])
    assert set(expected["cardine_distribution"]["console_entry_points"]) == cardine_entry_points
    assert all(target.startswith("study_agent.") for target in scripts.values())
    aliases = {name for name in scripts if name.startswith("study-agent")}
    assert aliases == set(expected["collisions"]["console_entry_points"])
