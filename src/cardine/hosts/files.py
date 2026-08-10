"""Trusted-host capture, canonical storage, and explicit ingestion bridge.

This module deliberately owns no course state or event stream.  It is a small
operational boundary around the existing SourceInputPort and text ingestion
service: callers obtain an opaque descriptor, while only trusted host code can
resolve a descriptor back to explicitly marked untrusted content.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast

from study_agent.domain._validation import require_aware
from study_agent.domain.context import ExecutionContext
from study_agent.domain.identifiers import CourseId, SessionId, SourceId
from study_agent.hosts.contracts import MAX_HOST_FILES, HostFileDescriptor
from study_agent.ports.clock import ClockPort
from study_agent.ports.host_file import (
    HostFileIdentityPort,
    HostFileIngestionPort,
    HostFileSnapshotStore,
)
from study_agent.ports.source_input import (
    MAX_SOURCE_BYTES,
    MAX_TOTAL_SOURCE_BYTES,
    SourceInputPort,
)

_SCHEMA_VERSION = 1
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPES = {".txt": "text/plain", ".md": "text/markdown"}
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_SNAPSHOT_FIELDS = frozenset(
    {
        "schema_version",
        "file_id",
        "course_id",
        "session_id",
        "display_name",
        "media_type",
        "original_filename",
        "byte_size",
        "checksum_sha256",
        "content_base64",
        "captured_at",
        "expires_at",
    }
)


class HostFileError(ValueError):
    """A host-file operation failed closed before any external effect."""


@dataclass(frozen=True, init=False)
class HostFileSnapshot:
    """Immutable exact bytes and trust-owner binding for one host capture."""

    __slots__ = (
        "byte_size",
        "captured_at",
        "checksum_sha256",
        "content",
        "course_id",
        "display_name",
        "expires_at",
        "file_id",
        "media_type",
        "original_filename",
        "session_id",
    )

    file_id: str
    course_id: CourseId
    session_id: SessionId
    display_name: str
    media_type: str
    original_filename: str
    byte_size: int
    checksum_sha256: str
    content: bytes
    captured_at: datetime
    expires_at: datetime

    def __init__(
        self,
        file_id: str,
        course_id: CourseId,
        session_id: SessionId,
        display_name: str,
        media_type: str,
        original_filename: str,
        byte_size: int,
        checksum_sha256: str,
        content: bytes,
        captured_at: datetime,
        expires_at: datetime,
    ) -> None:
        _require_opaque(file_id, "file_id")
        if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
            raise TypeError("snapshot owners must use CourseId and SessionId")
        _require_name(display_name, "display_name")
        _require_filename(original_filename)
        expected_media = _MEDIA_TYPES.get(_suffix(original_filename))
        if media_type != expected_media:
            raise HostFileError("media type must agree with original filename extension")
        if type(content) is not bytes:
            raise HostFileError("snapshot content must be bytes")
        if type(byte_size) is not int or isinstance(byte_size, bool):
            raise HostFileError("snapshot byte_size must be an integer")
        if not 0 <= byte_size <= MAX_SOURCE_BYTES or byte_size != len(content):
            raise HostFileError("snapshot byte_size is outside the source bound")
        try:
            content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise HostFileError("snapshot content must contain strict UTF-8") from None
        if not isinstance(checksum_sha256, str) or _DIGEST.fullmatch(checksum_sha256) is None:
            raise HostFileError("snapshot checksum must be lowercase SHA-256")
        if checksum_sha256 != sha256(content).hexdigest():
            raise HostFileError("snapshot checksum does not match content")
        _require_utc_aware(captured_at, "captured_at")
        _require_utc_aware(expires_at, "expires_at")
        if captured_at >= expires_at:
            raise HostFileError("snapshot captured_at must precede expires_at")
        object.__setattr__(self, "file_id", file_id)
        object.__setattr__(self, "course_id", course_id)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "original_filename", original_filename)
        object.__setattr__(self, "byte_size", byte_size)
        object.__setattr__(self, "checksum_sha256", checksum_sha256)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "expires_at", expires_at)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, HostFileSnapshot) and self.to_bytes() == other.to_bytes()

    def __hash__(self) -> int:
        return hash(self.to_bytes())

    @property
    def reference(self) -> HostFileReference:
        return HostFileReference(
            self.course_id, self.session_id, self.file_id, self.checksum_sha256
        )

    @property
    def id(self) -> str:
        return self.file_id

    @property
    def original_basename(self) -> str:
        return self.original_filename

    @property
    def descriptor(self) -> HostFileDescriptor:
        return HostFileDescriptor(
            self.file_id,
            self.display_name,
            self.media_type,
            self.byte_size,
            self.checksum_sha256,
        )

    def to_bytes(self) -> bytes:
        return _canonical_bytes(
            {
                "schema_version": _SCHEMA_VERSION,
                "file_id": self.file_id,
                "course_id": str(self.course_id),
                "session_id": str(self.session_id),
                "display_name": self.display_name,
                "media_type": self.media_type,
                "original_filename": self.original_filename,
                "byte_size": self.byte_size,
                "checksum_sha256": self.checksum_sha256,
                "content_base64": base64.b64encode(self.content).decode("ascii"),
                "captured_at": _timestamp(self.captured_at),
                "expires_at": _timestamp(self.expires_at),
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> HostFileSnapshot:
        raw = _canonical_object(data, "host file snapshot")
        if set(raw) != _SNAPSHOT_FIELDS:
            raise HostFileError("host file snapshot has unknown or missing fields")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != _SCHEMA_VERSION:
            raise HostFileError("unsupported host file snapshot schema")
        try:
            encoded = raw["content_base64"]
            if not isinstance(encoded, str):
                raise ValueError
            content = base64.b64decode(encoded.encode("ascii"), validate=True)
            captured = _parse_timestamp(raw["captured_at"])
            expires = _parse_timestamp(raw["expires_at"])
            values = {
                key: raw[key]
                for key in (
                    "file_id",
                    "display_name",
                    "media_type",
                    "original_filename",
                    "byte_size",
                    "checksum_sha256",
                )
            }
            if not all(isinstance(values[key], (str, int)) for key in values):
                raise ValueError
            if not isinstance(raw["course_id"], str) or not isinstance(raw["session_id"], str):
                raise ValueError
            snapshot = cls(
                cast(str, values["file_id"]),
                CourseId(raw["course_id"]),
                SessionId(raw["session_id"]),
                cast(str, values["display_name"]),
                cast(str, values["media_type"]),
                cast(str, values["original_filename"]),
                cast(int, values["byte_size"]),
                cast(str, values["checksum_sha256"]),
                content,
                captured,
                expires,
            )
            if snapshot.to_bytes() != data:
                raise HostFileError("host file snapshot is not semantically canonical")
            return snapshot
        except (TypeError, ValueError, UnicodeError, binascii.Error) as error:
            if isinstance(error, HostFileError):
                raise
            raise HostFileError("invalid host file snapshot fields") from None


@dataclass(frozen=True, init=False)
class HostFileReference:
    """Minimal owner/checksum key accepted by trusted lookup."""

    __slots__ = ("checksum_sha256", "course_id", "file_id", "session_id")

    course_id: CourseId
    session_id: SessionId
    file_id: str
    checksum_sha256: str

    def __init__(
        self,
        course_id: CourseId,
        session_id: SessionId,
        file_id: str,
        checksum_sha256: str,
    ) -> None:
        if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
            raise TypeError("host file reference owners must use CourseId and SessionId")
        _require_opaque(file_id, "file_id")
        if _DIGEST.fullmatch(checksum_sha256) is None:
            raise HostFileError("reference checksum must be lowercase SHA-256")
        object.__setattr__(self, "course_id", course_id)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "file_id", file_id)
        object.__setattr__(self, "checksum_sha256", checksum_sha256)

    @property
    def id(self) -> str:
        return self.file_id

    @property
    def checksum(self) -> str:
        return self.checksum_sha256


@dataclass(frozen=True, init=False)
class UntrustedHostFileContent:
    """Exact bytes explicitly marked as untrusted document content."""

    __slots__ = (
        "checksum_sha256",
        "content",
        "display_name",
        "is_untrusted",
        "media_type",
        "original_filename",
    )

    content: bytes
    display_name: str
    media_type: str
    checksum_sha256: str
    original_filename: str
    is_untrusted: bool

    def __init__(
        self,
        content: bytes,
        display_name: str,
        media_type: str,
        checksum_sha256: str,
        original_filename: str,
    ) -> None:
        if type(content) is not bytes or _DIGEST.fullmatch(checksum_sha256) is None:
            raise HostFileError("invalid untrusted host file content")
        if checksum_sha256 != sha256(content).hexdigest():
            raise HostFileError("untrusted host file checksum does not match content")
        _require_name(display_name, "display_name")
        _require_filename(original_filename)
        if _MEDIA_TYPES.get(_suffix(original_filename)) != media_type:
            raise HostFileError("untrusted content media type does not match filename")
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "checksum_sha256", checksum_sha256)
        object.__setattr__(self, "original_filename", original_filename)
        object.__setattr__(self, "is_untrusted", True)

    @property
    def byte_size(self) -> int:
        return len(self.content)


@dataclass(frozen=True, init=False)
class TrustedHostFileIngestionCommand:
    """Host-authorized ingestion metadata; none is selected by a model."""

    __slots__ = (
        "context",
        "expected_sequence",
        "reference",
        "source_id",
        "source_role",
        "title",
        "trust_level",
    )

    reference: HostFileReference
    source_id: SourceId
    title: str
    trust_level: int
    source_role: str
    context: ExecutionContext
    expected_sequence: int | None

    def __init__(
        self,
        reference: HostFileReference,
        source_id: SourceId,
        title: str,
        trust_level: int,
        source_role: str,
        context: ExecutionContext,
        expected_sequence: int | None = None,
    ) -> None:
        if not isinstance(reference, HostFileReference):
            raise TypeError("ingestion command requires a host file reference")
        if not isinstance(source_id, SourceId):
            raise TypeError("ingestion command source_id must be SourceId")
        _require_name(title, "title")
        _require_name(source_role, "source_role")
        if type(trust_level) is not int or not 0 <= trust_level <= 100:
            raise HostFileError("trust_level must be between zero and 100")
        if not isinstance(context, ExecutionContext):
            raise TypeError("ingestion command context must be ExecutionContext")
        if context.course_id != reference.course_id or context.session_id != reference.session_id:
            raise HostFileError("ingestion command owner does not match context")
        if expected_sequence is not None and (
            type(expected_sequence) is not int or expected_sequence < 0
        ):
            raise HostFileError("expected_sequence must be non-negative or None")
        object.__setattr__(self, "reference", reference)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "trust_level", trust_level)
        object.__setattr__(self, "source_role", source_role)
        object.__setattr__(self, "context", context)
        object.__setattr__(self, "expected_sequence", expected_sequence)


class HostFileRegistry:
    """Capture, store, verify, and explicitly bridge host file snapshots."""

    def __init__(
        self,
        source_input: SourceInputPort,
        identity: HostFileIdentityPort,
        store: HostFileSnapshotStore,
        clock: ClockPort,
        ttl: timedelta,
        *,
        max_snapshot_count: int = MAX_HOST_FILES,
        max_aggregate_bytes: int = MAX_TOTAL_SOURCE_BYTES,
    ) -> None:
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise ValueError("host file ttl must be positive")
        if type(max_snapshot_count) is not int or not 0 < max_snapshot_count <= MAX_HOST_FILES:
            raise ValueError("max_snapshot_count exceeds MAX_HOST_FILES")
        if (
            type(max_aggregate_bytes) is not int
            or not 0 < max_aggregate_bytes <= MAX_TOTAL_SOURCE_BYTES
        ):
            raise ValueError("max_aggregate_bytes exceeds MAX_TOTAL_SOURCE_BYTES")
        self._source_input = source_input
        self._identity = identity
        self._store = store
        self._clock = clock
        self._ttl = ttl
        self._max_snapshot_count = max_snapshot_count
        self._max_aggregate_bytes = max_aggregate_bytes
        self._known: dict[str, tuple[CourseId, SessionId, int]] = {}

    def capture(
        self,
        relative_path: str,
        course_id: CourseId,
        session_id: SessionId,
        display_name: str,
    ) -> HostFileDescriptor:
        # This is intentionally the sole call and sole filesystem boundary.
        source = self._source_input.snapshot(relative_path)
        _require_name(display_name, "display_name")
        filename = source.filename
        media_type = _MEDIA_TYPES.get(_suffix(filename))
        if media_type is None:
            raise HostFileError("only .txt and .md host files are supported")
        declaration = sha256(
            _canonical_bytes(
                {
                    "relative_path": source.relative_path,
                    "display_name": display_name,
                    "media_type": media_type,
                }
            )
        ).hexdigest()
        now = self._clock.now()
        _require_utc_aware(now, "clock.now()")
        file_id = self._identity.issue(course_id, session_id, source.checksum_sha256, declaration)
        candidate = HostFileSnapshot(
            file_id,
            course_id,
            session_id,
            display_name,
            media_type,
            filename,
            source.byte_size,
            source.checksum_sha256,
            source.content,
            now,
            now + self._ttl,
        )
        try:
            existing_payload = self._store.load(file_id)
        except (KeyError, LookupError):
            existing_payload = None
        if existing_payload is not None:
            existing = _decode_or_fail(existing_payload)
            if now >= existing.expires_at:
                raise HostFileError("host file snapshot has expired")
            if not _same_capture(existing, candidate):
                # Exact retries must not rotate time or identity; any mismatch
                # indicates an issuer collision or changed owner/declaration.
                if _same_immutable_identity(existing, candidate):
                    raise HostFileError("host file identity collision or changed bytes")
                raise HostFileError("host file identity is already bound differently")
            return existing.descriptor
        if source.byte_size > MAX_SOURCE_BYTES:
            raise HostFileError("host file exceeds per-file byte limit")
        current_count, current_bytes = self._usage()
        if current_count >= self._max_snapshot_count:
            raise HostFileError("host snapshot count limit exceeded")
        if current_bytes + source.byte_size > self._max_aggregate_bytes:
            raise HostFileError("host snapshot aggregate byte limit exceeded")
        payload = candidate.to_bytes()
        try:
            created = self._store.create(file_id, payload)
        except (KeyError, LookupError, ValueError) as error:
            raise HostFileError(str(error)) from None
        if not created:
            try:
                existing = _decode_or_fail(self._store.load(file_id))
            except (KeyError, LookupError):
                raise HostFileError("host snapshot store lost a concurrent create") from None
            if not _same_capture(existing, candidate):
                raise HostFileError("host file identity collision or changed bytes")
            return existing.descriptor
        self._known[file_id] = (course_id, session_id, source.byte_size)
        return candidate.descriptor

    def lookup(self, reference: HostFileReference) -> UntrustedHostFileContent:
        snapshot = self._verified(reference)
        return UntrustedHostFileContent(
            snapshot.content,
            snapshot.display_name,
            snapshot.media_type,
            snapshot.checksum_sha256,
            snapshot.original_filename,
        )

    def ingest(
        self,
        command: TrustedHostFileIngestionCommand,
        ingestion: HostFileIngestionPort,
    ) -> object:
        if not isinstance(command, TrustedHostFileIngestionCommand):
            raise TypeError("host ingestion requires TrustedHostFileIngestionCommand")
        snapshot = self._verified(command.reference)
        return ingestion.ingest(
            filename=snapshot.original_filename,
            content=snapshot.content,
            source_id=command.source_id,
            title=command.title,
            trust_level=command.trust_level,
            source_role=command.source_role,
            context=command.context,
            expected_sequence=command.expected_sequence,
        )

    def _verified(self, reference: HostFileReference) -> HostFileSnapshot:
        if not isinstance(reference, HostFileReference):
            raise TypeError("host lookup requires HostFileReference")
        try:
            payload = self._store.load(reference.file_id)
        except (KeyError, LookupError):
            raise HostFileError("host file snapshot not found") from None
        snapshot = _decode_or_fail(payload)
        if (
            snapshot.file_id != reference.file_id
            or snapshot.course_id != reference.course_id
            or snapshot.session_id != reference.session_id
            or snapshot.checksum_sha256 != reference.checksum_sha256
        ):
            raise HostFileError("host file owner or checksum binding mismatch")
        now = self._clock.now()
        _require_utc_aware(now, "clock.now()")
        if now >= snapshot.expires_at:
            raise HostFileError("host file snapshot has expired")
        return snapshot

    def _usage(self) -> tuple[int, int]:
        count = getattr(self._store, "snapshot_count", None)
        total = getattr(self._store, "aggregate_bytes", None)
        if type(count) is int and type(total) is int:
            return count, total
        return len(self._known), sum(item[2] for item in self._known.values())


def _same_immutable_identity(left: HostFileSnapshot, right: HostFileSnapshot) -> bool:
    return (
        left.file_id == right.file_id
        and left.course_id == right.course_id
        and left.session_id == right.session_id
    )


def _same_capture(left: HostFileSnapshot, right: HostFileSnapshot) -> bool:
    return (
        _same_immutable_identity(left, right)
        and left.display_name == right.display_name
        and left.media_type == right.media_type
        and left.original_filename == right.original_filename
        and left.byte_size == right.byte_size
        and left.checksum_sha256 == right.checksum_sha256
        and left.content == right.content
    )


def _decode_or_fail(payload: bytes) -> HostFileSnapshot:
    try:
        return HostFileSnapshot.from_bytes(payload)
    except (TypeError, ValueError) as error:
        raise HostFileError("stored host file snapshot failed canonical validation") from error


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_object(data: bytes, name: str) -> dict[str, Any]:
    if type(data) is not bytes:
        raise HostFileError(f"{name} bytes must be bytes")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HostFileError(f"{name} is not canonical JSON") from error
    if not isinstance(value, dict) or _canonical_bytes(value) != data:
        raise HostFileError(f"{name} bytes are not canonical")
    return value


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise HostFileError("snapshot timestamp must be text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HostFileError("snapshot timestamp is invalid") from error
    _require_utc_aware(parsed, "snapshot timestamp")
    if _timestamp(parsed) != value:
        raise HostFileError("snapshot timestamp is not canonical")
    return parsed


def _require_utc_aware(value: datetime, name: str) -> None:
    try:
        require_aware(value, name)
    except (TypeError, ValueError):
        raise HostFileError(f"{name} must be timezone-aware UTC") from None
    if value.utcoffset() != timedelta(0):
        raise HostFileError(f"{name} must be timezone-aware UTC")


def _require_opaque(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 255
        or any(unicodedata.category(character).startswith("C") for character in value)
        or any(character.isspace() for character in value)
        or "/" in value
        or "\\" in value
    ):
        raise HostFileError(f"{name} must be opaque")


def _require_name(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 255
        or "/" in value
        or "\\" in value
        or ":" in value
        or value in {".", ".."}
        or value.endswith((" ", "."))
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise HostFileError(f"{name} must be a sanitized display value")


def _require_filename(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 255
        or "/" in value
        or "\\" in value
        or ":" in value
        or value in {".", ".."}
        or value.endswith((" ", "."))
        or value.split(".", 1)[0].upper() in _WINDOWS_DEVICE_NAMES
        or any(unicodedata.category(character).startswith("C") for character in value)
        or _suffix(value) not in _MEDIA_TYPES
    ):
        raise HostFileError("original filename must be a portable .txt or .md basename")


def _suffix(value: str) -> str:
    index = value.rfind(".")
    return value[index:] if index >= 0 else ""


__all__ = [
    "HostFileError",
    "HostFileReference",
    "HostFileRegistry",
    "HostFileSnapshot",
    "TrustedHostFileIngestionCommand",
    "UntrustedHostFileContent",
]
