from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from study_agent.adapters.filesystem import (
    BlobIntegrityError,
    BlobNotFoundError,
    FilesystemBlobStore,
    UnsafeBlobPathError,
)
from study_agent.domain import BlobId, BlobRef


def object_path(root: Path, digest: str) -> Path:
    return root / "objects" / digest[:2] / digest[2:4] / digest


def test_identical_canonical_reads_have_bounded_hash_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"canonical source bytes" * 32_000
    store = FilesystemBlobStore(tmp_path / "blobs")
    ref = store.put(content)
    digest = hashlib.sha256
    hashed_bytes: list[int] = []

    def observed_digest(payload: bytes) -> Any:
        hashed_bytes.append(len(payload))
        return digest(payload)

    monkeypatch.setattr(hashlib, "sha256", observed_digest)
    for _ in range(16):
        assert store.get(ref) == content
    assert sum(hashed_bytes) <= len(content), "identical reads repeatedly hashed the whole source"


def test_duplicate_put_creates_one_sharded_immutable_object(tmp_path: Path) -> None:
    root = tmp_path / "blobs"
    store = FilesystemBlobStore(root)
    content = b"same content"

    first = store.put(content)
    second = store.put(content)

    stored_files = [path for path in (root / "objects").rglob("*") if path.is_file()]
    assert first == second
    assert stored_files == [object_path(root, first.checksum_sha256)]
    assert stored_files[0].read_bytes() == content
    assert stored_files[0].stat().st_mode & 0o222 == 0


def test_missing_and_corrupt_objects_fail_explicitly(tmp_path: Path) -> None:
    root = tmp_path / "blobs"
    store = FilesystemBlobStore(root)
    ref = store.put(b"canonical")
    target = object_path(root, ref.checksum_sha256)
    assert store.get(ref) == b"canonical"  # Warm the parsed/verified read path.

    wrong_length = BlobRef(ref.id, ref.checksum_sha256, ref.byte_length + 1)
    with pytest.raises(BlobIntegrityError, match="length mismatch"):
        store.get(wrong_length)

    target.chmod(0o644)
    target.write_bytes(b"corrupt!!")

    with pytest.raises(BlobIntegrityError, match="checksum mismatch"):
        store.get(ref)

    target.write_bytes(b"canonical")
    assert store.get(ref) == b"canonical"  # Failure must not poison recovery.

    target.unlink()
    with pytest.raises(BlobNotFoundError, match="does not exist"):
        store.get(ref)


def test_existing_digest_path_is_never_replaced(tmp_path: Path) -> None:
    root = tmp_path / "blobs"
    store = FilesystemBlobStore(root)
    content = b"expected"
    digest = hashlib.sha256(content).hexdigest()
    target = object_path(root, digest)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"attacker bytes")

    with pytest.raises(BlobIntegrityError):
        store.put(content)

    assert target.read_bytes() == b"attacker bytes"


def test_reference_id_cannot_control_or_escape_object_path(tmp_path: Path) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    digest = "a" * 64
    malicious = BlobRef(BlobId("../../outside"), digest, 0)

    with pytest.raises(UnsafeBlobPathError, match="exactly match"):
        store.get(malicious)


def test_symlinked_root_shard_and_object_are_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(UnsafeBlobPathError, match="root cannot be a symlink"):
        FilesystemBlobStore(root_link)

    root = tmp_path / "blobs"
    store = FilesystemBlobStore(root)
    content = b"symlink target"
    digest = hashlib.sha256(content).hexdigest()
    outside = tmp_path / "outside"
    outside.mkdir()
    shard = root / "objects" / digest[:2]
    shard.symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeBlobPathError):
        store.put(content)

    shard.unlink()
    ref = store.put(content)
    target = object_path(root, digest)
    target.chmod(0o644)
    target.unlink()
    external_file = tmp_path / "external"
    external_file.write_bytes(content)
    target.symlink_to(external_file)
    with pytest.raises(UnsafeBlobPathError):
        store.get(ref)


def test_retained_descriptors_ignore_root_path_swap(tmp_path: Path) -> None:
    root = tmp_path / "blobs"
    store = FilesystemBlobStore(root)
    anchored_root = tmp_path / "anchored-blobs"
    root.rename(anchored_root)
    root.mkdir()
    attacker_objects = tmp_path / "attacker-objects"
    attacker_objects.mkdir()
    (root / "objects").symlink_to(attacker_objects, target_is_directory=True)

    ref = store.put(b"after root swap")

    assert store.get(ref) == b"after root swap"
    assert object_path(anchored_root, ref.checksum_sha256).read_bytes() == b"after root swap"
    assert list(attacker_objects.iterdir()) == []


def test_shard_swap_to_symlink_is_rejected_relative_to_objects_fd(tmp_path: Path) -> None:
    root = tmp_path / "blobs"
    store = FilesystemBlobStore(root)
    ref = store.put(b"before shard swap")
    first_shard = root / "objects" / ref.checksum_sha256[:2]
    preserved_shard = root / "objects" / "preserved"
    first_shard.rename(preserved_shard)
    outside = tmp_path / "outside-shard"
    outside.mkdir()
    first_shard.symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeBlobPathError):
        store.get(ref)
    with pytest.raises(UnsafeBlobPathError):
        store.put(b"before shard swap")
    assert list(outside.iterdir()) == []


def test_concurrent_identical_puts_publish_one_verified_object(tmp_path: Path) -> None:
    root = tmp_path / "blobs"
    store = FilesystemBlobStore(root)
    content = b"concurrent immutable content"

    with ThreadPoolExecutor(max_workers=8) as executor:
        refs = tuple(executor.map(store.put, (content,) * 32))

    assert len(set(refs)) == 1
    ref = refs[0]
    assert store.get(ref) == content
    stored_files = [path for path in (root / "objects").rglob("*") if path.is_file()]
    assert stored_files == [object_path(root, ref.checksum_sha256)]
