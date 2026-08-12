from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path

from study_agent.capabilities import (
    PROPOSE_FLASHCARDS_MANIFEST,
    TutorCapabilityId,
    builtin_capability_bindings,
)
from study_agent.domain import ExecutionContext
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.playbooks import ReadDependency
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.tools import public_study_tool_manifests

ROOT = Path(__file__).parents[2] / "src" / "study_agent"
V1 = SemanticVersion.parse("1.0.0")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _keys(value: JsonValue) -> set[str]:
    if isinstance(value, Mapping):
        return set(value) | {
            child
            for item in value.values()
            for child in _keys(item)
        }
    if isinstance(value, tuple):
        return {child for item in value for child in _keys(item)}
    return set()


def test_candidate_contract_is_transient_and_keeps_inward_boundaries() -> None:
    imports = _imports(ROOT / "artifacts" / "candidates.py")
    forbidden = (
        "study_agent.adapters",
        "study_agent.capabilities",
        "study_agent.playbooks",
        "study_agent.prompts",
        "study_agent.skills",
        "study_agent.state",
        "study_agent.tools",
        "openai",
        "anthropic",
        "deepseek",
    )
    assert [
        item
        for item in imports
        if any(item == prefix or item.startswith(prefix + ".") for prefix in forbidden)
    ] == []


def test_dispatcher_depends_on_candidate_codec_but_not_product_or_provider_layers() -> None:
    imports = _imports(ROOT / "capabilities" / "dispatch.py")
    assert "study_agent.artifacts.candidates" in imports
    forbidden = (
        "study_agent.adapters",
        "study_agent.application",
        "cardine.cli",
        "study_agent.sessions",
        "study_agent.state",
        "study_agent.tools",
        "openai",
        "anthropic",
        "deepseek",
    )
    assert [
        item
        for item in imports
        if any(item == prefix or item.startswith(prefix + ".") for prefix in forbidden)
    ] == []


def test_public_flashcard_manifest_is_task_only_and_exports_no_internal_selection() -> None:
    input_properties = PROPOSE_FLASHCARDS_MANIFEST.input_schema["properties"]
    assert isinstance(input_properties, Mapping)
    assert tuple(input_properties) == (
        "query",
        "scope",
        "language",
        "candidate_ceiling",
        "continuation_summary_json",
    )
    forbidden = {
        "profile",
        "profile_selection_receipt",
        "provider",
        "model",
        "credential",
        "api_key",
        "deck",
        "tags",
        "template",
        "status",
        "state",
    }
    assert _keys(PROPOSE_FLASHCARDS_MANIFEST.input_schema).isdisjoint(forbidden)
    assert _keys(PROPOSE_FLASHCARDS_MANIFEST.output_schema).isdisjoint(forbidden)
    assert PROPOSE_FLASHCARDS_MANIFEST.required_authority == ("course:read",)
    assert PROPOSE_FLASHCARDS_MANIFEST.supports_suspension is True


def test_ordinary_gateway_registration_stays_two_and_flashcards_stay_dispatched() -> None:
    def resolver(
        *, context: ExecutionContext, inputs: JsonObject
    ) -> tuple[ReadDependency, ...]:
        del context, inputs
        return ()

    bindings = builtin_capability_bindings(
        explain_dependency_resolver=resolver,
        assess_dependency_resolver=resolver,
        model_adapter=ArtifactReference("fixture_model", V1),
        state_contract=ArtifactReference("event_state", V1),
    )
    assert tuple(binding.manifest.id for binding in bindings) == (
        TutorCapabilityId.EXPLAIN_CONCEPT,
        TutorCapabilityId.ASSESS_UNDERSTANDING,
    )
    assert all(
        binding.manifest.id is not TutorCapabilityId.PROPOSE_FLASHCARDS
        for binding in bindings
    )


def test_exact_seven_public_study_tools_remain_unchanged() -> None:
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
