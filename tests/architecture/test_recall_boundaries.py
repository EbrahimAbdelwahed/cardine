from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "study_agent"
OWNERS = (ROOT / "domain", ROOT / "ports", ROOT / "recall")
FORBIDDEN_MODULES = {
    "fsrs",
    "anki",
    "study_agent.adapters",
    "cardine.cli",
    "study_agent.application",
    "study_agent.ports.storage",
    "study_agent.prompts",
    "study_agent.skills",
    "study_agent.playbooks",
    "study_agent.tools",
}


def test_recall_contract_owners_have_no_outward_provider_or_storage_imports() -> None:
    violations: list[str] = []
    for owner in OWNERS:
        for path in owner.rglob("*.py"):
            if owner == ROOT / "domain" and path.name not in {"recall.py", "identifiers.py"}:
                continue
            if owner == ROOT / "ports" and path.name not in {"recall.py", "scheduling.py"}:
                continue
            if owner == ROOT / "recall" and path.name not in {
                "__init__.py",
                "contracts.py",
                "events.py",
                "projection.py",
                "view.py",
            }:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                imported = None
                if isinstance(node, ast.Import):
                    imported = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = (node.module,)
                if imported and any(
                    module == forbidden or module.startswith(forbidden + ".")
                    for module in imported
                    for forbidden in FORBIDDEN_MODULES
                ):
                    violations.append(f"{path}: {imported}")
    assert not violations


def test_recall_sources_do_not_persist_package_or_global_learner_state() -> None:
    source = "\n".join(
        path.read_text()
        for owner in (ROOT / "domain", ROOT / "ports", ROOT / "recall")
        for path in owner.rglob("*.py")
        if owner != ROOT / "domain" or path.name in {"recall.py", "identifiers.py"}
    ).lower()
    assert "review_log" not in source
    assert "ease_factor" not in source
    assert "mastery" not in source
    assert "learner aggregate" not in source
