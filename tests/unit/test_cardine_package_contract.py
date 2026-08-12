"""Regression coverage for Cardine's distribution identity and package data."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_project_metadata_is_cardine_private_and_publishes_only_cardine_commands() -> None:
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
    assert set(scripts) == {
        "cardine",
        "cardine-demo",
        "cardine-shell",
        "cardine-shell-web",
        "cardine-private-password-hash",
    }
    assert all(target.startswith("cardine.") for target in scripts.values())

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    private_license = (ROOT / "LICENSE-CARDINE.md").read_text(encoding="utf-8")
    assert readme.startswith("# Cardine\n")
    assert "Cardine is not an open-source release" in readme
    assert "# Study Agent Harness" not in readme
    assert "Cardine is a private product repository" in notice
    assert "not a grant of permission" in private_license
    assert "granted to copy" in private_license


def test_package_manifest_declares_product_assets_and_notices() -> None:
    """Pin the source manifest; CI separately builds and clean-installs the wheel."""

    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = configuration["tool"]["setuptools"]["package-data"]
    assert package_data["study_agent"] == ["py.typed"]
    assert package_data["study_agent.operator_skill"] == ["SKILL.md"]
    demo_patterns = set(package_data["cardine.demo"])
    assert {
        "browser.html",
        "browser.css",
        "browser.js",
        "ai-primitives.css",
        "ai-primitives.js",
        "fixtures/*.md",
        "icons/*.svg",
        "icons/*.txt",
        "fonts/*.woff2",
        "fonts/*.txt",
    } <= demo_patterns

    for path in (
        "src/study_agent/py.typed",
        "src/cardine/demo/browser.html",
        "src/cardine/demo/browser.css",
        "src/cardine/demo/browser.js",
        "src/cardine/demo/ai-primitives.css",
        "src/cardine/demo/ai-primitives.js",
        "src/cardine/demo/fixtures/heart-valves.md",
        "src/cardine/demo/icons/favicon.svg",
        "src/cardine/demo/icons/LICENSE.phosphor.txt",
        "src/cardine/demo/fonts/ibm-plex-mono-400.woff2",
        "src/cardine/demo/fonts/LICENSE.txt",
        "src/study_agent/operator_skill/SKILL.md",
        "LICENSE",
        "LICENSE-CARDINE.md",
        "NOTICE.md",
    ):
        assert (ROOT / path).is_file(), path
