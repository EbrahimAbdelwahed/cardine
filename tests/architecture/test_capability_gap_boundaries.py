from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "cardine"
FEEDBACK = ROOT / "feedback"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_gap_plane_does_not_import_model_network_devkit_or_course_owners() -> None:
    forbidden = (
        "study_agent.model",
        "study_agent.prompt",
        "study_agent.playbooks",
        "study_agent.skills",
        "study_agent.capabilities",
        "cardine.courses",
        "study_agent.sessions",
        "study_agent.events",
        "cardine.cli",
        "httpx",
        "openai",
        "flywheel",
        "devkit",
    )
    for path in FEEDBACK.glob("*.py"):
        imports = _imports(path)
        assert not any(
            imported == target or imported.startswith(target + ".")
            for imported in imports
            for target in forbidden
        ), path


def test_gap_plane_is_small_and_separate_from_study_tools() -> None:
    assert {path.name for path in FEEDBACK.glob("*.py")} == {
        "__init__.py",
        "contracts.py",
        "host_tool.py",
        "outbox.py",
        "service.py",
        "source_tracer.py",
        "view.py",
        "workarounds.py",
        "workaround_service.py",
    }
