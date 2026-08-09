from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from study_agent.domain._validation import JsonValue, freeze_json
from tests.support.parity_normalization import NONDETERMINISTIC_FIELDS, normalize_parity

ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "tests/parity/golden/manifest.json"
REQUIRED_CASES = {
    "replay",
    "source_identity",
    "substrate_identity_lineage",
    "citation_resolution",
    "session_continuation_recovery",
    "artifact_decisions",
    "assessment_presentation",
    "assessment_attempt",
    "assessment_grade",
    "assessment_contest",
    "recall_enrollment",
    "recall_review",
    "recall_due",
    "semantic_export",
    "failure_invalid",
    "failure_stale",
    "failure_unauthorized",
    "failure_not_found",
    "failure_conflict",
}
SEMANTIC_FIELDS = {
    "event_id",
    "event_type",
    "event_schema_version",
    "aggregate_id",
    "sequence",
    "causation_id",
    "correlation_id",
    "provenance",
    "citations",
    "decision",
    "status",
    "error_code",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_is_complete_and_fixture_hashes_are_frozen() -> None:
    manifest = _read_json(MANIFEST_PATH)
    assert manifest["manifest_schema_version"] == 1
    assert set(manifest["normalization"]["removed_fields"]) == NONDETERMINISTIC_FIELDS
    cases = manifest["cases"]
    assert len(cases) == len(REQUIRED_CASES)
    assert {case["case"] for case in cases} == REQUIRED_CASES
    assert len({case["case"] for case in cases}) == len(cases)
    for case in cases:
        fixture = ROOT / case["fixture"]
        normalized = ROOT / case["normalized_output"]
        assert fixture.is_file(), case
        assert normalized.is_file(), case
        assert case["fixture_sha256"] == _sha256(fixture)
        assert case["normalized_sha256"] == _sha256(normalized)
        assert case["schema_version"] == 1


def test_normalized_reports_match_allowlisted_semantic_projection() -> None:
    manifest = _read_json(MANIFEST_PATH)
    for case in manifest["cases"]:
        fixture = freeze_json(_read_json(ROOT / case["fixture"]))
        normalized = freeze_json(_read_json(ROOT / case["normalized_output"]))
        assert normalized == normalize_parity(fixture), case["case"]
        assert set(cast(dict[str, object], normalized)) >= SEMANTIC_FIELDS
        assert not NONDETERMINISTIC_FIELDS.intersection(cast(dict[str, object], normalized))


def test_normalizer_does_not_scrub_unlisted_semantic_names() -> None:
    vector = cast(
        JsonValue,
        {
            "event_id": "evt-semantic",
            "timestamp": "semantic-timestamp",
            "path": "source/path.md",
            "temporary_path": "/private/tmp/fixture",
            "process_id": 42,
        },
    )
    normalized = cast(dict[str, object], normalize_parity(vector))
    assert normalized == {
        "event_id": "evt-semantic",
        "timestamp": "semantic-timestamp",
        "path": "source/path.md",
    }
