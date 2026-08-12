from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "study_agent"
INDEPENDENT = ("domain", "state", "skills", "playbooks")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_core_owners_do_not_reverse_import_capability_gateway_or_runtime_sdks() -> None:
    forbidden = (
        "study_agent.capabilities",
        "openai",
        "anthropic",
        "fastapi",
        "streamlit",
    )
    violations: list[str] = []
    for package_name in INDEPENDENT:
        package = SOURCE_ROOT / package_name
        for path in sorted(package.rglob("*.py")):
            for imported in sorted(_imports(path)):
                if any(
                    imported == prefix or imported.startswith(f"{prefix}.") for prefix in forbidden
                ):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {imported}")
    assert violations == []


def test_capability_gateway_does_not_import_tools_or_product_layers() -> None:
    package = SOURCE_ROOT / "capabilities"
    forbidden = (
        "study_agent.tools.registry",
        "cardine.cli",
        "study_agent.adapters",
        "study_agent.application",
        "study_agent.sessions",
        "study_agent.study_context",
        "sbobby_web",
    )
    violations = [
        f"{path.relative_to(PROJECT_ROOT)} imports {imported}"
        for path in sorted(package.rglob("*.py"))
        for imported in sorted(_imports(path))
        if any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in forbidden)
    ]
    assert violations == []
