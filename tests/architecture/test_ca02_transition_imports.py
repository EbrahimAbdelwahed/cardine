from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.audit_harness_ownership as audit


def test_transition_overlay_has_exact_target_cardinality() -> None:
    rows, entrypoints = audit._load_transition_overlay()

    assert len(rows) == 119
    assert len({row["path"] for row in rows}) == 119
    assert len(entrypoints) == 5
    assert len({row["path"] for row in entrypoints}) == 5


def test_moved_cardine_path_uses_replacement_for_clean_archive_hashing() -> None:
    row = next(
        row
        for row in audit._load_classification()
        if row["path"] == "src/study_agent/demo/browser.js"
    )

    assert audit._current_path(row) == "src/cardine/demo/browser.js"


def test_transition_overlay_rejects_duplicate_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.loads(audit.TRANSITION_OVERLAY.read_text(encoding="utf-8"))
    payload["rows"].append(dict(payload["rows"][0]))
    candidate = tmp_path / "overlay.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(audit, "TRANSITION_OVERLAY", candidate)

    with pytest.raises(ValueError, match="duplicate path"):
        audit._load_transition_overlay()


def test_transition_ast_normalization_ignores_imports_but_catches_value_changes() -> None:
    baseline = "from study_agent.domain import Thing\nvalue = 1\n"
    retargeted = "from cardine.domain import Thing\nvalue = 1\n"
    changed = "from cardine.domain import Thing\nvalue = 2\n"

    assert audit._normalized_ast(baseline, "baseline.py") == audit._normalized_ast(
        retargeted, "retargeted.py"
    )
    assert audit._normalized_ast(baseline, "baseline.py") != audit._normalized_ast(
        changed, "changed.py"
    )


def test_cardine_domain_can_be_imported_before_study_agent() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "assert 'study_agent.domain' not in sys.modules; "
                "from cardine.domain.course import CourseProfile; "
                "from study_agent.domain import CourseProfile as HarnessCourseProfile; "
                "assert CourseProfile is HarnessCourseProfile"
            ),
        ],
        cwd=audit.ROOT / "src",
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_transition_contract_has_exact_exports_and_consumers() -> None:
    exports, consumers = audit._transition_contract()

    assert exports == audit.TRANSITION_EXPORTS
    assert consumers == audit.TRANSITION_CONSUMERS


def test_transition_contract_detects_an_extra_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    seam = root / "src/cardine/_transition/study_agent.py"
    seam.parent.mkdir(parents=True)
    seam.write_text("__all__ = ['CourseId']\n", encoding="utf-8")
    consumer = root / "src/cardine/application/extra.py"
    consumer.parent.mkdir(parents=True)
    consumer.write_text(
        "from cardine._transition import study_agent\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "ROOT", root)

    exports, consumers = audit._transition_contract()

    assert exports == {"CourseId"}
    assert consumers == {"src/cardine/application/extra.py"}
