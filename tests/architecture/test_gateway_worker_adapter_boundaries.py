from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from study_agent.tools import public_study_tool_manifests

PROJECT_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "study_agent"


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


def test_worker_resume_port_requires_the_exact_task_without_registry_lookup() -> None:
    port = SOURCE_ROOT / "ports" / "worker.py"
    resumes = [
        node
        for node in ast.walk(_tree(port))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "resume"
    ]
    assert len(resumes) == 1
    assert tuple(item.arg for item in resumes[0].args.args) == (
        "self",
        "task",
        "continuation",
        "response",
        "context",
    )
    combined = "\n".join(
        (SOURCE_ROOT / "workers" / name).read_text(encoding="utf-8")
        for name in ("service.py", "proof.py")
    ).lower()
    assert "task_registry" not in combined
    assert "task_cache" not in combined


def test_workers_and_capabilities_import_cleanly_in_both_orders() -> None:
    source_path = str(SOURCE_ROOT.parent)
    for modules in (
        ("study_agent.workers", "study_agent.capabilities"),
        ("study_agent.capabilities", "study_agent.workers"),
    ):
        imports = "; ".join(f"import {item}" for item in modules)
        result = subprocess.run(
            (
                sys.executable,
                "-I",
                "-c",
                f"import sys; sys.path.insert(0, {source_path!r}); {imports}",
            ),
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_definition_and_authority_use_one_public_helper_each() -> None:
    engine = (SOURCE_ROOT / "playbooks" / "engine.py").read_text(encoding="utf-8")
    gateway = (SOURCE_ROOT / "capabilities" / "gateway.py").read_text(encoding="utf-8")
    dispatch = (SOURCE_ROOT / "capabilities" / "dispatch.py").read_text(encoding="utf-8")
    service = (SOURCE_ROOT / "workers" / "service.py").read_text(encoding="utf-8")
    proof = (SOURCE_ROOT / "workers" / "proof.py").read_text(encoding="utf-8")

    assert "def playbook_definition_fingerprint(" in engine
    assert "def _definition_fingerprint(" not in engine
    assert "_bound_definition_fingerprint" not in gateway + dispatch
    assert "playbook_definition_fingerprint(" in gateway
    assert "playbook_definition_fingerprint(" in dispatch
    assert "def generation_worker_authority_fingerprint(" in service
    assert "def _authority_fingerprint(" not in service
    assert "generation_worker_authority_fingerprint(" in proof


def test_gateway_adapter_and_proof_are_provider_neutral_operational_modules() -> None:
    paths = (
        SOURCE_ROOT / "capabilities" / "worker_adapter.py",
        SOURCE_ROOT / "workers" / "proof.py",
        SOURCE_ROOT / "ports" / "worker.py",
    )
    forbidden = (
        "openai",
        "anthropic",
        "deepseek",
        "google.generativeai",
        "study_agent.adapters",
        "study_agent.artifacts",
        "study_agent.flashcards",
        "cardine.exams",
        "study_agent.state.events",
        "study_agent.state.store",
        "study_agent.ports.model",
    )
    violations = [
        f"{path.relative_to(PROJECT_ROOT)} imports {item}"
        for path in paths
        for item in _imports(path)
        if any(item == prefix or item.startswith(prefix + ".") for prefix in forbidden)
    ]
    assert violations == []


def test_proof_does_not_widen_worker_views_or_public_tools() -> None:
    view_source = (SOURCE_ROOT / "workers" / "view.py").read_text(encoding="utf-8")
    for name in (
        "VerifiedChildExecutionProof",
        "VerifiedChildProofView",
        "technical_model_receipt",
        "tool_outputs",
        "read_dependencies",
    ):
        assert name not in view_source

    names = tuple(item.name for item in public_study_tool_manifests())
    assert names == (
        "citation.resolve",
        "course.get",
        "grounding.ask",
        "session.get_context",
        "session.record_note",
        "source.list",
        "source.search",
    )
    assert not any("worker" in name or "proof" in name for name in names)


def test_proof_has_no_forbidden_raw_or_authority_fields() -> None:
    tree = _tree(SOURCE_ROOT / "workers" / "proof.py")
    field_names = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance((target := node.target), ast.Name)
    }
    assert field_names.isdisjoint(
        {
            "task",
            "task_bytes",
            "inputs",
            "raw_inputs",
            "traces",
            "messages",
            "request",
            "requests",
            "endpoint",
            "headers",
            "credentials",
            "principal_id",
            "write_authority",
        }
    )
