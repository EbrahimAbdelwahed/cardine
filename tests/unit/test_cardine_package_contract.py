"""Regression coverage for Cardine's distribution identity and package data."""

from __future__ import annotations

import io
import shutil
import tomllib
import zipfile
from importlib import import_module
from pathlib import Path
from typing import Any, cast

build_meta = cast(Any, import_module("setuptools.build_meta"))

ROOT = Path(__file__).parents[2]


def test_project_metadata_is_cardine_private_and_keeps_core_aliases_explicit() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["name"] == "cardine"
    assert project["description"].startswith("Private Cardine")
    assert project["license"] == "LicenseRef-Cardine-Private"
    assert project["license-files"] == ["LICENSE", "LICENSE-CARDINE.md", "NOTICE.md"]
    assert project["urls"] == {
        "Homepage": "https://github.com/EbrahimAbdelwahed/cardine",
        "Documentation": "https://github.com/EbrahimAbdelwahed/cardine#readme",
        "Issues": "https://github.com/EbrahimAbdelwahed/cardine/issues",
        "Source": "https://github.com/EbrahimAbdelwahed/cardine",
    }

    scripts = project["scripts"]
    for name in (
        "cardine",
        "cardine-demo",
        "cardine-shell",
        "cardine-shell-web",
        "cardine-private-password-hash",
    ):
        assert name in scripts
    # The copied core's aliases are compatibility surface, not the product name.
    assert scripts["cardine"] == scripts["study-agent"]

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    private_license = (ROOT / "LICENSE-CARDINE.md").read_text(encoding="utf-8")
    assert readme.startswith("# Cardine\n")
    assert "Cardine is not an open-source release" in readme
    assert "# Study Agent Harness" not in readme
    assert "Cardine is a private product repository" in notice
    assert "not a grant of permission" in private_license
    assert "granted to copy" in private_license


def test_clean_wheel_contains_product_entrypoints_assets_and_notices(tmp_path: Path) -> None:
    """Build in an isolated copy so package-data regressions fail offline."""

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copytree(ROOT / "src", checkout / "src")
    for filename in ("pyproject.toml", "README.md", "LICENSE", "LICENSE-CARDINE.md", "NOTICE.md"):
        shutil.copy2(ROOT / filename, checkout / filename)
    wheel_directory = checkout / "dist"
    wheel_directory.mkdir()

    # setuptools' in-process backend needs only the repository and its pinned
    # build dependency; no package index, credentials, or external service is
    # touched by this smoke path.
    import contextlib

    with contextlib.chdir(checkout), contextlib.redirect_stdout(io.StringIO()):
        wheel_name = build_meta.build_wheel(str(wheel_directory))

    with zipfile.ZipFile(wheel_directory / wheel_name) as wheel:
        names = set(wheel.namelist())
        dist_info = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = wheel.read(dist_info).decode("utf-8")
        assert "\nName: cardine\n" in metadata
        assert "github.com/EbrahimAbdelwahed/cardine" in metadata

        for path in (
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
        ):
            assert path in names, path

        license_paths = {
            name.split("/", 2)[-1]
            for name in names
            if ".dist-info/licenses/" in name
        }
        assert license_paths == {"LICENSE", "LICENSE-CARDINE.md", "NOTICE.md"}

        entry_points = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        entry_point_text = wheel.read(entry_points).decode("utf-8")
        assert "cardine = study_agent.cli.main:main" in entry_point_text
        assert "cardine-shell-web = study_agent.demo.browser:main" in entry_point_text
