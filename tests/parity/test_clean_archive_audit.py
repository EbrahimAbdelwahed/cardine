from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def _clean_archive(tmp_path: Path) -> Path:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    clean_root = tmp_path / "clean-archive"
    clean_root.mkdir()
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as tar:
        tar.extractall(clean_root, filter="data")
    return clean_root


def test_clean_archive_audit_is_self_contained_and_keeps_dirty_baseline_rows(
    tmp_path: Path,
) -> None:
    clean_root = _clean_archive(tmp_path)
    result = subprocess.run(
        [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
        cwd=clean_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ownership audit: OK (322 rows)" in result.stdout
    classification = json.loads(
        (clean_root / "tests/parity/ownership-classification.json").read_text(encoding="utf-8")
    )
    rows = {row["path"]: row for row in classification["rows"]}
    assert rows["src/study_agent/hosts/flashcard_routing.py"]["baseline_state"] == "untracked"
    assert rows["src/study_agent/hosts/flashcard_routing.py"]["sha256"] == (
        "48a5f5d2bbea11adc2af6e24cf65c8697039eabfc83648f3d3a9c0601fd444b5"
    )


def test_classification_rejects_mutated_successor_and_blank_columns(tmp_path: Path) -> None:
    clean_root = _clean_archive(tmp_path)
    path = clean_root / "tests/parity/ownership-classification.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["rows"][0]["replacement_import_or_path"] = "study_agent.api"
    path.write_text(json.dumps(raw), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
        cwd=clean_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "forbidden bare study_agent.api" in result.stderr


def test_post_baseline_byte_commitments_reject_further_drift(tmp_path: Path) -> None:
    clean_root = _clean_archive(tmp_path)
    path = clean_root / "src/cardine/hosts/source_grounding.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# unexpected drift\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
        cwd=clean_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "CA-02 transition sha256 mismatch" in result.stderr


def test_recovery_custody_rejects_mutated_core_and_new_unclassified_file(
    tmp_path: Path,
) -> None:
    clean_root = _clean_archive(tmp_path)
    protected = (
        "src/cardine/demo/browser.py",
        "src/study_agent/domain/__init__.py",
        "src/study_agent/tutor_snapshot/reader.py",
        "src/study_agent/ingestion/preparation.py",
        "src/cardine/demo/browser.js",
    )
    for relative in protected:
        path = clean_root / relative
        original = path.read_bytes()
        path.write_bytes(original + b"\n# unexpected recovery drift\n")
        result = subprocess.run(
            [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
            cwd=clean_root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert f"recovery overlay sha256 mismatch for {relative}" in result.stderr
        path.write_bytes(original)

    new_path = clean_root / "src/study_agent/ingestion/unreviewed.py"
    new_path.write_text("value = 1\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
        cwd=clean_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "classification is missing current path" in result.stderr


def test_student_journal_custody_rejects_each_changed_source_and_retired_source(
    tmp_path: Path,
) -> None:
    clean_root = _clean_archive(tmp_path)
    manifest = json.loads(
        (clean_root / "tests/parity/student-journal-overlay.json").read_text(encoding="utf-8")
    )
    for row in manifest["rows"]:
        path = clean_root / row["path"]
        original = path.read_bytes()
        path.write_bytes(original + b"\n# unapproved journal drift\n")
        result = subprocess.run(
            [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
            cwd=clean_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0, row["path"]
        assert f"recovery overlay sha256 mismatch for {row['path']}" in result.stderr
        path.write_bytes(original)
    for relative in manifest["removed"]:
        path = clean_root / relative
        path.write_text("# unexpectedly restored source\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
            cwd=clean_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert f"student journal retired source is present: {relative}" in result.stderr
        path.unlink()


def test_student_journal_custody_rejects_missing_binding(tmp_path: Path) -> None:
    clean_root = _clean_archive(tmp_path)
    path = clean_root / "tests/parity/student-journal-overlay.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["rows"].pop()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
        cwd=clean_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "student journal overlay has incomplete scope" in result.stderr


def test_student_journal_custody_cannot_add_an_unapproved_path(tmp_path: Path) -> None:
    clean_root = _clean_archive(tmp_path)
    path = clean_root / "tests/parity/student-journal-overlay.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["rows"].append({"path": "src/study_agent/unapproved.py", "sha256": "0" * 64})
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
        cwd=clean_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "student journal overlay has invalid binding" in result.stderr


def test_new_derived_module_commitment_rejects_drift(tmp_path: Path) -> None:
    clean_root = _clean_archive(tmp_path)
    path = clean_root / "src/study_agent/flashcards/semantic.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# unexpected drift\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
        cwd=clean_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "post-baseline addition sha256 mismatch" in result.stderr


def test_new_unclassified_core_file_remains_rejected(tmp_path: Path) -> None:
    clean_root = _clean_archive(tmp_path)
    path = clean_root / "src/study_agent/knowledge/unregistered.py"
    path.write_text("# unregistered core module\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
        cwd=clean_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert (
        "classification is missing current path: src/study_agent/knowledge/unregistered.py"
        in result.stderr
    )


@pytest.mark.parametrize(
    "filename",
    [
        "selected-lesson-notes-overlay.json",
        "structure-lesson-notes-overlay.json",
    ],
)
def test_selected_notes_custody_rejects_drift_and_scope_changes(
    tmp_path: Path, filename: str
) -> None:
    clean_root = _clean_archive(tmp_path)
    overlay = clean_root / "tests/parity" / filename
    original = overlay.read_text(encoding="utf-8")
    raw = json.loads(original)
    for mutate in ("missing", "foreign", "drift"):
        changed = json.loads(original)
        if mutate == "missing":
            changed["rows"].pop()
        elif mutate == "foreign":
            changed["rows"][0]["path"] = "src/study_agent/domain/provenance.py"
        else:
            changed["rows"][0]["sha256"] = "0" * 64
        overlay.write_text(json.dumps(changed), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
            cwd=clean_root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, mutate
    overlay.write_text(original, encoding="utf-8")
    for row in raw["rows"]:
        path = clean_root / row["path"]
        original_bytes = path.read_bytes()
        path.write_bytes(original_bytes + b"\n/* unexpected drift */\n")
        result = subprocess.run(
            [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
            cwd=clean_root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, row["path"]
        assert "sha256 mismatch" in result.stderr
        path.write_bytes(original_bytes)
