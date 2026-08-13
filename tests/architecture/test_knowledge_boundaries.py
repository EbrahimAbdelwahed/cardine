from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "study_agent"


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_substrate_domain_contract_has_no_adapter_provider_or_model_imports() -> None:
    forbidden = (
        "study_agent.adapters",
        "study_agent.application",
        "cardine.cli",
        "study_agent.connectors",
        "study_agent.models",
        "study_agent.providers",
        "openai",
        "anthropic",
        "deepseek",
        "httpx",
        "requests",
        "sqlite3",
        "socket",
    )
    paths = (ROOT / "domain" / "substrate.py", ROOT / "domain" / "identifiers.py")
    violations = {
        str(path.relative_to(ROOT)): imported
        for path in paths
        for imported in imports(path)
        if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden)
    }
    assert violations == {}


def test_substrate_modules_do_not_define_a_second_byte_store_or_filesystem_owner() -> None:
    paths = (
        ROOT / "domain" / "substrate.py",
        ROOT / "ingestion" / "substrate.py",
        ROOT / "ingestion" / "substrate_events.py",
        ROOT / "ingestion" / "substrate_projection.py",
    )
    forbidden_class_names = {"BlobStore", "FilesystemBlobStore", "SQLiteEventStore"}
    forbidden_effect_methods = {"open", "write", "unlink", "rename", "mkdir"}
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in forbidden_class_names:
                violations.append(f"{path.relative_to(ROOT)} defines {node.name}")
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in forbidden_effect_methods
            ):
                violations.append(f"{path.relative_to(ROOT)} defines {node.name}")
    assert violations == []


def test_public_substrate_types_are_owned_by_domain_and_ingestion_modules() -> None:
    from study_agent.domain import PageMapEntry, Substrate, SubstrateProduction
    from study_agent.ingestion.substrate import ConverterReceipt, SubstrateProductionService

    assert PageMapEntry.__module__ == "study_agent.domain.substrate"
    assert Substrate.__module__ == "study_agent.domain.substrate"
    assert SubstrateProduction.__module__ == "study_agent.domain.substrate"
    assert ConverterReceipt.__module__ == "study_agent.ingestion.substrate"
    assert SubstrateProductionService.__module__ == "study_agent.ingestion.substrate"
