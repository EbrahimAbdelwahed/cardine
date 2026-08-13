from __future__ import annotations

import ast
import inspect
from pathlib import Path

from study_agent.artifacts import (
    ARTIFACT_EVENT_TYPES,
    DECISION_RECORDED,
    PROPOSAL_BATCH_RECORDED,
    ArtifactService,
)
from study_agent.capabilities import (
    ASSESS_UNDERSTANDING_MANIFEST,
    EXPLAIN_CONCEPT_MANIFEST,
)
from study_agent.ports import (
    ArtifactViewPort,
    ServiceDecisionPolicyPort,
    SourceCommitmentLookupPort,
    VerifiedGeneratedBatchPort,
)
from study_agent.tools import public_study_tool_manifests

ROOT = Path(__file__).parents[2] / "src" / "study_agent"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_lifecycle_modules_keep_inward_provider_neutral_boundaries() -> None:
    paths = (
        *(
            ROOT / "artifacts" / name
            for name in (
                "contracts.py",
                "events.py",
                "projection.py",
                "service.py",
                "view.py",
            )
        ),
        ROOT / "ports" / "artifact.py",
    )
    forbidden = (
        "study_agent.adapters",
        "study_agent.application",
        "study_agent.capabilities",
        "cardine.cli",
        "study_agent.ingestion",
        "study_agent.playbooks",
        "study_agent.prompts",
        "study_agent.retrieval",
        "study_agent.skills",
        "study_agent.tools",
        "openai",
        "anthropic",
        "deepseek",
        "httpx",
        "requests",
        "sqlite3",
    )
    violations = {
        f"{path.relative_to(ROOT)} imports {imported}"
        for path in paths
        for imported in _imports(path)
        if any(
            imported == prefix or imported.startswith(prefix + ".")
            for prefix in forbidden
        )
    }
    assert violations == set()


def test_projection_is_the_only_artifact_event_state_owner() -> None:
    projection = ROOT / "artifacts" / "projection.py"
    production = {
        name: (ROOT / "artifacts" / name).read_text(encoding="utf-8")
        for name in ("contracts.py", "events.py", "service.py", "view.py")
    }
    projection_source = projection.read_text(encoding="utf-8")

    assert "def reduce_proposal_batch_recorded" in projection_source
    assert "def reduce_decision_recorded" in projection_source
    assert "def register_artifact_events" in projection_source
    for name, source in production.items():
        assert "def reduce_" not in source, name
        assert "register_event(" not in source, name
    assert 'state.get("study_artifacts"' not in production["service.py"]
    assert "Projection" not in production["service.py"]


def test_public_commands_and_ports_are_narrow_authority_safe_surfaces() -> None:
    assert tuple(inspect.signature(ArtifactService.record_generated).parameters) == (
        "self",
        "run_id",
        "context",
        "expected_sequence",
    )
    assert tuple(inspect.signature(ArtifactService.apply_service_decision).parameters) == (
        "self",
        "revision_id",
        "context",
        "expected_sequence",
    )
    assert set(dir(VerifiedGeneratedBatchPort)) >= {"recover"}
    assert tuple(inspect.signature(VerifiedGeneratedBatchPort.recover).parameters) == (
        "self",
        "run_id",
        "context",
    )
    assert set(dir(SourceCommitmentLookupPort)) >= {"contains"}
    assert set(dir(ServiceDecisionPolicyPort)) >= {"decide"}
    assert set(dir(ArtifactViewPort)) >= {"get", "command_fingerprint"}
    assert not hasattr(VerifiedGeneratedBatchPort, "store")
    assert not hasattr(ArtifactViewPort, "append")


def test_exact_two_events_seven_tools_and_two_tutor_capabilities_are_unchanged() -> None:
    assert frozenset({PROPOSAL_BATCH_RECORDED, DECISION_RECORDED}) == ARTIFACT_EVENT_TYPES
    assert tuple(
        (manifest.name, manifest.version, manifest.fingerprint)
        for manifest in public_study_tool_manifests()
    ) == (
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
    assert tuple(
        (manifest.id.value, str(manifest.version), manifest.fingerprint)
        for manifest in (
            ASSESS_UNDERSTANDING_MANIFEST,
            EXPLAIN_CONCEPT_MANIFEST,
        )
    ) == (
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
