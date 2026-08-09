"""Validate the reviewed CA-01 ownership inventory and its CSV projection.

``tests/parity/ownership-classification.json`` is the human-reviewed source of
truth.  The CSV is intentionally only a transport ledger: this checker does
not infer ownership from directory prefixes or catch-all rules.  A clean
archive can therefore verify every row, including explicitly declared dirty
baseline-only paths, without requiring those paths to exist in the archive.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "specs/harness-adoption/assets/ownership-ledger.csv"
CLASSIFICATION = ROOT / "tests/parity/ownership-classification.json"
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


def _declared_package_data(config: Mapping[str, object]) -> set[str]:
    tool = config.get("tool")
    setuptools = tool.get("setuptools") if isinstance(tool, Mapping) else None
    package_data = setuptools.get("package-data") if isinstance(setuptools, Mapping) else None
    if not isinstance(package_data, Mapping):
        return set()
    paths: set[str] = set()
    for package, patterns in package_data.items():
        if not isinstance(package, str) or not isinstance(patterns, list):
            raise ValueError("package-data declarations must map package names to lists")
        package_root = SOURCE_ROOT / package.replace(".", "/")
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError(f"package-data pattern for {package!r} is blank")
            matches = tuple(path for path in package_root.glob(pattern) if path.is_file())
            if not matches:
                raise ValueError(f"package-data pattern has no file: {package}={pattern}")
            paths.update(path.relative_to(ROOT).as_posix() for path in matches)
    return paths


def _entry_point_targets(config: Mapping[str, object]) -> dict[str, str]:
    project = config.get("project")
    scripts = project.get("scripts") if isinstance(project, Mapping) else None
    if not isinstance(scripts, Mapping) or not scripts:
        raise ValueError("pyproject is missing [project.scripts]")
    return {f"entrypoint:{name}": str(target) for name, target in scripts.items()}


def _source_paths() -> set[str]:
    return {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src/study_agent").rglob("*")
        if path.is_file() and "/__pycache__/" not in path.as_posix() and path.suffix != ".pyc"
    }


def _load_classification() -> list[dict[str, str]]:
    if not CLASSIFICATION.is_file():
        raise ValueError(f"missing reviewed classification: {CLASSIFICATION}")
    raw = json.loads(CLASSIFICATION.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise ValueError("classification must declare schema_version 1")
    if tuple(raw.get("columns", ())) != FIELDS:
        raise ValueError(f"classification columns must be exactly {FIELDS}")
    rows = raw.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("classification rows must be a non-empty list")
    loaded: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(rows, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"classification row {index} is not an object")
        missing = [
            field
            for field in FIELDS
            if not isinstance(item.get(field), str) or not item[field].strip()
        ]
        if missing:
            raise ValueError(f"classification row {index} has blank fields: {', '.join(missing)}")
        row = {field: str(item[field]) for field in FIELDS}
        baseline_state = item.get("baseline_state", "clean")
        if baseline_state not in {"clean", "modified", "untracked"}:
            raise ValueError(f"classification row {index} has invalid baseline_state")
        row["baseline_state"] = str(baseline_state)
        path = row["path"]
        if path in seen:
            raise ValueError(f"duplicate classification path: {path}")
        if not (path.startswith("src/study_agent/") or path.startswith("entrypoint:")):
            raise ValueError(f"classification path is outside the owned universe: {path}")
        if row["disposition"] not in DISPOSITIONS:
            raise ValueError(f"classification row {index} has unknown disposition")
        digest = row["sha256"]
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"classification row {index} has invalid sha256")
        if row["replacement_import_or_path"] == "study_agent.api":
            raise ValueError(
                f"classification row {index} uses the forbidden bare study_agent.api successor"
            )
        seen.add(path)
        loaded.append(row)
    return loaded


def _load_rows() -> list[dict[str, str]]:
    if not LEDGER.is_file():
        raise ValueError(f"missing ownership ledger: {LEDGER}")
    with LEDGER.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f"ledger columns must be exactly {FIELDS}")
        return [dict(row) for row in reader]


def _digest(path: str, targets: Mapping[str, str]) -> str:
    if path.startswith("entrypoint:"):
        return hashlib.sha256(f"{path}\0{targets[path]}".encode()).hexdigest()
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def validate(*, live: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        reviewed = _load_classification()
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        targets = _entry_point_targets(config)
        current_paths = _source_paths() | _declared_package_data(config) | set(targets)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        return [f"cannot derive ownership universe: {error}"]
    reviewed_by_path = {row["path"]: row for row in reviewed}
    current_clean = {
        path
        for path in current_paths
        if reviewed_by_path.get(path, {}).get("baseline_state", "clean") == "clean"
    }
    baseline_only = set(reviewed_by_path) - current_paths
    for path in sorted(current_paths - set(reviewed_by_path)):
        errors.append(f"classification is missing current path: {path}")
    for path in sorted(baseline_only):
        if reviewed_by_path[path].get("baseline_state", "clean") == "clean":
            errors.append(f"classification has undeclared baseline-only path: {path}")
    for path in sorted(current_clean):
        try:
            if _digest(path, targets) != reviewed_by_path[path]["sha256"]:
                errors.append(f"classification sha256 mismatch for committed path: {path}")
        except OSError as error:
            errors.append(f"cannot hash classified path {path}: {error}")
    if live:
        for path, row in reviewed_by_path.items():
            if row.get("baseline_state", "clean") in {"modified", "untracked"}:
                candidate = ROOT / path
                if not candidate.is_file():
                    errors.append(f"live baseline file is absent: {path}")
                elif _digest(path, targets) != row["sha256"]:
                    errors.append(f"live baseline sha256 drift: {path}")
    try:
        actual = _load_rows()
    except (OSError, ValueError) as error:
        return [str(error)]
    if len(reviewed) != 322:
        errors.append(f"classification must contain 322 rows, found {len(reviewed)}")
    if len(actual) != len(reviewed):
        errors.append(
            f"ledger row count {len(actual)} does not match classification {len(reviewed)}"
        )
    actual_by_path = {row.get("path", ""): row for row in actual}
    for path in sorted(set(reviewed_by_path) - set(actual_by_path)):
        errors.append(f"ledger is missing classified path: {path}")
    for path in sorted(set(actual_by_path) - set(reviewed_by_path)):
        errors.append(f"ledger has unreviewed path: {path}")
    for path in sorted(set(reviewed_by_path) & set(actual_by_path)):
        expected = reviewed_by_path[path]
        row = actual_by_path[path]
        for field in FIELDS:
            if row.get(field) != expected[field]:
                errors.append(f"ledger {field} mismatch for {path}")
        if (
            row.get("disposition") == "LEGACY_ORACLE_THEN_REMOVE"
            and row.get("removal_slice") != "CA-10"
        ):
            errors.append(f"legacy row {path} must remove at CA-10")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="validate the reviewed classification and ledger"
    )
    parser.add_argument(
        "--live", action="store_true", help="also check explicitly recorded dirty baseline hashes"
    )
    args = parser.parse_args()
    errors = validate(live=args.live)
    if errors:
        for error in errors:
            print(f"ownership audit: {error}", file=sys.stderr)
        return 1
    print("ownership audit: OK (322 rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
