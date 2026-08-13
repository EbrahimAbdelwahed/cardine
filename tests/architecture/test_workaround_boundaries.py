from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "cardine"
STUDY_AGENT_ROOT = Path(__file__).parents[2] / "src" / "study_agent"


def test_workaround_plane_has_no_execution_or_provider_dependency() -> None:
    files = (ROOT / "feedback" / "workarounds.py", ROOT / "feedback" / "workaround_service.py")
    forbidden = {
        "subprocess",
        "importlib",
        "httpx",
        "requests",
        "openai",
        "study_agent.model",
        "study_agent.capabilities",
        "study_agent.playbooks",
        "study_agent.events",
        "flywheel",
        "devkit",
    }
    for path in files:
        tree = ast.parse(path.read_text())
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert not any(
            name in forbidden or name.startswith(f"{item}.")
            for name in imported
            for item in forbidden
        )


def test_workaround_executor_is_only_an_inward_protocol() -> None:
    source = (STUDY_AGENT_ROOT / "ports" / "workaround.py").read_text()
    assert "class WorkaroundExecutor(Protocol)" in source
    assert "subprocess" not in source
    assert "importlib" not in source
