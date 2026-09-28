"""Validate the reviewed CA-01 ownership inventory and its CSV projection.

``tests/parity/ownership-classification.json`` is the human-reviewed source of
truth.  The CSV is intentionally only a transport ledger: this checker does
not infer ownership from directory prefixes or catch-all rules.  A clean
archive can therefore verify every row, including explicitly declared dirty
baseline-only paths, without requiring those paths to exist in the archive.
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
RECOVERY_OVERLAY = ROOT / "tests/parity/wave-a-recovery-overlay.json"
RECOVERY_NEW_CORE_PATHS = {
    "src/study_agent/ingestion/preparation.py",
    "src/study_agent/prompts/retrieval_query_recovery_v1.py",
}
RECOVERY_AST_VARIANCE = {
    "src/study_agent/domain/__init__.py",
    "src/study_agent/tutor_snapshot/reader.py",
}
BASELINE_WHEEL = ROOT / "tests/parity/artifacts/cardine-0.2.0-py3-none-any.whl"
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
    # Wave A teaches canonical export replay about Cardine-owned policy events
    # so it preserves their audit rows instead of dropping or rejecting them.
    "src/study_agent/application/export.py",
    "src/study_agent/artifacts/verified_batch.py",
    "src/study_agent/capabilities/morphology_flashcards.py",
    "src/study_agent/flashcards/lesson_worker_service.py",
    "src/study_agent/prompts/morphology_flashcards_v1.py",
    # Wave A extends the verified proof-reader port with an optional exact
    # execution-input commitment.  This is a reviewed protocol correction,
    # not a namespace-transition import rewrite.
    "src/study_agent/ports/verified_batch.py",
    "src/study_agent/workers/proof.py",
}
# Exact bytes from committed Wave A product evolution after the historical
# CA-01/CA-02 snapshots. Preserve the frozen ledgers and reject further drift.
POST_BASELINE_SHA256 = {
    "src/study_agent/adapters/sqlite/fts_retrieval.py": (
        "6478e496fa957b601866fbe5561a96bea1f92b61adc707c4e1dddef125188b5c"
    ),
    "src/study_agent/prompts/explain_concept_v1.py": (
        "00957942586239a396bae507593b687b1896fee120eba83e37feae592a95ff48"
    ),
    "src/study_agent/prompts/tutor_decision_v1.py": (
        "828072504ce14499f604fa3951b796e25a1422b4784f72a496ab16b5accf220b"
    ),
    "src/cardine/cli/repository.py": (
        "eadd4ed0448a40c37f80d81277a512bb8a7aa195f0f1d8ecfd43cad1f977f00f"
    ),
    "src/cardine/demo/browser.js": (
        "66e3c9107c7f25295a03aa10e776a3ae1ea3a55b88ceacc06ba0c42c86bc92f4"
    ),
    "src/cardine/demo/ui_application.py": (
        "a3099df6a060ea0c927de35ee07fd68a597b84f3c21400e1dfd45776f68c43d9"
    ),
    "src/cardine/hosts/flashcard_routing.py": (
        "e91a8ce48b54469829f1f8c5bc808c739d86279ad037e9957965dfc8c801683c"
    ),
    "src/cardine/hosts/source_grounding.py": (
        "dce83af15586066e48306ddcfe755a469836e2eeb3fe381d3d32610d6f4ea1f0"
    ),
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
    # These four rows were already dirty at the rejected checkpoint but are
    # still part of the reviewed copied-core transition inventory.
    "src/study_agent/capabilities/morphology_flashcards.py",
    "src/study_agent/flashcards/lesson_worker_service.py",
    "src/study_agent/prompts/morphology_flashcards_v1.py",
    "src/study_agent/workers/proof.py",
}


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
            paths.update(
                path.relative_to(ROOT).as_posix()
                for path in matches
                # Qualified third-party artifacts have their own exact
                # supply-chain verifier and are not CA-01/CA-02 namespace rows.
                if not path.is_relative_to(ROOT / "src/cardine/documents/_vendor")
                and path != ROOT / "src/cardine/adapters/pageindex/page_index_md.py.data"
            )
    return paths


def _entry_point_targets(config: Mapping[str, object]) -> dict[str, str]:
    project = config.get("project")
    scripts = project.get("scripts") if isinstance(project, Mapping) else None
    if not isinstance(scripts, Mapping) or not scripts:
        raise ValueError("pyproject is missing [project.scripts]")
    return {f"entrypoint:{name}": str(target) for name, target in scripts.items()}


def _source_paths(reviewed_current_paths: set[str]) -> set[str]:
    paths: set[str] = set()
    for package_root in (ROOT / "src/study_agent", ROOT / "src/cardine"):
        paths.update(
            path.relative_to(ROOT).as_posix()
            for path in package_root.rglob("*")
            if path.is_file()
            and "/__pycache__/" not in path.as_posix()
            and path.suffix != ".pyc"
            and "_transition" not in path.parts
            # CA-02 freezes namespace ownership. Later Cardine integration
            # modules have their own slice boundary and are not CA-01 rows.
            and not path.is_relative_to(ROOT / "src/cardine/integrations")
            # New post-CA-02 Cardine product modules are governed by their own
            # slice contracts.  This audit continues to bind only the exact
            # reviewed CA-01/CA-02 ownership universe.
            and (
                package_root == ROOT / "src/study_agent"
                or path.relative_to(ROOT).as_posix() in reviewed_current_paths
            )
        )
    return paths


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


def _load_recovery_overlay() -> dict[str, str]:
    """Owner-approved temporary evolution; historical custody stays unchanged."""
    raw = json.loads(RECOVERY_OVERLAY.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "source_commit", "rows"}:
        raise ValueError("recovery overlay has invalid fields")
    if raw["schema_version"] != 1 or raw["source_commit"] != (
        "50cb0cbfeb8e5f66ccd75153e6729b67d8cf1968"
    ):
        raise ValueError("recovery overlay has invalid approval checkpoint")
    rows = raw["rows"]
    if not isinstance(rows, list) or len(rows) != 46:
        raise ValueError("recovery overlay must contain exactly 46 approved paths")
    hashes: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ValueError("recovery overlay row has invalid fields")
        path, digest = row["path"], row["sha256"]
        if (
            not isinstance(path, str)
            or not path.startswith(("src/cardine/", "src/study_agent/"))
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise ValueError("recovery overlay path is outside the owned universe")
        if path in hashes:
            raise ValueError(f"recovery overlay duplicate path: {path}")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError(f"recovery overlay invalid sha256: {path}")
        hashes[path] = digest
    if not hashes.keys() >= (RECOVERY_NEW_CORE_PATHS | RECOVERY_AST_VARIANCE):
        raise ValueError("recovery overlay is missing required core bindings")
    return hashes


def _load_rows() -> list[dict[str, str]]:
    if not LEDGER.is_file():
        raise ValueError(f"missing ownership ledger: {LEDGER}")
    with LEDGER.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f"ledger columns must be exactly {FIELDS}")
        return [dict(row) for row in reader]


def _load_transition_overlay() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if not TRANSITION_OVERLAY.is_file():
        raise ValueError(f"missing CA-02 transition overlay: {TRANSITION_OVERLAY}")
    raw = json.loads(TRANSITION_OVERLAY.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise ValueError("CA-02 transition overlay must declare schema_version 1")
    if tuple(raw.get("columns", ())) != OVERLAY_FIELDS:
        raise ValueError(f"CA-02 transition overlay columns must be exactly {OVERLAY_FIELDS}")
    rows = raw.get("rows")
    entrypoints = raw.get("entrypoints")
    if not isinstance(rows, list) or not isinstance(entrypoints, list):
        raise ValueError("CA-02 transition overlay requires rows and entrypoints lists")
    loaded: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(rows, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"CA-02 transition row {index} is not an object")
        if any(
            not isinstance(item.get(field), str) or not item[field].strip()
            for field in OVERLAY_FIELDS
        ):
            raise ValueError(f"CA-02 transition row {index} has blank fields")
        row = {field: str(item[field]) for field in OVERLAY_FIELDS}
        if row["path"] in seen:
            raise ValueError(f"CA-02 transition overlay duplicate path: {row['path']}")
        if row["disposition"] not in OVERLAY_DISPOSITIONS:
            raise ValueError(f"CA-02 transition row {index} has unknown disposition")
        digest = row["transition_sha256"]
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"CA-02 transition row {index} has invalid transition_sha256")
        seen.add(row["path"])
        loaded.append(row)
    loaded_entrypoints: list[dict[str, str]] = []
    seen_entrypoints: set[str] = set()
    for index, item in enumerate(entrypoints, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"CA-02 entrypoint row {index} is not an object")
        if any(
            not isinstance(item.get(field), str) or not item[field].strip()
            for field in OVERLAY_FIELDS
        ):
            raise ValueError(f"CA-02 entrypoint row {index} has blank fields")
        row = {field: str(item[field]) for field in OVERLAY_FIELDS}
        if row["path"] in seen_entrypoints:
            raise ValueError(f"CA-02 entrypoint duplicate path: {row['path']}")
        seen_entrypoints.add(row["path"])
        loaded_entrypoints.append(row)
    return loaded, loaded_entrypoints


def _digest(path: str, targets: Mapping[str, str]) -> str:
    if path.startswith("entrypoint:"):
        return hashlib.sha256(f"{path}\0{targets[path]}".encode()).hexdigest()
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def _baseline_source(path: str) -> str:
    """Read the frozen CA-01 source counterpart from the parity wheel."""

    member = path.removeprefix("src/")
    if not BASELINE_WHEEL.is_file():
        raise OSError(f"missing frozen baseline wheel: {BASELINE_WHEEL}")
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
            and any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            )
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            values = {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            if len(values) != len(node.value.elts):
                raise ValueError(
                    "Cardine transition __all__ must contain only unique string literals"
                )
            exports = values
    if exports is None:
        raise ValueError("Cardine transition seam is missing a literal __all__")

    consumers: set[str] = set()
    for path in (ROOT / "src/cardine").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("src/cardine/_transition/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for imported_node in ast.walk(tree):
            if (
                isinstance(imported_node, ast.ImportFrom)
                and imported_node.module == "cardine._transition.study_agent"
            ):
                consumers.add(relative)
            if (
                isinstance(imported_node, ast.ImportFrom)
                and imported_node.module == "cardine._transition"
                and any(alias.name == "study_agent" for alias in imported_node.names)
            ):
                consumers.add(relative)
            if isinstance(imported_node, ast.Import) and any(
                alias.name == "cardine._transition.study_agent" for alias in imported_node.names
            ):
                consumers.add(relative)
    return exports, consumers


def _current_path(row: Mapping[str, str]) -> str:
    path = row["path"]
    replacement = row["replacement_import_or_path"]
    if row["disposition"] == "CARDINE_OWNER" and replacement.startswith("src/"):
        return replacement
    return path


def _validate_cardine_transition(
    reviewed: list[dict[str, str]],
    current_paths: set[str],
    targets: Mapping[str, str],
    transition_rows: list[dict[str, str]],
    transition_entrypoints: list[dict[str, str]],
    recovery_hashes: Mapping[str, str],
    errors: list[str],
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
        errors.append(
            f"CA-02 must classify exactly 86 Cardine source targets, found {len(source_rows)}"
        )
    if len(entrypoint_rows) != 5:
        errors.append(
            f"CA-02 must classify exactly five Cardine entrypoints, found {len(entrypoint_rows)}"
        )
    expected_entrypoints = {
        "entrypoint:cardine",
        "entrypoint:cardine-demo",
        "entrypoint:cardine-shell",
        "entrypoint:cardine-shell-web",
        "entrypoint:cardine-private-password-hash",
    }
    actual_entrypoints = {row["path"] for row in entrypoint_rows}
    if actual_entrypoints != expected_entrypoints:
        errors.append(f"CA-02 entrypoint rows are not exact: {sorted(actual_entrypoints)}")
    if set(targets) != expected_entrypoints:
        errors.append(f"pyproject must publish exactly five Cardine entrypoints: {sorted(targets)}")
    if any(target.startswith("study_agent.") for target in targets.values()):
        errors.append("Cardine entrypoints must not target study_agent")
    if any(name.startswith("entrypoint:study-agent") for name in targets):
        errors.append("Cardine namespace must not publish study-agent aliases")
    for row in source_rows:
        old_path = row["path"]
        replacement = row["replacement_import_or_path"]
        if not replacement.startswith("src/cardine/"):
            errors.append(
                f"Cardine source row has invalid replacement: {old_path} -> {replacement}"
            )
            continue
        if old_path in current_paths:
            errors.append(f"moved Cardine path remains present: {old_path}")
        if replacement not in current_paths:
            errors.append(f"Cardine replacement path is absent: {replacement}")
    expected_moved = {row["path"]: row["replacement_import_or_path"] for row in source_rows}
    expected_copied = set(COPIED_IMPORT_PATHS)
    expected_transition_paths = set(expected_moved) | expected_copied | TRANSITION_CONSUMERS
    if len(transition_rows) != 119:
        errors.append(
            f"CA-02 transition overlay must contain 119 rows, found {len(transition_rows)}"
        )
    if {row["path"] for row in transition_rows} != expected_transition_paths:
        errors.append("CA-02 transition overlay path set is not the reviewed 119-target set")
    by_path = {row["path"]: row for row in transition_rows}
    for old_path, replacement in expected_moved.items():
        moved_row = by_path.get(old_path)
        if moved_row is None:
            continue
        if moved_row["source_path"] != replacement or moved_row["disposition"] != "CARDINE_OWNER":
            errors.append(f"CA-02 moved transition binding is invalid: {old_path}")
    for path in expected_copied:
        copied_row = by_path.get(path)
        if copied_row is None:
            continue
        if copied_row["source_path"] != path or copied_row["disposition"] != "HARNESS_IMPORT":
            errors.append(f"CA-02 copied-core transition binding is invalid: {path}")
    for path in TRANSITION_CONSUMERS:
        consumer_row = by_path.get(path)
        if consumer_row is None:
            continue
        if (
            consumer_row["source_path"] != path
            or consumer_row["disposition"] != "TRANSITION_CONSUMER"
        ):
            errors.append(f"CA-02 transition consumer binding is invalid: {path}")
    for row in transition_rows:
        source_path = row["source_path"]
        try:
            if _digest(source_path, targets) != recovery_hashes.get(
                source_path, POST_BASELINE_SHA256.get(source_path, row["transition_sha256"])
            ):
                errors.append(f"CA-02 transition sha256 mismatch for {source_path}")
            if source_path.endswith(".py"):
                current_source = (ROOT / source_path).read_text(encoding="utf-8")
                ast.parse(current_source, filename=source_path)
                if (
                    row["disposition"] == "HARNESS_IMPORT"
                    and source_path not in REVIEWED_NON_IMPORT_AST_VARIANCE
                    and source_path not in RECOVERY_AST_VARIANCE
                ):
                    baseline_source = _baseline_source(source_path)
                    if _normalized_ast(current_source, source_path) != _normalized_ast(
                        baseline_source, f"baseline:{source_path}"
                    ):
                        errors.append(f"CA-02 copied-core non-import AST mismatch: {source_path}")
        except (OSError, SyntaxError) as error:
            errors.append(f"CA-02 transition source invalid {source_path}: {error}")
    expected_entrypoints = {
        "entrypoint:cardine",
        "entrypoint:cardine-demo",
        "entrypoint:cardine-shell",
        "entrypoint:cardine-shell-web",
        "entrypoint:cardine-private-password-hash",
    }
    if len(transition_entrypoints) != 5:
        errors.append(
            "CA-02 transition overlay must contain five entrypoint rows, "
            f"found {len(transition_entrypoints)}"
        )
    raw_entrypoint_names = {row["path"] for row in transition_entrypoints}
    if raw_entrypoint_names != expected_entrypoints:
        errors.append("CA-02 transition entrypoint path set is not exact")
    if raw_entrypoint_names == expected_entrypoints:
        for row in transition_entrypoints:
            if row["source_path"] != targets.get(row["path"]):
                errors.append(f"CA-02 transition entrypoint target mismatch: {row['path']}")
            if row["disposition"] != "CARDINE_OWNER":
                errors.append(f"CA-02 transition entrypoint disposition mismatch: {row['path']}")
            if _digest(row["path"], targets) != row["transition_sha256"]:
                errors.append(f"CA-02 transition entrypoint digest mismatch: {row['path']}")


def validate(*, live: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        recovery_hashes = _load_recovery_overlay()
        reviewed = _load_classification()
        reviewed_current_paths = {_current_path(row) for row in reviewed}
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        targets = _entry_point_targets(config)
        current_paths = (
            _source_paths(reviewed_current_paths) | _declared_package_data(config) | set(targets)
        )
        transition_rows, transition_entrypoints = _load_transition_overlay()
    except (OSError, ValueError, tomllib.TOMLDecodeError, json.JSONDecodeError) as error:
        return [f"cannot derive ownership universe: {error}"]
    reviewed_by_path = {row["path"]: row for row in reviewed}
    reviewed_by_current_path = {_current_path(row): row for row in reviewed}
    current_clean = {
        path
        for path in current_paths
        if (
            path in reviewed_by_current_path
            and reviewed_by_current_path[path].get("baseline_state", "clean") == "clean"
            and reviewed_by_current_path[path]["path"] == path
        )
    }
    for path, expected_digest in recovery_hashes.items():
        try:
            if _digest(path, targets) != expected_digest:
                errors.append(f"recovery overlay sha256 mismatch for {path}")
        except OSError as error:
            errors.append(f"recovery overlay missing source {path}: {error}")
    for path in sorted(current_paths - set(reviewed_by_current_path) - RECOVERY_NEW_CORE_PATHS):
        errors.append(f"classification is missing current path: {path}")
    for path in sorted(set(reviewed_by_current_path) - current_paths):
        row = reviewed_by_current_path[path]
        if row.get("baseline_state", "clean") == "clean" and not (
            path.startswith("entrypoint:study-agent") and row["removal_slice"] == "CA-02"
        ):
            errors.append(f"classification has undeclared baseline-only path: {path}")
    transition_sources = {row["source_path"] for row in transition_rows}
    for path in sorted(current_clean):
        try:
            if (
                path not in transition_sources
                and not path.startswith("entrypoint:")
                and _digest(path, targets)
                != recovery_hashes.get(
                    path, POST_BASELINE_SHA256.get(path, reviewed_by_current_path[path]["sha256"])
                )
            ):
                errors.append(f"classification sha256 mismatch for committed path: {path}")
        except OSError as error:
            errors.append(f"cannot hash classified path {path}: {error}")
    _validate_cardine_transition(
        reviewed,
        current_paths,
        targets,
        transition_rows,
        transition_entrypoints,
        recovery_hashes,
        errors,
    )
    if live:
        for path, row in reviewed_by_path.items():
            if row.get("baseline_state", "clean") in {"modified", "untracked"}:
                candidate = ROOT / path
                if not candidate.is_file():
                    errors.append(f"live baseline file is absent: {path}")
                elif path not in transition_sources and _digest(path, targets) != row["sha256"]:
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
