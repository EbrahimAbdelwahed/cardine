from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from study_agent.domain._validation import JsonValue, freeze_json
from tests.support.parity_driver import CASE_NAMES, load_input, run_case
from tests.support.parity_normalization import TMP_POINTERS, normalize_parity

ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "tests/parity/golden/manifest.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_is_complete_and_inputs_and_outputs_are_frozen() -> None:
    manifest = _read_json(MANIFEST_PATH)
    assert manifest["manifest_schema_version"] == 1
    assert manifest["normalization"]["json_pointer_replacements"] == sorted(TMP_POINTERS)
    cases = manifest["cases"]
    assert len(cases) == len(CASE_NAMES)
    assert {case["case"] for case in cases} == set(CASE_NAMES)
    for case in cases:
        input_path = ROOT / case["input"]
        normalized = ROOT / case["normalized_output"]
        assert input_path.is_file(), case
        assert normalized.is_file(), case
        assert case["input_sha256"] == _sha256(input_path)
        assert case["normalized_sha256"] == _sha256(normalized)
        assert case["schema_version"] == 1


def test_frozen_outputs_match_real_baseline_driver() -> None:
    manifest = _read_json(MANIFEST_PATH)
    for case in manifest["cases"]:
        expected = freeze_json(_read_json(ROOT / case["normalized_output"]))
        actual = normalize_parity(run_case(case["case"], load_input(case["case"])))
        assert actual == expected, case["case"]
        assert "events" in actual and "effect_counters" in actual


def test_normalizer_replaces_only_exact_tmp_pointers() -> None:
    vector = cast(
        JsonValue,
        {
            "runtime": {
                "tmp_root": "/private/tmp/a",
                "events_path": "/private/tmp/a/events.sqlite3",
            },
            "event": {
                "event_id": "evt-semantic",
                "occurred_at": "2026-08-09T12:00:00Z",
                "timestamp": "semantic-timestamp",
                "path": "source/path.md",
                "temporary_path": "/private/tmp/fixture",
                "process_id": 42,
            },
        },
    )
    normalized = cast(dict[str, object], normalize_parity(vector))
    assert normalized["runtime"] == {"tmp_root": "<TMP_ROOT>", "events_path": "<TMP_ROOT>"}
    assert normalized["event"] == cast(dict[str, object], vector)["event"]
