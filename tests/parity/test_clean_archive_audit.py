from __future__ import annotations

import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).parents[2]
SNAPSHOT = ROOT / "tests/parity/golden/baseline-dirty-snapshot.json"


def test_clean_archive_audit_is_self_contained_and_keeps_dirty_baseline_rows(
    tmp_path: Path,
) -> None:
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
    result = subprocess.run(
        [sys.executable, "scripts/audit_harness_ownership.py", "--check"],
        cwd=clean_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ownership audit: OK (322 rows)" in result.stdout
    assert (clean_root / "tests/parity/golden/baseline-dirty-snapshot.json").is_file()
    ledger = (clean_root / "specs/harness-adoption/assets/ownership-ledger.csv").read_text()
    assert "src/study_agent/hosts/flashcard_routing.py" in ledger
    assert "48a5f5d2bbea11adc2af6e24cf65c8697039eabfc83648f3d3a9c0601fd444b5" in ledger
