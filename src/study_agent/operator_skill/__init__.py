"""Packaged operator workflow and offline extraction contract."""

from __future__ import annotations

import os
import stat
import tempfile
from errno import ELOOP
from hashlib import sha256
from importlib.resources import files
from pathlib import Path

from study_agent.domain._validation import JsonObject

SKILL_ID = "study-agent-operator"
SKILL_VERSION = "1.0.0"
EXTRACTION_COMMAND = "cardine --json operator skill --output PATH"


def skill_bytes() -> bytes:
    """Read the canonical bytes from the installed distribution."""
    return files(__package__).joinpath("SKILL.md").read_bytes()


def skill_fingerprint() -> str:
    """Fingerprint the exact bytes returned by extraction."""
    return sha256(skill_bytes()).hexdigest()


def skill_metadata() -> JsonObject:
    """Return the closed discovery shape without duplicating skill content."""
    return {
        "id": SKILL_ID,
        "version": SKILL_VERSION,
        "fingerprint": skill_fingerprint(),
        "extraction_command": EXTRACTION_COMMAND,
    }


def extract_skill(output: Path) -> JsonObject:
    """Write the canonical resource offline and return a verification receipt."""
    content = skill_bytes()
    destination = output.expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = _read_stable_regular_file(destination)
    except FileNotFoundError:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination, follow_symlinks=False)
                _fsync_directory(destination.parent)
            except FileExistsError:
                if _read_stable_regular_file(destination) != content:
                    raise FileExistsError(
                        "operator skill output already exists with different content"
                    ) from None
        finally:
            temporary.unlink(missing_ok=True)
    else:
        if existing != content:
            raise FileExistsError(
                "operator skill output already exists with different content"
            )
    return {
        "id": SKILL_ID,
        "version": SKILL_VERSION,
        "fingerprint": sha256(content).hexdigest(),
        "path": str(destination),
    }


def _read_stable_regular_file(path: Path) -> bytes:
    """Read one unchanged regular inode without following the final component."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if error.errno == ELOOP:
            raise FileExistsError("operator skill output must be a regular file") from None
        raise
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FileExistsError("operator skill output must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read()
        after = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or _stable_identity(before) != _stable_identity(after)
            or _stable_identity(before) != _stable_identity(current)
        ):
            raise FileExistsError("operator skill output changed while it was read")
        return content
    finally:
        os.close(descriptor)


def _stable_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "EXTRACTION_COMMAND",
    "SKILL_ID",
    "SKILL_VERSION",
    "extract_skill",
    "skill_bytes",
    "skill_fingerprint",
    "skill_metadata",
]
