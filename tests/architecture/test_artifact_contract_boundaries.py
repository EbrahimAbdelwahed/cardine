from __future__ import annotations

import ast
from pathlib import Path

from study_agent.artifacts import (
    GeneratedArtifactProvenance,
    StudyArtifactEnvelope,
)
from study_agent.capabilities import (
    ASSESS_UNDERSTANDING_MANIFEST,
    EXPLAIN_CONCEPT_MANIFEST,
    TutorCapabilityId,
)
from study_agent.domain import ArtifactId, ArtifactReadDependency, VerifiedMediaRef
from study_agent.pedagogy import PedagogicalProfileCatalog, ProfileSelectionReceipt
from study_agent.tools import public_study_tool_manifests

ROOT = Path(__file__).parents[2] / "src" / "study_agent"

TOOL_SNAPSHOT = (
    (
        "citation.resolve",
        "1.0.0",
        "1b7f74005dfaee7879322edd8f60ca7892e9e20d024b1807aafac0af5ecfcb71",
    ),
    (
        "course.get",
        "1.0.0",
        "ccfeca393bc56a3de08abc0d91ef68a9104255a43f0d428312c46d841008934b",
    ),
    (
        "grounding.ask",
        "1.0.0",
        "7452676719dfcfa31f4824f45ed1d1a417dcbbb7522494522955f762850eec0e",
    ),
    (
        "session.get_context",
        "1.0.0",
        "ea60a58728e9d9d96c11fa3cc69bc85e73e7fcbbb7fab9ce2b7821229734d3ba",
    ),
    (
        "session.record_note",
        "1.0.0",
        "e4d3e7446a82e4570c67abb9da9ddab70c60e14350e412c18065bf33c1b04986",
    ),
    (
        "source.list",
        "1.0.0",
        "387d629a6bd69ffad34dae41a5fe1c88f2619bda4637fcfeb3be96ac21cef24b",
    ),
    (
        "source.search",
        "1.0.0",
        "f66b9bf4a901367ab9867efeab53bd749218e8d01f1639282300abb55b2f5c97",
    ),
)

CAPABILITY_SNAPSHOT = (
    (
        "assess_understanding",
        "1.0.0",
        "d49d55b2efa04f642fbd08e84204b50f471422335d01dc283bfa26da4753b1d9",
    ),
    (
        "explain_concept",
        "1.0.0",
        "6e563b5a2750f8077f3a516ea50a7e938552824afbde7bbebed04563783465c3",
    ),
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_artifact_and_pedagogy_contracts_keep_inward_import_boundaries() -> None:
    paths = (
        ROOT / "domain" / "artifact.py",
        ROOT / "artifacts" / "content.py",
        ROOT / "artifacts" / "identity.py",
        ROOT / "pedagogy" / "profiles.py",
    )
    forbidden = (
        "study_agent.adapters",
        "study_agent.application",
        "study_agent.capabilities",
        "cardine.cli",
        "cardine.courses",
        "study_agent.ingestion",
        "study_agent.playbooks",
        "study_agent.prompts",
        "study_agent.retrieval",
        "study_agent.sessions",
        "study_agent.skills",
        "study_agent.state",
        "study_agent.tools",
        "openai",
        "anthropic",
        "deepseek",
        "httpx",
        "requests",
        "socket",
        "sqlite3",
    )

    violations = {
        str(path.relative_to(ROOT)): imported
        for path in paths
        for imported in _imports(path)
        if any(
            imported == prefix or imported.startswith(prefix + ".")
            for prefix in forbidden
        )
    }
    assert violations == {}


def test_public_contracts_are_owned_by_domain_artifacts_and_pedagogy() -> None:
    assert ArtifactId.__module__ == "study_agent.domain.identifiers"
    assert ArtifactReadDependency.__module__ == "study_agent.domain.artifact"
    assert VerifiedMediaRef.__module__ == "study_agent.domain.artifact"
    assert StudyArtifactEnvelope.__module__ == "study_agent.artifacts.content"
    assert GeneratedArtifactProvenance.__module__ == "study_agent.artifacts.identity"
    assert PedagogicalProfileCatalog.__module__ == "study_agent.pedagogy.profiles"
    assert ProfileSelectionReceipt.__module__ == "study_agent.pedagogy.profiles"


def test_contract_modules_add_no_runtime_state_or_effect_owner() -> None:
    paths = (
        ROOT / "domain" / "artifact.py",
        ROOT / "artifacts" / "content.py",
        ROOT / "artifacts" / "identity.py",
        ROOT / "pedagogy" / "profiles.py",
    )
    forbidden_calls = {
        "append",
        "apply",
        "commit",
        "execute",
        "invoke",
        "publish",
        "register",
        "save",
        "write",
    }
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in forbidden_calls
            ):
                violations.append(f"{path.relative_to(ROOT)} defines {node.name}")
    assert violations == []


def test_exact_seven_tools_and_two_ordinary_gateway_capabilities_are_unchanged() -> None:
    tools = tuple(
        (manifest.name, manifest.version, manifest.fingerprint)
        for manifest in public_study_tool_manifests()
    )
    capabilities = tuple(
        (manifest.id.value, str(manifest.version), manifest.fingerprint)
        for manifest in sorted(
            (ASSESS_UNDERSTANDING_MANIFEST, EXPLAIN_CONCEPT_MANIFEST),
            key=lambda item: item.id.value,
        )
    )

    assert tools == TOOL_SNAPSHOT
    assert capabilities == CAPABILITY_SNAPSHOT
    assert tuple(TutorCapabilityId) == (
        TutorCapabilityId.EXPLAIN_CONCEPT,
        TutorCapabilityId.ASSESS_UNDERSTANDING,
        TutorCapabilityId.PROPOSE_FLASHCARDS,
        TutorCapabilityId.ANALYZE_EXAM_SAMPLE,
        TutorCapabilityId.GRADE_RESPONSE,
    )
