from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "study_agent"
CARDINE_ROOT = Path(__file__).parents[2] / "src" / "cardine"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_neutral_host_contracts_do_not_import_effect_or_provider_layers() -> None:
    paths = (
        CARDINE_ROOT / "hosts" / "contracts.py",
        CARDINE_ROOT / "hosts" / "context.py",
        CARDINE_ROOT / "hosts" / "runner.py",
        CARDINE_ROOT / "hosts" / "scripted.py",
        ROOT / "ports" / "tutor_host.py",
        ROOT / "ports" / "tutor_runner.py",
    )
    forbidden = (
        "study_agent.adapters",
        "study_agent.application",
        "study_agent.models",
        "study_agent.providers",
        "openai",
        "anthropic",
        "deepseek",
        "httpx",
        "requests",
        "sqlite3",
        "pathlib",
        "os",
        "subprocess",
    )
    violations = {
        f"{path.relative_to(ROOT.parent)} imports {imported}"
        for path in paths
        for imported in _imports(path)
        if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden)
    }
    assert violations == set()


def test_host_file_contracts_do_not_import_filesystem_or_provider_layers() -> None:
    paths = (
        CARDINE_ROOT / "hosts" / "files.py",
        ROOT / "ports" / "host_file.py",
    )
    forbidden = (
        "study_agent.adapters.filesystem",
        "study_agent.application",
        "study_agent.providers",
        "openai",
        "anthropic",
        "deepseek",
        "httpx",
        "requests",
        "sqlite3",
        "pathlib",
        "os",
        "subprocess",
    )
    violations = {
        f"{path.relative_to(ROOT.parent)} imports {imported}"
        for path in paths
        for imported in _imports(path)
        if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden)
    }
    assert violations == set()


def test_existing_state_and_behavior_owners_do_not_depend_on_tutor_hosts() -> None:
    owner_paths = tuple(
        path
        for directory in (
            "domain",
            "state",
            "skills",
            "playbooks",
            "capabilities",
            "assessments",
            "tutor_snapshot",
        )
        for path in (ROOT / directory).rglob("*.py")
    )
    violations = {
        str(path.relative_to(ROOT))
        for path in owner_paths
        if any(
            imported == "cardine.hosts" or imported.startswith("cardine.hosts.")
            for imported in _imports(path)
        )
    }
    assert violations == set()


def test_decision_port_exposes_only_effect_free_decision_and_interruption_methods() -> None:
    tree = ast.parse((ROOT / "ports" / "tutor_host.py").read_text(encoding="utf-8"))
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    protocols = {
        node.name: {
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in classes
        if any(isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases)
    }
    non_protocol_classes = {
        node.name
        for node in classes
        if not any(isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases)
    }

    assert protocols == {
        "TutorInterruptionToken": {"is_interrupted"},
        "TutorDecisionPort": {"decide"},
    }
    assert non_protocol_classes == {"RetryableTutorDecisionError"}
    source = (ROOT / "ports" / "tutor_host.py").read_text(encoding="utf-8").lower()
    for forbidden in ("event_store", "filesystem", "gateway", "persist", "write", "model"):
        assert forbidden not in source


def test_runner_ports_are_explicit_and_do_not_add_state_owners() -> None:
    source = (ROOT / "ports" / "tutor_runner.py").read_text(encoding="utf-8")
    assert "class TutorCapabilityGatewayPort" in source
    assert "class TutorHostAuthorityPort" in source
    assert "class TutorHostActionIdentityPort" in source
    assert "class TutorContinuationStore" in source
    for forbidden in ("event_store", "filesystem", "sqlite3", "requests", "openai"):
        assert forbidden not in source.lower()


def test_reference_closure_reuses_one_runner_and_keeps_provider_optional() -> None:
    example = (ROOT.parents[1] / "examples" / "reference_tutor_host.py").read_text(
        encoding="utf-8"
    )
    assert "TutorHostRunner" in example
    assert "ScriptedTutorDecisionPort" in example
    assert "OpenAIResponsesTutorDecisionPort" in example
    assert "subprocess" not in example
    assert "import requests" not in example
