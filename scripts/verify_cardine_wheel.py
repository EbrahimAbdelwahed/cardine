"""Verify Cardine wheel and source distributions before release."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

REQUIRED_FILES = {
    "study_agent/py.typed",
    "study_agent/demo/browser.html",
    "study_agent/demo/browser.css",
    "study_agent/demo/browser.js",
    "study_agent/demo/ai-primitives.css",
    "study_agent/demo/ai-primitives.js",
    "study_agent/demo/fixtures/heart-valves.md",
    "study_agent/demo/icons/favicon.svg",
    "study_agent/demo/icons/LICENSE.phosphor.txt",
    "study_agent/demo/fonts/ibm-plex-mono-400.woff2",
    "study_agent/demo/fonts/LICENSE.txt",
    "study_agent/operator_skill/SKILL.md",
}
REQUIRED_ENTRY_POINTS = {
    "cardine = study_agent.cli.main:main",
    "cardine-demo = study_agent.demo.anatomy:main",
    "cardine-shell = study_agent.demo.product_shell:main",
    "cardine-shell-web = study_agent.demo.browser:main",
    "cardine-private-password-hash = study_agent.demo.private_access:main",
}

# These are checkout/runtime artifacts, not product source.  Keep the check
# segment-based so valid package modules such as ``study_agent/state`` and
# ``study_agent/*/runtime.py`` remain publishable.
FORBIDDEN_TOP_LEVEL_DIRECTORIES = {
    ".beads",
    ".cardine-ui-preview",
    "blobs",
    "exports",
    "runtime",
    "state",
    "study-agent-ui",
}
FORBIDDEN_TOP_LEVEL_FILES = {".study-agent.json", ".study-agent.lock", "study-agent.json"}
FORBIDDEN_PREFIXES = ("docs/design-source",)


def verify(artifact_path: Path) -> None:
    """Verify one wheel or sdist, failing closed for unknown archive types."""

    if artifact_path.name.endswith(".whl"):
        _verify_wheel(artifact_path)
    elif artifact_path.name.endswith((".tar.gz", ".tgz")):
        _verify_sdist(artifact_path)
    else:
        raise SystemExit(f"unsupported artifact type: {artifact_path}")


def _verify_wheel(wheel_path: Path) -> None:
    with zipfile.ZipFile(wheel_path) as wheel:
        raw_names = wheel.namelist()
        names = set(raw_names)
        _validate_archive_names(raw_names, "wheel")
        _verify_forbidden_paths(names, "wheel")
        missing = REQUIRED_FILES - names
        if missing:
            raise SystemExit(f"wheel is missing package data: {sorted(missing)}")

        metadata_path = _single(names, ".dist-info/METADATA")
        metadata = wheel.read(metadata_path).decode("utf-8")
        _verify_metadata(metadata, "wheel")

        entry_points_path = _single(names, ".dist-info/entry_points.txt")
        entry_points = wheel.read(entry_points_path).decode("utf-8")
        missing_entry_points = {
            marker for marker in REQUIRED_ENTRY_POINTS if marker not in entry_points
        }
        if missing_entry_points:
            raise SystemExit(
                f"wheel is missing product entrypoints: {sorted(missing_entry_points)}"
            )

        license_names = {
            name.rsplit("/", 1)[-1] for name in names if ".dist-info/licenses/" in name
        }
        expected_licenses = {"LICENSE", "LICENSE-CARDINE.md", "NOTICE.md"}
        if license_names != expected_licenses:
            raise SystemExit(
                f"wheel license set is {sorted(license_names)}, "
                f"expected {sorted(expected_licenses)}"
            )


def _verify_sdist(sdist_path: Path) -> None:
    with tarfile.open(sdist_path, mode="r:*") as sdist:
        members = sdist.getmembers()
        raw_names = [member.name for member in members]
        names = set(raw_names)
        _validate_archive_names(raw_names, "sdist")
        root = _single_root(names, "sdist")
        outside_root = sorted(
            name for name in names if name != root and not name.startswith(f"{root}/")
        )
        if outside_root:
            raise SystemExit(f"sdist contains members outside its root: {outside_root}")
        special_members = sorted(
            member.name for member in members if not (member.isfile() or member.isdir())
        )
        if special_members:
            raise SystemExit(f"sdist contains special members: {special_members}")
        relative_names = {
            name.removeprefix(f"{root}/") for name in names if name != root
        }
        _verify_forbidden_paths(relative_names, "sdist")
        required = {
            "README.md",
            "LICENSE",
            "LICENSE-CARDINE.md",
            "NOTICE.md",
            "pyproject.toml",
            *(f"src/{path}" for path in REQUIRED_FILES),
        }
        missing = required - relative_names
        if missing:
            raise SystemExit(f"sdist is missing release files: {sorted(missing)}")
        pyproject = sdist.extractfile(f"{root}/pyproject.toml")
        if pyproject is None:
            raise SystemExit("sdist is missing pyproject.toml")
        _verify_metadata(pyproject.read().decode("utf-8"), "sdist")


def _verify_metadata(metadata: str, artifact_kind: str) -> None:
    markers = (
        'name = "cardine"' if artifact_kind == "sdist" else "\nName: cardine\n",
        (
            'license = "LicenseRef-Cardine-Private"'
            if artifact_kind == "sdist"
            else "\nLicense-Expression: LicenseRef-Cardine-Private\n"
        ),
        "github.com/EbrahimAbdelwahed/cardine",
    )
    for marker in markers:
        if marker not in metadata:
            raise SystemExit(f"{artifact_kind} metadata is missing {marker!r}")


def _verify_forbidden_paths(names: Iterable[str], artifact_kind: str) -> None:
    forbidden: list[str] = []
    for name in names:
        path = name.strip("/")
        top_level, _, remainder = path.partition("/")
        if (
            top_level in FORBIDDEN_TOP_LEVEL_DIRECTORIES
            or path in FORBIDDEN_TOP_LEVEL_FILES
            or any(path == prefix or path.startswith(f"{prefix}/") for prefix in FORBIDDEN_PREFIXES)
            or (
                remainder
                and any(part in {".beads", ".cardine-ui-preview"} for part in path.split("/"))
            )
        ):
            forbidden.append(name)
    if forbidden:
        raise SystemExit(f"{artifact_kind} contains forbidden paths: {sorted(forbidden)}")


def _validate_archive_names(names: list[str], artifact_kind: str) -> None:
    if len(names) != len(set(names)):
        raise SystemExit(f"{artifact_kind} contains duplicate paths")
    for name in names:
        if not name or name.startswith("/") or "\\" in name:
            raise SystemExit(f"{artifact_kind} contains an unsafe path: {name!r}")
        parts = [part for part in name.split("/") if part]
        if ".." in parts:
            raise SystemExit(f"{artifact_kind} contains an unsafe path: {name!r}")


def _single(names: set[str], suffix: str) -> str:
    matches = sorted(name for name in names if name.endswith(suffix))
    if len(matches) != 1:
        raise SystemExit(f"wheel must contain exactly one {suffix}: {matches}")
    return matches[0]


def _single_root(names: set[str], artifact_kind: str) -> str:
    roots = {name.split("/", 1)[0] for name in names}
    if len(roots) != 1:
        raise SystemExit(f"{artifact_kind} must contain exactly one top-level directory: {roots}")
    return next(iter(roots))


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: verify_cardine_wheel.py ARTIFACT [ARTIFACT ...]")
    for raw_path in sys.argv[1:]:
        artifact_path = Path(raw_path)
        if not artifact_path.is_file():
            raise SystemExit(f"artifact does not exist: {artifact_path}")
        verify(artifact_path)


if __name__ == "__main__":
    main()
