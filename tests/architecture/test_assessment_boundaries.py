from __future__ import annotations

import ast
from pathlib import Path

from study_agent.assessments import ASSESSMENT_EVENT_TYPES
from study_agent.tools import public_study_tool_manifests

ROOT = Path(__file__).parents[2] / "src" / "study_agent"
CARDINE_ROOT = Path(__file__).parents[2] / "src" / "cardine"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_assessment_owner_keeps_inward_provider_neutral_boundaries() -> None:
    paths = (
        *(
            ROOT / "assessments" / name
            for name in ("contracts.py", "events.py", "projection.py", "view.py")
        ),
        ROOT / "ports" / "assessment.py",
        ROOT / "domain" / "assessment.py",
    )
    forbidden = (
        "study_agent.adapters",
        "study_agent.application",
        "study_agent.capabilities",
        "cardine.cli",
        "study_agent.models",
        "study_agent.playbooks",
        "study_agent.prompts",
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
        if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden)
    }
    assert violations == set()


def test_projection_alone_owns_assessment_event_state_and_defines_no_behavior_service() -> None:
    sources = {
        name: (ROOT / "assessments" / name).read_text(encoding="utf-8")
        for name in ("contracts.py", "events.py", "projection.py", "view.py")
    }
    assert "def reduce_item_presented" in sources["projection.py"]
    assert "def register_assessment_events" in sources["projection.py"]
    for name in ("contracts.py", "events.py", "view.py"):
        assert "def reduce_" not in sources[name]
        assert "register_event(" not in sources[name]
    combined = "\n".join(sources.values()).lower()
    assert "class assessmentservice" not in combined
    assert "def grade_response" not in combined
    assert "modelgateway" not in combined


def test_assessment_contract_has_no_mastery_schedule_or_global_learner_aggregate() -> None:
    contracts = (ROOT / "assessments" / "contracts.py").read_text(encoding="utf-8").lower()
    domain = (ROOT / "domain" / "assessment.py").read_text(encoding="utf-8").lower()
    public_names = ast.parse(contracts)
    class_names = {
        node.name.lower() for node in ast.walk(public_names) if isinstance(node, ast.ClassDef)
    }
    assert not any("mastery" in name or "schedule" in name for name in class_names)
    assert "learnermodel" not in contracts.replace("_", "")
    assert "mastery" not in domain
    assert "schedule" not in domain


def test_registration_is_additive_and_seven_public_study_tools_are_unchanged() -> None:
    assert frozenset(
        {
            "assessment.item_presented",
            "assessment.attempt_recorded",
            "assessment.grade_recorded",
            "assessment.grade_contested",
        }
    ) == ASSESSMENT_EVENT_TYPES
    manifests = public_study_tool_manifests()
    assert len(manifests) == 7
    assert tuple(item.name for item in manifests) == (
        "citation.resolve",
        "course.get",
        "grounding.ask",
        "session.get_context",
        "session.record_note",
        "source.list",
        "source.search",
    )
    for relative in (
        "application/export.py",
        "adapters/sqlite/lifecycle_observer.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "register_artifact_events" in source
        assert "register_assessment_events" in source
        assert source.index("register_artifact_events") < source.rindex(
            "register_assessment_events"
        )
    source = (CARDINE_ROOT / "cli" / "repository.py").read_text(encoding="utf-8")
    assert "register_artifact_events" in source
    assert "register_assessment_events" in source
    assert source.index("register_artifact_events") < source.rindex(
        "register_assessment_events"
    )
