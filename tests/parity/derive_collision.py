"""Derive expected-red distribution collision evidence without installation."""

from __future__ import annotations

import hashlib
import json
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "tests/parity/artifacts"


def inspect_wheel(path: Path) -> dict[str, object]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with zipfile.ZipFile(path) as wheel:
        names = sorted(wheel.namelist())
        metadata_name = next(name for name in names if name.endswith("/METADATA"))
        entry_name = next(name for name in names if name.endswith("/entry_points.txt"))
        metadata = Parser().parsestr(wheel.read(metadata_name).decode("utf-8"))
        sections: dict[str, list[str]] = {}
        current: str | None = None
        for line in wheel.read(entry_name).decode("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1]
                sections[current] = []
            elif current is not None:
                sections[current].append(line)
        members = {
            name: hashlib.sha256(wheel.read(name)).hexdigest()
            for name in names
            if not name.endswith("/")
        }
    return {
        "filename": path.name,
        "name": metadata["Name"],
        "version": metadata["Version"],
        "sha256": digest,
        "metadata_member": metadata_name,
        "entry_points_member": entry_name,
        "console_entry_points": sorted(
            line.split("=", 1)[0].strip()
            for line in sections.get("console_scripts", [])
            if "=" in line
        ),
        "regular_packages": sorted(
            {
                name.split("/", 1)[0]
                for name in names
                if "/" in name and not name.startswith("dist-info/")
            }
        ),
        "member_sha256": members,
    }


def derive() -> dict[str, object]:
    cardine = inspect_wheel(ARTIFACTS / "cardine-0.2.0-py3-none-any.whl")
    harness = inspect_wheel(ARTIFACTS / "study_agent_harness-0.2.0-py3-none-any.whl")
    cardine_packages = cast(list[str], cardine["regular_packages"])
    harness_packages = cast(list[str], harness["regular_packages"])
    cardine_entry_points = cast(list[str], cardine["console_entry_points"])
    harness_entry_points = cast(list[str], harness["console_entry_points"])
    package_collisions = sorted(set(cardine_packages) & set(harness_packages))
    entry_collisions = sorted(set(cardine_entry_points) & set(harness_entry_points))
    return {
        "schema_version": 1,
        "expected_red": True,
        "install_policy": "inspect-independent-zips-never-coinstall",
        "artifacts": {"cardine": cardine, "harness": harness},
        "collisions": {
            "regular_packages": package_collisions,
            "console_entry_points": entry_collisions,
        },
    }


if __name__ == "__main__":
    print(json.dumps(derive(), sort_keys=True, indent=2))
