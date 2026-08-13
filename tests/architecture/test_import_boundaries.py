from __future__ import annotations

import ast
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "study_agent"
CORE_PACKAGES = tuple(
    SOURCE_ROOT / name
    for name in (
        "domain",
        "ports",
        "grounding",
        "prompts",
        "skills",
        "playbooks",
        "sessions",
        "study_context",
    )
)
FORBIDDEN_TOP_LEVEL_IMPORTS = {
    "anthropic",
    "click",
    "fastapi",
    "openai",
    "sqlalchemy",
    "sqlite3",
    "tau",
    "typer",
}
FORBIDDEN_INTERNAL_PREFIXES = {
    "study_agent.adapters",
    "cardine.cli",
    "study_agent.retrieval",
}
CLI_INDEPENDENT_PACKAGES = tuple(
    SOURCE_ROOT / name for name in ("application", "domain", "ports", "tools")
)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class ImportBoundaryTests(unittest.TestCase):
    def test_domain_and_ports_do_not_import_implementations(self) -> None:
        violations: list[str] = []
        for package in CORE_PACKAGES:
            for path in sorted(package.rglob("*.py")):
                for module in sorted(imported_modules(path)):
                    top_level = module.partition(".")[0]
                    if top_level in FORBIDDEN_TOP_LEVEL_IMPORTS or any(
                        module == prefix or module.startswith(f"{prefix}.")
                        for prefix in FORBIDDEN_INTERNAL_PREFIXES
                    ):
                        violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {module}")

        message = "Core import-boundary violations:\n" + "\n".join(violations)
        self.assertEqual([], violations, message)

    def test_application_and_tool_layers_do_not_import_cli_composition(self) -> None:
        violations: list[str] = []
        for package in CLI_INDEPENDENT_PACKAGES:
            for path in sorted(package.rglob("*.py")):
                for module in sorted(imported_modules(path)):
                    if module == "cardine.cli" or module.startswith("cardine.cli."):
                        violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {module}")

        message = "CLI reverse-import violations:\n" + "\n".join(violations)
        self.assertEqual([], violations, message)


if __name__ == "__main__":
    unittest.main()
