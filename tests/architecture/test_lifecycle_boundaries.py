from __future__ import annotations

import ast
import sys
from pathlib import Path

from study_agent.tools.builtin import public_study_tool_manifests

ROOT = Path(__file__).parents[2] / "src" / "study_agent"
CARDINE_ROOT = Path(__file__).parents[2] / "src" / "cardine"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_lifecycle_contracts_do_not_import_cli_adapters_providers_or_stateful_services() -> None:
    forbidden = (
        "study_agent.adapters",
        "study_agent.application",
        "cardine.cli",
        "cardine.courses",
        "study_agent.ingestion",
        "study_agent.retrieval",
        "study_agent.sessions",
        "study_agent.tools",
        "openai",
        "anthropic",
        "deepseek",
        "httpx",
        "requests",
        "socket",
        "sqlite3",
    )
    imports = _imports(ROOT / "lifecycle" / "contracts.py")
    assert not {
        item
        for item in imports
        if any(item == prefix or item.startswith(prefix + ".") for prefix in forbidden)
    }


def test_lifecycle_planner_does_not_import_io_or_stateful_owners() -> None:
    forbidden = (
        "study_agent.adapters",
        "study_agent.application",
        "cardine.cli",
        "cardine.courses",
        "study_agent.ingestion",
        "study_agent.retrieval",
        "study_agent.sessions",
        "study_agent.tools",
        "pathlib",
        "socket",
        "sqlite3",
    )

    imports = _imports(ROOT / "lifecycle" / "planner.py")

    assert not {
        item
        for item in imports
        if any(item == prefix or item.startswith(prefix + ".") for prefix in forbidden)
    }


def test_domain_events_and_projections_do_not_import_lifecycle_intent() -> None:
    violations: list[str] = []
    for package in ("courses", "ingestion", "sessions"):
        for path in sorted((ROOT / package).rglob("*.py")):
            if path.name not in {"events.py", "projection.py"}:
                continue
            for imported in _imports(path):
                if imported == "study_agent.lifecycle" or imported.startswith(
                    "study_agent.lifecycle."
                ):
                    violations.append(f"{path.relative_to(ROOT)} imports {imported}")
    assert violations == []


def test_lifecycle_adds_no_study_tools_and_exact_seven_surface_is_unchanged() -> None:
    manifests = public_study_tool_manifests()
    assert tuple(item.name for item in manifests) == (
        "citation.resolve",
        "course.get",
        "grounding.ask",
        "session.get_context",
        "session.record_note",
        "source.list",
        "source.search",
    )
    assert len({item.identity for item in manifests}) == 7


def test_repository_target_adapter_owns_the_boundary_without_forbidden_imports() -> None:
    adapter = ROOT / "adapters" / "filesystem" / "repository_target.py"
    forbidden = (
        "study_agent.application",
        "cardine.cli",
        "cardine.courses",
        "study_agent.domain",
        "study_agent.ingestion",
        "study_agent.lifecycle",
        "study_agent.ports",
        "study_agent.retrieval",
        "study_agent.sessions",
        "study_agent.tools",
        "study_agent.adapters.model",
        "openai",
        "anthropic",
        "deepseek",
        "httpx",
        "requests",
        "socket",
        "sqlite3",
    )

    imports = _imports(adapter)

    assert not {
        item
        for item in imports
        if any(item == prefix or item.startswith(prefix + ".") for prefix in forbidden)
    }


def test_cli_repository_delegates_initialization_to_filesystem_adapter() -> None:
    path = CARDINE_ROOT / "cli" / "repository.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    locally_defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    adapter_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "study_agent.adapters.filesystem"
        for alias in node.names
    }

    assert "initialize_local_repository" in adapter_imports
    assert "initialize_local_repository" not in locally_defined


def test_source_input_port_is_provider_neutral_and_standard_library_only() -> None:
    path = ROOT / "ports" / "source_input.py"

    assert {
        imported
        for imported in _imports(path)
        if imported.split(".", 1)[0] not in sys.stdlib_module_names
    } == set()


def test_filesystem_source_input_has_no_behavior_or_provider_dependencies() -> None:
    path = ROOT / "adapters" / "filesystem" / "source_input.py"
    non_standard_library = {
        imported
        for imported in _imports(path)
        if imported.split(".", 1)[0] not in sys.stdlib_module_names
    }

    assert non_standard_library == {"study_agent.ports.source_input"}


def test_manifest_reader_does_not_own_source_snapshot_io() -> None:
    path = ROOT / "adapters" / "filesystem" / "lifecycle.py"
    source = path.read_text(encoding="utf-8")

    assert "FilesystemSourceInput" not in source
    assert "SourceSnapshot" not in source
    assert "MAX_SOURCE_BYTES" not in source
    assert "_read_source" not in source


def test_cli_delegates_source_capture_to_the_single_filesystem_adapter() -> None:
    path = CARDINE_ROOT / "cli" / "commands.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    locally_defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    adapter_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "study_agent.adapters.filesystem"
        for alias in node.names
    }
    calls = {
        (
            node.func.value.id,
            node.func.attr,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    source_io_attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in {"read_bytes", "read_text"}
    }
    builtin_open_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open"
    }
    source_bound_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id.startswith("MAX_SOURCE")
    }

    assert "FilesystemSourceInput" in adapter_imports
    assert "_read_source" not in locally_defined
    assert source_bound_names == set()
    assert not ({("os", "open"), ("os", "read")} & calls)
    assert source_io_attributes == set()
    assert builtin_open_calls == set()
