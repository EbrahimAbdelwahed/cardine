from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

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
