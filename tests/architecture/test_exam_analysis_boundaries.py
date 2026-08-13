from __future__ import annotations

import ast
from pathlib import Path

from study_agent.tools import public_study_tool_manifests

PROJECT_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "cardine"
STUDY_AGENT_ROOT = PROJECT_ROOT / "src" / "study_agent"
EXAM_FILES = (
    SOURCE_ROOT / "exams" / "contracts.py",
    SOURCE_ROOT / "exams" / "analysis.py",
    SOURCE_ROOT / "exams" / "worker.py",
    STUDY_AGENT_ROOT / "ports" / "exam.py",
    STUDY_AGENT_ROOT / "tools" / "exam_scope_bridge.py",
    STUDY_AGENT_ROOT / "skills" / "builtin" / "analyze_exam_sample.py",
    STUDY_AGENT_ROOT / "playbooks" / "builtin" / "analyze_exam_sample_flow.py",
    STUDY_AGENT_ROOT / "prompts" / "exam_sample_analysis_v1.py",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(path: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_exam_analysis_files_exist_at_the_declared_boundary() -> None:
    missing = [path.relative_to(PROJECT_ROOT) for path in EXAM_FILES if not path.is_file()]
    assert missing == []


def test_exam_layer_cannot_import_provider_or_canonical_write_owners() -> None:
    forbidden = (
        "study_agent.adapters",
        "study_agent.artifacts",
        "study_agent.capabilities.gateway",
        "study_agent.events",
        "study_agent.models",
        "study_agent.ports.model",
        "study_agent.state.events",
        "study_agent.state.store",
        "anthropic",
        "deepseek",
        "google.generativeai",
        "openai",
    )
    violations = [
        f"{path.relative_to(PROJECT_ROOT)} imports {name}"
        for path in EXAM_FILES
        for name in sorted(_imports(path))
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    ]
    assert violations == []


def test_exam_facade_has_no_direct_execution_or_store_dependencies() -> None:
    path = SOURCE_ROOT / "exams" / "worker.py"
    forbidden_names = {
        "CapabilityGateway",
        "ModelAdapter",
        "GenerationWorkerStore",
        "VerifiedChildProofOwner",
        "ValidatorExecutor",
    }
    forbidden_arguments = {
        "artifact_store",
        "event_store",
        "gateway",
        "model",
        "proof_store",
        "worker_store",
    }
    violations: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Name) and node.id in forbidden_names:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} references {node.id}")
        if isinstance(node, ast.arg) and node.arg in forbidden_arguments:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} accepts {node.arg}")
    assert violations == []


def test_private_exam_scope_bridge_does_not_expand_public_study_tools() -> None:
    names = tuple(manifest.name for manifest in public_study_tool_manifests())
    assert names == (
        "citation.resolve",
        "course.get",
        "grounding.ask",
        "session.get_context",
        "session.record_note",
        "source.list",
        "source.search",
    )
    assert "source.prepare_exam_sample_scope" not in names
