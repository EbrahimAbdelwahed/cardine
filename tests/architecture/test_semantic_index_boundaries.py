from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "study_agent"


def test_semantic_core_remains_provider_free() -> None:
    forbidden = ("jev", "typesafe", "pageindex", "study_agent.adapters", "httpx", "requests")
    paths = [
        ROOT / "domain" / "document_index.py",
        ROOT / "ports" / "document_index.py",
        ROOT / "ports" / "judgement.py",
        ROOT / "knowledge" / "document_index.py",
    ]
    paths += list((ROOT / "flashcards").glob("*.py"))
    paths += list((ROOT / "hosts").glob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_text())
        for statement in ast.walk(tree):
            names: list[str] = []
            if isinstance(statement, ast.Import):
                names = [alias.name for alias in statement.names]
            elif isinstance(statement, ast.ImportFrom) and statement.module:
                names = [statement.module]
            assert not any(
                name == prefix or name.startswith(prefix + ".")
                for name in names
                for prefix in forbidden
            ), path


def test_document_index_has_no_canonical_identity_materialization() -> None:
    path = ROOT / "knowledge" / "document_index.py"
    tree = ast.parse(path.read_text())
    forbidden = {"_unit_id", "UnitId", "RetrievableUnit", "Citation"}
    for statement in ast.walk(tree):
        if isinstance(statement, (ast.FunctionDef, ast.ClassDef)):
            assert statement.name not in forbidden
        if isinstance(statement, ast.Call) and isinstance(statement.func, ast.Name):
            assert statement.func.id not in forbidden
