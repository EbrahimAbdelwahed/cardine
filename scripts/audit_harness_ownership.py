"""Mechanically validate the CA-01 one-owner ledger.

The ledger is evidence for the expansion phase, not a source relocation tool.
This check derives its expected universe from tracked ``src/study_agent``
files, setuptools package-data declarations, and project console scripts so a
row cannot be silently omitted or repeated.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "specs/harness-adoption/assets/ownership-ledger.csv"
SOURCE_ROOT = ROOT / "src"
DISPOSITIONS = {"HARNESS_IMPORT", "CARDINE_OWNER", "LEGACY_ORACLE_THEN_REMOVE"}
FIELDS = (
    "path",
    "sha256",
    "disposition",
    "terminal_owner",
    "replacement_import_or_path",
    "first_consuming_slice",
    "removal_slice",
)


def _source_paths() -> set[str]:
    """Return every current source/package-data file, excluding interpreter caches."""

    return {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src/study_agent").rglob("*")
        if path.is_file() and "/__pycache__/" not in path.as_posix() and path.suffix != ".pyc"
    }


def _declared_package_data(config: Mapping[str, object]) -> set[str]:
    tool = config.get("tool")
    if not isinstance(tool, Mapping):
        return set()
    setuptools = tool.get("setuptools")
    if not isinstance(setuptools, Mapping):
        return set()
    package_data = setuptools.get("package-data")
    if not isinstance(package_data, Mapping):
        return set()
    paths: set[str] = set()
    for package, raw_patterns in package_data.items():
        if not isinstance(package, str) or not isinstance(raw_patterns, list):
            raise ValueError("package-data declarations must map package names to lists")
        package_root = SOURCE_ROOT / package.replace(".", "/")
        for pattern in raw_patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError(f"package-data pattern for {package!r} is blank")
            matches = tuple(path for path in package_root.glob(pattern) if path.is_file())
            if not matches:
                raise ValueError(f"package-data pattern has no file: {package}={pattern}")
            for path in matches:
                paths.add(path.relative_to(ROOT).as_posix())
    return paths


def _entry_point_paths(config: Mapping[str, object]) -> dict[str, str]:
    project = config.get("project")
    if not isinstance(project, Mapping):
        raise ValueError("pyproject is missing [project]")
    scripts = project.get("scripts")
    if not isinstance(scripts, Mapping) or not scripts:
        raise ValueError("pyproject is missing [project.scripts]")
    return {f"entrypoint:{name}": str(target) for name, target in scripts.items()}


def _expected_rows() -> tuple[dict[str, str], ...]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    paths = _source_paths() | _declared_package_data(config)
    entry_points = _entry_point_paths(config)
    expected: list[dict[str, str]] = []
    for path in sorted(paths):
        expected.append(_row_for(path, None))
    for path, target in sorted(entry_points.items()):
        expected.append(_row_for(path, target))
    return tuple(expected)


def _digest(path: str, entry_point_target: str | None) -> str:
    if entry_point_target is not None:
        payload = f"{path}\0{entry_point_target}".encode()
    else:
        payload = (ROOT / path).read_bytes()
    return hashlib.sha256(payload).hexdigest()


def _is_cardine_owner(path: str) -> bool:
    if path.startswith("entrypoint:"):
        return not path.removeprefix("entrypoint:").startswith("study-agent")
    if path.startswith(
        ("src/study_agent/demo/", "src/study_agent/diagnostics/", "src/study_agent/hosts/")
    ):
        return True
    if path.startswith(
        ("src/study_agent/courses/", "src/study_agent/exams/", "src/study_agent/feedback/")
    ):
        return True
    if path.startswith("src/study_agent/adapters/host/"):
        return True
    return path in {
        "src/study_agent/adapters/model/openai_luna.py",
        "src/study_agent/adapters/model/tutor_decision.py",
        "src/study_agent/application/conversation_turn.py",
        "src/study_agent/application/capability_completion.py",
        "src/study_agent/application/flashcard_profile_selection.py",
        "src/study_agent/application/flashcard_proposals.py",
        "src/study_agent/application/grounding_ask.py",
        "src/study_agent/application/study_readiness.py",
        "src/study_agent/application/tool_surface.py",
        "src/study_agent/cli/__init__.py",
        "src/study_agent/cli/__main__.py",
        "src/study_agent/cli/commands.py",
        "src/study_agent/cli/config.py",
        "src/study_agent/cli/lifecycle.py",
        "src/study_agent/cli/main.py",
        "src/study_agent/cli/output.py",
        "src/study_agent/cli/registry.py",
        "src/study_agent/cli/repository.py",
        "src/study_agent/domain/course.py",
        "src/study_agent/domain/study_context.py",
    }


def _is_legacy_oracle(path: str) -> bool:
    # Evaluation package markers are baseline-only bookkeeping; they are not
    # part of the released Harness facade or the Cardine product namespace.
    return path.startswith("src/study_agent/evals/")


def _row_for(path: str, entry_point_target: str | None) -> dict[str, str]:
    if _is_legacy_oracle(path):
        disposition = "LEGACY_ORACLE_THEN_REMOVE"
        owner = "legacy-oracle"
        replacement = f"tests/parity/golden/fixtures/{Path(path).stem}.json"
        first_slice = "CA-04"
    elif _is_cardine_owner(path):
        disposition = "CARDINE_OWNER"
        owner = "cardine"
        if path.startswith("entrypoint:"):
            replacement = path.removeprefix("entrypoint:")
        else:
            replacement = path.replace("src/study_agent/", "src/cardine/", 1)
        first_slice = "CA-02"
    else:
        disposition = "HARNESS_IMPORT"
        owner = "study-agent-harness"
        replacement = "study_agent.api"
        first_slice = "CA-04"
    removal = "CA-02" if path.startswith("entrypoint:study-agent") else "CA-10"
    return {
        "path": path,
        "sha256": _digest(path, entry_point_target),
        "disposition": disposition,
        "terminal_owner": owner,
        "replacement_import_or_path": replacement,
        "first_consuming_slice": first_slice,
        "removal_slice": removal,
    }


def _load_rows() -> list[dict[str, str]]:
    if not LEDGER.is_file():
        raise ValueError(f"missing ownership ledger: {LEDGER}")
    with LEDGER.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f"ledger columns must be exactly {FIELDS}")
        return [dict(row) for row in reader]


def validate() -> list[str]:
    errors: list[str] = []
    try:
        expected = _expected_rows()
    except (OSError, ValueError) as error:
        return [f"cannot derive ownership universe: {error}"]
    try:
        actual = _load_rows()
    except (OSError, ValueError) as error:
        return [str(error)]
    seen: set[str] = set()
    for index, row in enumerate(actual, start=2):
        missing = [field for field in FIELDS if not row.get(field, "").strip()]
        if missing:
            errors.append(f"row {index} has blank fields: {', '.join(missing)}")
        path = row.get("path", "")
        if path in seen:
            errors.append(f"duplicate ledger path: {path}")
        seen.add(path)
        if row.get("disposition") not in DISPOSITIONS:
            errors.append(f"row {index} has unknown disposition: {row.get('disposition')!r}")
        if (
            row.get("disposition") == "LEGACY_ORACLE_THEN_REMOVE"
            and row.get("removal_slice") != "CA-10"
        ):
            errors.append(f"legacy row {path} must remove at CA-10")
        if len(row.get("sha256", "")) != 64:
            errors.append(f"row {index} has invalid sha256")
    expected_by_path = {row["path"]: row for row in expected}
    actual_by_path = {row.get("path", ""): row for row in actual}
    missing = sorted(set(expected_by_path) - set(actual_by_path))
    extra = sorted(set(actual_by_path) - set(expected_by_path))
    if missing:
        errors.append(f"ledger is missing {len(missing)} paths: {missing[:5]}")
    if extra:
        errors.append(f"ledger has {len(extra)} unexpected paths: {extra[:5]}")
    for path in sorted(set(expected_by_path) & set(actual_by_path)):
        expected_row = expected_by_path[path]
        actual_row = actual_by_path[path]
        if expected_row["sha256"] != actual_row.get("sha256"):
            errors.append(f"sha256 mismatch for {path}")
        if expected_row["disposition"] != actual_row.get("disposition"):
            errors.append(f"disposition mismatch for {path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the checked-in ledger")
    parser.parse_args()
    errors = validate()
    if errors:
        for error in errors:
            print(f"ownership audit: {error}", file=sys.stderr)
        return 1
    print(f"ownership audit: OK ({len(_expected_rows())} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
