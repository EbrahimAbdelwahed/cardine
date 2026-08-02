from __future__ import annotations

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


def _verifier() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "verify_cardine_wheel.py"
    spec = importlib.util.spec_from_file_location("cardine_artifact_verifier", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load artifact verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wheel_verifier_rejects_checkout_preview_paths(tmp_path: Path) -> None:
    path = tmp_path / "cardine-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(".cardine-ui-preview/state/secret.json", "secret")

    with pytest.raises(SystemExit, match="forbidden paths"):
        _verifier().verify(path)


def test_sdist_verifier_rejects_private_design_source_paths(tmp_path: Path) -> None:
    path = tmp_path / "cardine-0.2.0.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        payload = b"private design source"
        member = tarfile.TarInfo("cardine-0.2.0/docs/design-source/prototype.zip")
        member.size = len(payload)
        archive.addfile(member, fileobj=io.BytesIO(payload))

    with pytest.raises(SystemExit, match="forbidden paths"):
        _verifier().verify(path)


def test_sdist_verifier_rejects_extra_top_level_members(tmp_path: Path) -> None:
    path = tmp_path / "cardine-0.2.0.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        root = tarfile.TarInfo("cardine-0.2.0/")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        payload = b"runtime configuration"
        member = tarfile.TarInfo("study-agent.json")
        member.size = len(payload)
        archive.addfile(member, fileobj=io.BytesIO(payload))

    with pytest.raises(SystemExit, match="exactly one top-level directory"):
        _verifier().verify(path)


def test_sdist_verifier_rejects_links_and_special_members(tmp_path: Path) -> None:
    path = tmp_path / "cardine-0.2.0.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        link = tarfile.TarInfo("cardine-0.2.0/linked-secret")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../private-source"
        archive.addfile(link)

    with pytest.raises(SystemExit, match="special members"):
        _verifier().verify(path)
