"""Validate the frozen CA-01/CA-02 ownership inventory and transition map.

``tests/parity/ownership-classification.json`` is the reviewed ownership source
of truth. ``--check`` validates that frozen inventory, its CSV projection, the
CA-02 namespace transition, and the currently required successor paths without
assuming post-CA-02 product code is byte-for-byte immutable forever.

``--live`` adds byte/AST drift checks against the historical CA-01/CA-02
snapshots.  This keeps the historical ownership gate useful without making it
an accidental freeze on later reviewed product work.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import sys
import tomllib
import zipfile
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "specs/harness-adoption/assets/ownership-ledger.csv"
CLASSIFICATION = ROOT / "tests/parity/ownership-classification.json"
TRANSITION_OVERLAY = ROOT / "tests/parity/ca02-transition-overlay.json"
BASELINE_WHEEL = ROOT / "tests/parity/artifacts/cardine-0.2.0-py3-none-any.whl"
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
OVERLAY_FIELDS = ("path", "source_path", "disposition", "transition_sha256")
OVERLAY_DISPOSITIONS = {"HARNESS_IMPORT", "CARDINE_OWNER", "TRANSITION_CONSUMER"}
TRANSITION_CONSUMERS = {
    "src/cardine/domain/course.py",
    "src/cardine/domain/study_context.py",
}
TRANSITION_EXPORTS = {
    "CourseId",
    "EventId",
    "InteractionId",
    "SessionId",
    "StatementId",
    "require_aware",
    "require_text",
}
REVIEWED_NON_IMPORT_AST_VARIANCE = {
    "src/study_agent/application/export.py",
    "src/study_agent/artifacts/verified_batch.py",
    "src/study_agent/capabilities/morphology_flashcards.py",
    "src/study_agent/domain/__init__.py",
    "src/study_agent/flashcards/lesson_worker_service.py",
    "src/study_agent/ports/verified_batch.py",
    "src/study_agent/prompts/morphology_flashcards_v1.py",
    "src/study_agent/tutor_snapshot/reader.py",
    "src/study_agent/workers/proof.py",
}
COPIED_IMPORT_PATHS = {
    "src/study_agent/adapters/memory/host_file.py",
    "src/study_agent/adapters/model/__init__.py",
    "src/study_agent/adapters/sqlite/capability_gap_store.py",
    "src/study_agent/adapters/sqlite/lifecycle_observer.py",
    "src/study_agent/adapters/workarounds/manifest.py",
    "src/study_agent/adapters/workarounds/pdf_markdown.py",
    "src/study_agent/application/__init__.py",
    "src/study_agent/application/export.py",
    "src/study_agent/application/harness.py",
    "src/study_agent/artifacts/verified_batch.py",
    "src/study_agent/domain/__init__.py",
    "src/study_agent/domain/tutor_snapshot.py",
    "src/study_agent/lifecycle/contracts.py",
    "src/study_agent/lifecycle/planner.py",
    "src/study_agent/ports/capability_gap.py",
    "src/study_agent/ports/course.py",
    "src/study_agent/ports/exam.py",
    "src/study_agent/ports/tutor_host.py",
    "src/study_agent/ports/tutor_runner.py",
    "src/study_agent/ports/verified_batch.py",
    "src/study_agent/ports/workaround.py",
    "src/study_agent/sessions/events.py",
    "src/study_agent/sessions/turn_service.py",
    "src/study_agent/tools/builtin.py",
    "src/study_agent/tools/exam_scope_bridge.py",
    "src/study_agent/tools/registry.py",
    "src/study_agent/tutor_snapshot/reader.py",
    "src/study_agent/capabilities/morphology_flashcards.py",
    "src/study_agent/flashcards/lesson_worker_service.py",
    "src/study_agent/prompts/morphology_flashcards_v1.py",
    "src/study_agent/workers/proof.py",
}
EXPECTED_ENTRYPOINTS = {
    "entrypoint:cardine",
    "entrypoint:cardine-demo",
    "entrypoint:cardine-shell",
    "entrypoint:cardine-shell-web",
    "entrypoint:cardine-private-password-hash",
}


def _sha256_text(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _load_classification() -> list[dict[str, str]]:
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
            if not isinstance(item.get(field), str) or not str(item[field]).strip()
        ]
        if missing:
            raise ValueError(f"classification row {index} has blank fields: {', '.join(missing)}")
        row = {field: str(item[field]) for field in FIELDS}
        row["baseline_state"] = str(item.get("baseline_state", "clean"))
        if row["baseline_state"] not in {"clean", "modified", "untracked"}:
            raise ValueError(f"classification row {index} has invalid baseline_state")
        path = row["path"]
        if path in seen:
            raise ValueError(f"duplicate classification path: {path}")
        if not (path.startswith("src/study_agent/") or path.startswith("entrypoint:")):
            raise ValueError(f"classification path is outside the owned universe: {path}")
        if row["disposition"] not in DISPOSITIONS:
            raise ValueError(f"classification row {index} has unknown disposition")
        if not _sha256_text(row["sha256"]):
            raise ValueError(f"classification row {index} has invalid sha256")
        if row["replacement_import_or_path"] == "study_agent.api":
            raise ValueError(
                f"classification row {index} uses the forbidden bare study_agent.api successor"
            )
        seen.add(path)
        loaded.append(row)
    return loaded


def _load_ledger() -> list[dict[str, str]]:
    with LEDGER.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f"ledger columns must be exactly {FIELDS}")
        return [dict(row) for row in reader]


def _load_transition_overlay() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    raw = json.loads(TRANSITION_OVERLAY.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise ValueError("CA-02 transition overlay must declare schema_version 1")
    if tuple(raw.get("columns", ())) != OVERLAY_FIELDS:
        raise ValueError(f"CA-02 transition overlay columns must be exactly {OVERLAY_FIELDS}")

    def load_items(value: object, label: str) -> list[dict[str, str]]:
        if not isinstance(value, list):
            raise ValueError(f"CA-02 transition overlay requires a {label} list")
        loaded: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, item in enumerate(value, start=1):
            if not isinstance(item, Mapping):
                raise ValueError(f"CA-02 {label} row {index} is not an object")
            if any(
                not isinstance(item.get(field), str) or not str(item[field]).strip()
                for field in OVERLAY_FIELDS
            ):
                raise ValueError(f"CA-02 {label} row {index} has blank fields")
            row = {field: str(item[field]) for field in OVERLAY_FIELDS}
            if row["path"] in seen:
                raise ValueError(f"CA-02 {label} duplicate path: {row['path']}")
            if row["disposition"] not in OVERLAY_DISPOSITIONS:
                raise ValueError(f"CA-02 {label} row {index} has unknown disposition")
            if not _sha256_text(row["transition_sha256"]):
                raise ValueError(f"CA-02 {label} row {index} has invalid transition_sha256")
            seen.add(row["path"])
            loaded.append(row)
        return loaded

    return load_items(raw.get("rows"), "transition"), load_items(
        raw.get("entrypoints"), "entrypoint"
    )


def _entrypoint_targets() -> dict[str, str]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = config.get("project")
    scripts = project.get("scripts") if isinstance(project, Mapping) else None
    if not isinstance(scripts, Mapping) or not scripts:
        raise ValueError("pyproject is missing [project.scripts]")
    return {f"entrypoint:{name}": str(target) for name, target in scripts.items()}


def _current_path(row: Mapping[str, str]) -> str:
    replacement = row["replacement_import_or_path"]
    if row["disposition"] == "CARDINE_OWNER" and replacement.startswith("src/"):
        return replacement
    return row["path"]


def _source_paths(reviewed_current_paths: set[str]) -> set[str]:
    """Return the source universe governed by the frozen CA-02 ownership audit.

    ``study_agent`` remains fully visible so unexpected harness-side additions
    cannot hide behind the Cardine exception. Cardine integrations are the one
    reviewed package family excluded from CA-02; other Cardine files are
    included only when they belong to the frozen ownership inventory.
    """

    paths: set[str] = set()
    for package_root in (ROOT / "src/study_agent", ROOT / "src/cardine"):
        paths.update(
            path.relative_to(ROOT).as_posix()
            for path in package_root.rglob("*")
            if path.is_file()
            and "/__pycache__/" not in path.as_posix()
            and path.suffix != ".pyc"
            and "_transition" not in path.parts
            and not path.is_relative_to(ROOT / "src/cardine/integrations")
            and (
                package_root == ROOT / "src/study_agent"
                or path.relative_to(ROOT).as_posix() in reviewed_current_paths
            )
        )
    return paths


def _digest(path: str, targets: Mapping[str, str]) -> str:
    if path.startswith("entrypoint:"):
        return hashlib.sha256(f"{path}\0{targets[path]}".encode()).hexdigest()
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def _baseline_source(path: str) -> str:
    member = path.removeprefix("src/")
    with zipfile.ZipFile(BASELINE_WHEEL) as wheel:
        try:
            return wheel.read(member).decode("utf-8")
        except KeyError as error:
            raise OSError(f"baseline wheel is missing {member}") from error


def _normalized_ast(source: str, filename: str) -> object:
    tree = ast.parse(source, filename=filename)

    class _ImportStripper(ast.NodeTransformer):
        def visit_Import(self, node: ast.Import) -> None:
            return None

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            return None

    tree = _ImportStripper().visit(tree)
    ast.fix_missing_locations(tree)
    return ast.dump(tree, annotate_fields=True, include_attributes=False)


def _transition_contract() -> tuple[set[str], set[str]]:
    seam_path = ROOT / "src/cardine/_transition/study_agent.py"
    seam_tree = ast.parse(seam_path.read_text(encoding="utf-8"), filename=str(seam_path))
    exports: set[str] | None = None
    for node in seam_tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            values = {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            if len(values) != len(node.value.elts):
                raise ValueError("Cardine transition __all__ must contain unique string literals")
            exports = values
    if exports is None:
        raise ValueError("Cardine transition seam is missing a literal __all__")

    consumers: set[str] = set()
    for path in (ROOT / "src/cardine").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("src/cardine/_transition/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "cardine._transition.study_agent":
                consumers.add(relative)
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == "cardine._transition"
                and any(alias.name == "study_agent" for alias in node.names)
            ):
                consumers.add(relative)
            elif isinstance(node, ast.Import) and any(
                alias.name == "cardine._transition.study_agent" for alias in node.names
            ):
                consumers.add(relative)
    return exports, consumers


def _validate_transition(
    reviewed: list[dict[str, str]],
    transition_rows: list[dict[str, str]],
    transition_entrypoints: list[dict[str, str]],
    targets: Mapping[str, str],
    errors: list[str],
    *,
    live: bool,
) -> None:
    try:
        exports, consumers = _transition_contract()
        if exports != TRANSITION_EXPORTS:
            errors.append(f"Cardine transition exports are not exact: {sorted(exports)}")
        if consumers != TRANSITION_CONSUMERS:
            errors.append(f"Cardine transition consumers are not exact: {sorted(consumers)}")
    except (OSError, SyntaxError, ValueError) as error:
        errors.append(f"cannot validate Cardine transition contract: {error}")

    cardine_rows = [row for row in reviewed if row["disposition"] == "CARDINE_OWNER"]
    source_rows = [row for row in cardine_rows if row["path"].startswith("src/")]
    entrypoint_rows = [row for row in cardine_rows if row["path"].startswith("entrypoint:")]
    if len(source_rows) != 86:
        errors.append(f"CA-02 must classify exactly 86 Cardine source targets, found {len(source_rows)}")
    if len(entrypoint_rows) != 5:
        errors.append(f"CA-02 must classify exactly five Cardine entrypoints, found {len(entrypoint_rows)}")
    if {row["path"] for row in entrypoint_rows} != EXPECTED_ENTRYPOINTS:
        errors.append("CA-02 Cardine entrypoint rows are not exact")
    if set(targets) != EXPECTED_ENTRYPOINTS:
        errors.append(f"pyproject must publish exactly five Cardine entrypoints: {sorted(targets)}")
    if any(target.startswith("study_agent.") for target in targets.values()):
        errors.append("Cardine entrypoints must not target study_agent")

    expected_moved = {
        row["path"]: row["replacement_import_or_path"] for row in source_rows
    }
    for old_path, replacement in expected_moved.items():
        if not replacement.startswith("src/cardine/"):
            errors.append(f"Cardine source row has invalid replacement: {old_path} -> {replacement}")
            continue
        if (ROOT / old_path).exists():
            errors.append(f"moved Cardine path remains present: {old_path}")
        if not (ROOT / replacement).is_file():
            errors.append(f"Cardine replacement path is absent: {replacement}")

    expected_paths = set(expected_moved) | COPIED_IMPORT_PATHS | TRANSITION_CONSUMERS
    if len(transition_rows) != 119:
        errors.append(f"CA-02 transition overlay must contain 119 rows, found {len(transition_rows)}")
    if {row["path"] for row in transition_rows} != expected_paths:
        errors.append("CA-02 transition overlay path set is not the reviewed 119-target set")

    by_path = {row["path"]: row for row in transition_rows}
    for old_path, replacement in expected_moved.items():
        row = by_path.get(old_path)
        if row and (row["source_path"] != replacement or row["disposition"] != "CARDINE_OWNER"):
            errors.append(f"CA-02 moved transition binding is invalid: {old_path}")
    for path in COPIED_IMPORT_PATHS:
        row = by_path.get(path)
        if row and (row["source_path"] != path or row["disposition"] != "HARNESS_IMPORT"):
            errors.append(f"CA-02 copied-core transition binding is invalid: {path}")
    for path in TRANSITION_CONSUMERS:
        row = by_path.get(path)
        if row and (row["source_path"] != path or row["disposition"] != "TRANSITION_CONSUMER"):
            errors.append(f"CA-02 transition consumer binding is invalid: {path}")

    for row in transition_rows:
        source_path = row["source_path"]
        candidate = ROOT / source_path
        if not candidate.is_file():
            errors.append(f"CA-02 transition source is absent: {source_path}")
            continue
        if source_path.endswith(".py"):
            try:
                current_source = candidate.read_text(encoding="utf-8")
                ast.parse(current_source, filename=source_path)
            except (OSError, SyntaxError) as error:
                errors.append(f"CA-02 transition source invalid {source_path}: {error}")
                continue
            if (
                live
                and row["disposition"] == "HARNESS_IMPORT"
                and source_path not in REVIEWED_NON_IMPORT_AST_VARIANCE
            ):
                try:
                    baseline_source = _baseline_source(source_path)
                    if _normalized_ast(current_source, source_path) != _normalized_ast(
                        baseline_source, f"baseline:{source_path}"
                    ):
                        errors.append(f"CA-02 copied-core non-import AST mismatch: {source_path}")
                except (OSError, SyntaxError) as error:
                    errors.append(f"CA-02 baseline AST unavailable {source_path}: {error}")
        if live:
            try:
                if _digest(source_path, targets) != row["transition_sha256"]:
                    errors.append(f"CA-02 transition sha256 mismatch for {source_path}")
            except (OSError, KeyError) as error:
                errors.append(f"CA-02 transition source invalid {source_path}: {error}")

    if len(transition_entrypoints) != 5:
        errors.append(
            "CA-02 transition overlay must contain five entrypoint rows, "
            f"found {len(transition_entrypoints)}"
        )
    if {row["path"] for row in transition_entrypoints} != EXPECTED_ENTRYPOINTS:
        errors.append("CA-02 transition entrypoint path set is not exact")
    for row in transition_entrypoints:
        if row["source_path"] != targets.get(row["path"]):
            errors.append(f"CA-02 transition entrypoint target mismatch: {row['path']}")
        if row["disposition"] != "CARDINE_OWNER":
            errors.append(f"CA-02 transition entrypoint disposition mismatch: {row['path']}")
        if live:
            try:
                if _digest(row["path"], targets) != row["transition_sha256"]:
                    errors.append(f"CA-02 transition entrypoint digest mismatch: {row['path']}")
            except KeyError:
                errors.append(f"CA-02 transition entrypoint target is absent: {row['path']}")


def validate(*, live: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        reviewed = _load_classification()
        actual = _load_ledger()
        transition_rows, transition_entrypoints = _load_transition_overlay()
        targets = _entrypoint_targets()
    except (OSError, ValueError, tomllib.TOMLDecodeError, json.JSONDecodeError) as error:
        return [f"cannot derive ownership universe: {error}"]

    if len(reviewed) != 322:
        errors.append(f"classification must contain 322 rows, found {len(reviewed)}")
    if len(actual) != len(reviewed):
        errors.append(f"ledger row count {len(actual)} does not match classification {len(reviewed)}")

    reviewed_by_path = {row["path"]: row for row in reviewed}
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
        if row.get("disposition") == "LEGACY_ORACLE_THEN_REMOVE" and row.get("removal_slice") != "CA-10":
            errors.append(f"legacy row {path} must remove at CA-10")

    # The frozen inventory owns only its reviewed paths. New post-CA-02 modules
    # are intentionally outside this historical universe and must be governed
    # by their own slice/architecture contracts.
    for row in reviewed:
        current = _current_path(row)
        if current.startswith("entrypoint:"):
            continue
        candidate = ROOT / current
        if row["disposition"] == "CARDINE_OWNER":
            if not candidate.is_file():
                errors.append(f"classified Cardine successor path is absent: {current}")
        elif row.get("baseline_state", "clean") == "clean" and row["removal_slice"] != "CA-02":
            if not candidate.is_file():
                errors.append(f"classification has undeclared baseline-only path: {current}")

    _validate_transition(
        reviewed,
        transition_rows,
        transition_entrypoints,
        targets,
        errors,
        live=live,
    )

    if live:
        transition_sources = {row["source_path"] for row in transition_rows}
        for row in reviewed:
            path = _current_path(row)
            if path.startswith("entrypoint:") or path in transition_sources:
                continue
            candidate = ROOT / path
            if row.get("baseline_state", "clean") == "clean" and candidate.is_file():
                try:
                    if _digest(path, targets) != row["sha256"]:
                        errors.append(f"classification sha256 mismatch for committed path: {path}")
                except OSError as error:
                    errors.append(f"cannot hash classified path {path}: {error}")
            elif row.get("baseline_state", "clean") in {"modified", "untracked"}:
                original = ROOT / row["path"]
                if not original.is_file():
                    errors.append(f"live baseline file is absent: {row['path']}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the frozen ownership inventory")
    parser.add_argument(
        "--live",
        action="store_true",
        help="also compare current bytes/AST with frozen CA-01/CA-02 snapshots",
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
