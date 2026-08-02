"""Verify the built Cardine wheel without importing its build backend."""

from __future__ import annotations

import sys
import zipfile
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


def verify(wheel_path: Path) -> None:
    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
        missing = REQUIRED_FILES - names
        if missing:
            raise SystemExit(f"wheel is missing package data: {sorted(missing)}")

        metadata_path = _single(names, ".dist-info/METADATA")
        metadata = wheel.read(metadata_path).decode("utf-8")
        for marker in (
            "\nName: cardine\n",
            "\nLicense-Expression: LicenseRef-Cardine-Private\n",
            "github.com/EbrahimAbdelwahed/cardine",
        ):
            if marker not in metadata:
                raise SystemExit(f"wheel metadata is missing {marker.strip()!r}")

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
            name.rsplit("/", 1)[-1]
            for name in names
            if ".dist-info/licenses/" in name
        }
        expected_licenses = {"LICENSE", "LICENSE-CARDINE.md", "NOTICE.md"}
        if license_names != expected_licenses:
            raise SystemExit(
                f"wheel license set is {sorted(license_names)}, "
                f"expected {sorted(expected_licenses)}"
            )


def _single(names: set[str], suffix: str) -> str:
    matches = sorted(name for name in names if name.endswith(suffix))
    if len(matches) != 1:
        raise SystemExit(f"wheel must contain exactly one {suffix}: {matches}")
    return matches[0]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_cardine_wheel.py WHEEL")
    wheel_path = Path(sys.argv[1])
    if not wheel_path.is_file():
        raise SystemExit(f"wheel does not exist: {wheel_path}")
    verify(wheel_path)


if __name__ == "__main__":
    main()
