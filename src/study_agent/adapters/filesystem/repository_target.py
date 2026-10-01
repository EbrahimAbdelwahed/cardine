"""Read-only, descriptor-anchored resolution for local repository targets."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
import threading
import unicodedata
import weakref
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from study_agent.repository_config import (
    CONFIG_FILENAME,
    MAX_CONFIG_BYTES,
    LocalConfigError,
    LocalRepositoryConfig,
)

_STATE_DIRECTORY = "state"
_BLOB_DIRECTORY = "blobs"
_EXPORT_DIRECTORY = "exports"
_EVENT_DATABASE = "events.sqlite3"
_RUN_DATABASE = "runs.sqlite3"
_RETRIEVAL_DATABASE = "retrieval.sqlite3"
_REPOSITORY_LOCK = ".study-agent.lock"
_CONFIG_TEMPORARY = ".study-agent.json.tmp"
_MUTATION_CWD_LOCK = threading.RLock()
_MUTATION_CWD_ACTIVE = False

_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


class RepositoryTargetError(RuntimeError):
    """The requested repository target is not a safe local directory target."""


LocalRepositoryError = RepositoryTargetError


class RepositoryTargetInspectionCode(StrEnum):
    """Stable outcomes from read-only repository compatibility inspection."""

    ABSENT = "repository_absent"
    COMPATIBLE = "repository_compatible"
    CONFLICT = "repository_incompatible"


@dataclass(frozen=True, slots=True)
class LocalRepositoryPaths:
    """The complete versioned filesystem layout beneath one repository root."""

    root: Path
    config: Path
    state: Path
    events: Path
    runs: Path
    retrieval: Path
    blobs: Path
    exports: Path

    @classmethod
    def at(cls, root: str | Path) -> LocalRepositoryPaths:
        base = Path(root).expanduser()
        state = base / _STATE_DIRECTORY
        return cls(
            base,
            base / CONFIG_FILENAME,
            state,
            state / _EVENT_DATABASE,
            state / _RUN_DATABASE,
            state / _RETRIEVAL_DATABASE,
            base / _BLOB_DIRECTORY,
            base / _EXPORT_DIRECTORY,
        )


@dataclass(frozen=True, slots=True)
class RepositoryTargetInspection:
    """Immutable compatibility result for one previously resolved target."""

    code: RepositoryTargetInspectionCode
    paths: LocalRepositoryPaths
    observation: RepositoryObservationHandle | None = None


class RepositoryObservationHandle:
    """Retained, identity-bound descriptors for one repository observation."""

    def __init__(
        self,
        target: ResolvedRepositoryTarget,
        descriptors: dict[str, int | None],
        identities: dict[str, tuple[int, int] | None],
        expected_config: LocalRepositoryConfig,
    ) -> None:
        self._target = target
        self._descriptors = descriptors
        self._identities = identities
        self._expected_config = expected_config
        self._mutation_scope_active = False
        owned = tuple(value for value in descriptors.values() if value is not None)
        self._finalizer = weakref.finalize(self, _close_descriptors, owned)
        self._adopted_finalizers: list[weakref.finalize[..., object]] = []

    @property
    def paths(self) -> LocalRepositoryPaths:
        """Return display-only lexical paths; observation never reopens them."""
        return self._target.paths

    def directory_descriptor(self, name: str) -> int:
        """Duplicate a retained directory descriptor for an adapter owner."""
        if name not in {"blobs", "state"}:
            raise ValueError("unsupported repository directory descriptor")
        return os.dup(self._required_descriptor(name))

    def mutation_paths(self) -> LocalRepositoryPaths:
        """Return verified display paths for this retained repository owner."""
        self.verify_binding()
        return self._target.paths

    @contextmanager
    def mutation_scope(self) -> Iterator[None]:
        """Pin lifecycle SQLite opens to the retained state directory.

        This is a CLI composition seam: changing the process working directory is
        process-global, so every lifecycle caller is serialized by one process-wide
        lock. The prior working directory is restored on both success and failure.
        SQLite adapters must be constructed and used entirely inside this scope.
        """
        global _MUTATION_CWD_ACTIVE

        if not self._finalizer.alive:
            raise RepositoryTargetError("repository observation handle is closed")
        with _MUTATION_CWD_LOCK:
            if _MUTATION_CWD_ACTIVE:
                raise RepositoryTargetError("repository mutation scope is already active")
            _MUTATION_CWD_ACTIVE = True
            try:
                previous_directory = os.open(".", _DIRECTORY_OPEN_FLAGS)
                changed_directory = False
                try:
                    self.verify_binding()
                    self._ensure_database_bindings()
                    os.fchdir(self._required_descriptor("state"))
                    changed_directory = True
                    self.verify_binding()
                    self._mutation_scope_active = True
                    try:
                        yield
                    finally:
                        self.adopt_created_database_bindings()
                        self.verify_binding()
                finally:
                    self._mutation_scope_active = False
                    try:
                        if changed_directory:
                            os.fchdir(previous_directory)
                    finally:
                        os.close(previous_directory)
            finally:
                _MUTATION_CWD_ACTIVE = False

    def mutation_database_path(self, name: str) -> Path:
        """Return a canonical relative SQLite name inside an active mutation scope."""
        entries = {
            "events": _EVENT_DATABASE,
            "runs": _RUN_DATABASE,
            "retrieval": _RETRIEVAL_DATABASE,
        }
        try:
            entry = entries[name]
        except KeyError:
            raise ValueError("unsupported repository database") from None
        if not self._mutation_scope_active:
            raise RepositoryTargetError("repository mutation scope is not active")
        return Path(entry)

    def database_connection_identity(self, name: str) -> tuple[int, int]:
        """Return the retained identity for one mutable database entry."""
        if name not in {"events", "runs", "retrieval"}:
            raise ValueError("unsupported repository database")
        if not self._mutation_scope_active:
            raise RepositoryTargetError("repository mutation scope is not active")
        descriptor = self._required_descriptor(name)
        identity = self._identities[name]
        if identity is None:
            raise RepositoryTargetError("repository database binding is unavailable")
        _verify_descriptor_identity(descriptor, identity, directory=False)
        return identity

    def _ensure_database_bindings(self) -> None:
        """Create absent database entries with no-follow openat before SQLite runs."""
        state_descriptor = self._required_descriptor("state")
        created_any = False
        for name, entry in (
            ("events", _EVENT_DATABASE),
            ("runs", _RUN_DATABASE),
            ("retrieval", _RETRIEVAL_DATABASE),
        ):
            if self._descriptors[name] is not None:
                continue
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            try:
                descriptor = os.open(entry, flags, 0o600, dir_fd=state_descriptor)
            except FileExistsError:
                raise RepositoryTargetError("repository database binding changed") from None
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise RepositoryTargetError("repository database path is incompatible")
                identity = (metadata.st_dev, metadata.st_ino)
                _verify_regular_binding(
                    state_descriptor, entry, identity, "repository database"
                )
                os.fsync(descriptor)
            except BaseException:
                os.close(descriptor)
                raise
            self._descriptors[name] = descriptor
            self._identities[name] = identity
            self._adopted_finalizers.append(weakref.finalize(self, os.close, descriptor))
            created_any = True
        if created_any:
            os.fsync(state_descriptor)
        self.verify_binding()

    def adopt_created_database_bindings(self) -> None:
        """Pin databases created by a mutation and reject replaced existing ones."""
        if not self._finalizer.alive:
            raise RepositoryTargetError("repository observation handle is closed")
        self._verify_retained_owner()
        state_descriptor = self._required_descriptor("state")
        for name, entry in (
            ("events", _EVENT_DATABASE),
            ("runs", _RUN_DATABASE),
            ("retrieval", _RETRIEVAL_DATABASE),
        ):
            descriptor = self._descriptors[name]
            identity = self._identities[name]
            if descriptor is not None:
                if identity is None:
                    raise RepositoryTargetError("repository observation binding is invalid")
                _verify_descriptor_identity(descriptor, identity, directory=False)
                _verify_regular_binding(state_descriptor, entry, identity, "repository database")
                continue
            created = _open_optional_regular(state_descriptor, entry)
            if created is None:
                continue
            metadata = os.fstat(created)
            created_identity = (metadata.st_dev, metadata.st_ino)
            try:
                _verify_regular_binding(
                    state_descriptor,
                    entry,
                    created_identity,
                    "repository database",
                )
            except BaseException:
                os.close(created)
                raise
            self._descriptors[name] = created
            self._identities[name] = created_identity
            self._adopted_finalizers.append(weakref.finalize(self, os.close, created))
        self.verify_binding()

    def database_descriptor_path(self, name: str) -> Path | None:
        """Return an identity-checked process-local path to a retained database fd."""
        if name not in {"events", "retrieval"}:
            raise ValueError("unsupported repository database descriptor")
        descriptor = self._descriptors[name]
        if descriptor is None:
            return None
        return _stable_descriptor_path(descriptor)

    def verify_binding(self) -> None:
        """Revalidate every retained descriptor and its repository entry binding."""
        if not self._finalizer.alive:
            raise RepositoryTargetError("repository observation handle is closed")
        self._verify_retained_owner()
        state_descriptor = self._required_descriptor("state")
        for name, entry in (
            ("events", _EVENT_DATABASE),
            ("runs", _RUN_DATABASE),
            ("retrieval", _RETRIEVAL_DATABASE),
        ):
            identity = self._identities[name]
            descriptor = self._descriptors[name]
            if descriptor is None:
                if not _entry_is_absent(state_descriptor, entry):
                    raise RepositoryTargetError("repository database binding changed")
                continue
            if identity is None:
                raise RepositoryTargetError("repository observation binding is invalid")
            _verify_descriptor_identity(descriptor, identity, directory=False)
            _verify_regular_binding(state_descriptor, entry, identity, "repository database")

    def _verify_retained_owner(self) -> None:
        _verify_complete_binding(self._target, self._target.existing_prefix_identities)
        target_descriptor = self._required_descriptor("target")
        if _read_config(target_descriptor, missing_ok=False) != self._expected_config:
            raise RepositoryTargetError("repository configuration changed")
        for name, entry, label in (
            ("config", CONFIG_FILENAME, "repository configuration"),
            ("lock", _REPOSITORY_LOCK, "repository lock"),
        ):
            identity = self._identities[name]
            if identity is None:
                raise RepositoryTargetError("repository observation binding is invalid")
            _verify_descriptor_identity(self._required_descriptor(name), identity, directory=False)
            _verify_regular_binding(target_descriptor, entry, identity, label)
        for name, entry in (
            ("state", _STATE_DIRECTORY),
            ("blobs", _BLOB_DIRECTORY),
            ("exports", _EXPORT_DIRECTORY),
        ):
            identity = self._identities[name]
            if identity is None:
                raise RepositoryTargetError("repository observation binding is invalid")
            _verify_descriptor_identity(self._required_descriptor(name), identity, directory=True)
            _verify_child_binding(target_descriptor, entry, identity)

    def close(self) -> None:
        """Release all descriptors retained by this observation capability."""
        for finalizer in reversed(self._adopted_finalizers):
            finalizer()
        self._finalizer()

    def __enter__(self) -> RepositoryObservationHandle:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _required_descriptor(self, name: str) -> int:
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            raise RepositoryTargetError("repository observation descriptor is unavailable")
        return descriptor


@dataclass(frozen=True, slots=True)
class ResolvedRepositoryTarget:
    """Lexical target plus identities observed through no-follow directory fds."""

    trusted_root: Path
    relative_parts: tuple[str, ...]
    verified_root_identity: tuple[int, int]
    existing_parts: tuple[str, ...]
    existing_prefix_identities: tuple[tuple[int, int], ...]
    missing_parts: tuple[str, ...]

    @property
    def root(self) -> Path:
        """Return the lexical repository root without resolving it again."""
        return self.trusted_root.joinpath(*self.relative_parts)

    @property
    def paths(self) -> LocalRepositoryPaths:
        """Return layout paths rooted at the lexical repository target."""
        return LocalRepositoryPaths.at(self.root)

    @property
    def exists(self) -> bool:
        """Report whether the complete target existed during inspection."""
        return not self.missing_parts


def resolve_repository_target(
    trusted_root: str | Path, relative_path: str | Path
) -> ResolvedRepositoryTarget:
    """Inspect a portable relative target beneath an explicit trusted root.

    Resolution is read-only. Existing target components are opened relative to
    the preceding verified directory descriptor and never followed through a
    symlink. The first missing component ends inspection and the complete
    unobserved tail is retained for a later fd-relative initializer.
    """

    root = _absolute_lexical_path(trusted_root)
    parts = _portable_relative_parts(relative_path)
    descriptors: list[int] = []
    existing_parts: list[str] = []
    identities: list[tuple[int, int]] = []
    try:
        root_descriptor = _open_directory(root)
        descriptors.append(root_descriptor)
        root_identity = _directory_identity(root_descriptor)

        parent_descriptor = root_descriptor
        for index, component in enumerate(parts):
            try:
                descriptor = os.open(
                    component,
                    _DIRECTORY_OPEN_FLAGS,
                    dir_fd=parent_descriptor,
                )
            except OSError as error:
                if error.errno == errno.ENOENT:
                    return ResolvedRepositoryTarget(
                        root,
                        parts,
                        root_identity,
                        tuple(existing_parts),
                        tuple(identities),
                        parts[index:],
                    )
                raise RepositoryTargetError(
                    "repository target contains an incompatible component"
                ) from None
            descriptors.append(descriptor)
            identities.append(_directory_identity(descriptor))
            existing_parts.append(component)
            parent_descriptor = descriptor

        return ResolvedRepositoryTarget(
            root,
            parts,
            root_identity,
            tuple(existing_parts),
            tuple(identities),
            (),
        )
    finally:
        for descriptor in reversed(descriptors):
            with suppress(OSError):
                os.close(descriptor)


def resolve_explicit_repository_target(path: str | Path) -> ResolvedRepositoryTarget:
    """Resolve a trusted host path after lexical normalization."""

    raw = os.fspath(path)
    if not isinstance(raw, str):
        raise RepositoryTargetError("repository target path must be text")
    normalized = os.path.abspath(os.path.expanduser(raw))
    anchor = Path(normalized).anchor
    tail = normalized[len(anchor) :]
    return resolve_repository_target(anchor, tail)


def inspect_repository_target(
    target: ResolvedRepositoryTarget, expected_config: LocalRepositoryConfig
) -> RepositoryTargetInspection:
    """Inspect repository compatibility without re-resolving or mutating it.

    The target identities captured during resolution are revalidated through
    no-follow directory descriptors. A target that appeared, disappeared, or
    changed identity after resolution is a stable conflict rather than a new
    observation of an untrusted path.
    """

    _validate_resolved_target(target)
    descriptors: list[int] = []
    try:
        root_descriptor = _open_directory(target.trusted_root)
        descriptors.append(root_descriptor)
        if _directory_identity(root_descriptor) != target.verified_root_identity:
            return _repository_inspection(target, RepositoryTargetInspectionCode.CONFLICT)

        parent_descriptor = root_descriptor
        observed_identities: list[tuple[int, int]] = []
        for component, expected_identity in zip(
            target.existing_parts,
            target.existing_prefix_identities,
            strict=True,
        ):
            descriptor = _openat_directory(parent_descriptor, component)
            descriptors.append(descriptor)
            identity = _directory_identity(descriptor)
            if identity != expected_identity:
                return _repository_inspection(target, RepositoryTargetInspectionCode.CONFLICT)
            observed_identities.append(identity)
            parent_descriptor = descriptor

        if target.missing_parts:
            if _entry_is_absent(parent_descriptor, target.missing_parts[0]):
                return _repository_inspection(target, RepositoryTargetInspectionCode.ABSENT)
            return _repository_inspection(target, RepositoryTargetInspectionCode.CONFLICT)

        observed_config = _read_config(parent_descriptor, missing_ok=False)
        if observed_config != expected_config:
            return _repository_inspection(target, RepositoryTargetInspectionCode.CONFLICT)
        _validate_layout_fd(parent_descriptor)
        if _read_config(parent_descriptor, missing_ok=False) != expected_config:
            return _repository_inspection(target, RepositoryTargetInspectionCode.CONFLICT)
        _verify_complete_binding(target, tuple(observed_identities))
        observation = _open_observation_handle(
            target,
            parent_descriptor,
            tuple(observed_identities),
            expected_config,
        )
        try:
            observation.verify_binding()
            if _read_config(parent_descriptor, missing_ok=False) != expected_config:
                raise RepositoryTargetError("repository configuration changed")
        except BaseException:
            observation.close()
            raise
        return _repository_inspection(
            target, RepositoryTargetInspectionCode.COMPATIBLE, observation
        )
    except (LocalConfigError, OSError, RepositoryTargetError):
        return _repository_inspection(target, RepositoryTargetInspectionCode.CONFLICT)
    finally:
        for descriptor in reversed(descriptors):
            with suppress(OSError):
                os.close(descriptor)


def _repository_inspection(
    target: ResolvedRepositoryTarget,
    code: RepositoryTargetInspectionCode,
    observation: RepositoryObservationHandle | None = None,
) -> RepositoryTargetInspection:
    return RepositoryTargetInspection(code=code, paths=target.paths, observation=observation)


def initialize_repository_target(
    target: ResolvedRepositoryTarget, config: LocalRepositoryConfig
) -> LocalRepositoryPaths:
    """Initialize a resolved target without re-resolving its untrusted path."""

    _validate_resolved_target(target)
    descriptors: list[int] = []
    owned_tail: list[tuple[int, str, tuple[int, int]]] = []
    created_layout: list[tuple[int, str, tuple[int, int]]] = []
    lock_descriptor: int | None = None
    lock_created = False
    lock_identity: tuple[int, int] | None = None
    target_descriptor: int | None = None
    temporary_created = False
    temporary_identity: tuple[int, int] | None = None
    config_created = False
    binding_failed = False
    try:
        root_descriptor = _open_directory(target.trusted_root)
        descriptors.append(root_descriptor)
        if _directory_identity(root_descriptor) != target.verified_root_identity:
            raise RepositoryTargetError("repository target identity changed")

        parent_descriptor = root_descriptor
        observed_identities: list[tuple[int, int]] = []
        for component, expected in zip(
            target.existing_parts, target.existing_prefix_identities, strict=True
        ):
            descriptor = _openat_directory(parent_descriptor, component)
            descriptors.append(descriptor)
            identity = _directory_identity(descriptor)
            if identity != expected:
                raise RepositoryTargetError("repository target identity changed")
            observed_identities.append(identity)
            parent_descriptor = descriptor

        for component in target.missing_parts:
            created = False
            try:
                os.mkdir(component, 0o700, dir_fd=parent_descriptor)
                created = True
            except FileExistsError:
                pass
            descriptor = _openat_directory(parent_descriptor, component)
            descriptors.append(descriptor)
            identity = _directory_identity(descriptor)
            if created:
                owned_tail.append((parent_descriptor, component, identity))
                os.fsync(descriptor)
                os.fsync(parent_descriptor)
            _verify_child_binding(parent_descriptor, component, identity)
            observed_identities.append(identity)
            _verify_prefix_binding(
                target,
                target.relative_parts[: len(observed_identities)],
                tuple(observed_identities),
            )
            parent_descriptor = descriptor

        target_descriptor = parent_descriptor
        _verify_complete_binding(target, tuple(observed_identities))
        initial_entries = _directory_entries(target_descriptor)
        if initial_entries and _REPOSITORY_LOCK not in initial_entries:
            raise RepositoryTargetError("refusing to initialize a non-empty directory")

        lock_flags = (
            os.O_RDWR
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            lock_descriptor = os.open(
                _REPOSITORY_LOCK,
                lock_flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=target_descriptor,
            )
            lock_created = True
        except FileExistsError:
            lock_descriptor = os.open(_REPOSITORY_LOCK, lock_flags, dir_fd=target_descriptor)
        if not stat.S_ISREG(os.fstat(lock_descriptor).st_mode):
            raise RepositoryTargetError("repository lock is incompatible")
        lock_metadata = os.fstat(lock_descriptor)
        lock_identity = (lock_metadata.st_dev, lock_metadata.st_ino)
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        _verify_regular_binding(
            target_descriptor, _REPOSITORY_LOCK, lock_identity, "repository lock"
        )
        os.fsync(lock_descriptor)
        os.fsync(target_descriptor)

        existing_config = _read_config(target_descriptor, missing_ok=True)
        if existing_config is not None:
            if existing_config != config:
                raise RepositoryTargetError("repository configuration is incompatible")
            _remove_verified_regular(target_descriptor, _CONFIG_TEMPORARY)
            os.fsync(target_descriptor)
            if _read_config(target_descriptor, missing_ok=False) != config:
                raise RepositoryTargetError("repository configuration is incompatible")
            _validate_layout_fd(target_descriptor)
            _verify_complete_binding(target, tuple(observed_identities))
            return target.paths

        allowed_recovery = {
            _REPOSITORY_LOCK,
            _CONFIG_TEMPORARY,
            _STATE_DIRECTORY,
            _BLOB_DIRECTORY,
            _EXPORT_DIRECTORY,
        }
        current_entries = _directory_entries(target_descriptor)
        if current_entries - allowed_recovery:
            raise RepositoryTargetError(
                "interrupted repository initialization contains unknown paths"
            )
        _remove_verified_regular(target_descriptor, _CONFIG_TEMPORARY)
        for directory_name in (
            _STATE_DIRECTORY,
            _BLOB_DIRECTORY,
            _EXPORT_DIRECTORY,
        ):
            directory_descriptor, created = _ensure_empty_directory(
                target_descriptor, directory_name
            )
            descriptors.append(directory_descriptor)
            identity = _directory_identity(directory_descriptor)
            if created:
                created_layout.append((target_descriptor, directory_name, identity))
            os.fsync(directory_descriptor)
        os.fsync(target_descriptor)

        payload = config.to_bytes()
        temporary_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        temporary_descriptor = os.open(
            _CONFIG_TEMPORARY,
            temporary_flags,
            0o600,
            dir_fd=target_descriptor,
        )
        temporary_created = True
        try:
            _write_all(temporary_descriptor, payload)
            os.fsync(temporary_descriptor)
            temporary_metadata = os.fstat(temporary_descriptor)
            temporary_identity = (
                temporary_metadata.st_dev,
                temporary_metadata.st_ino,
            )
        finally:
            os.close(temporary_descriptor)

        _verify_complete_binding(target, tuple(observed_identities))
        _verify_regular_binding(
            target_descriptor,
            _REPOSITORY_LOCK,
            lock_identity,
            "repository lock",
        )
        _verify_regular_binding(
            target_descriptor,
            _CONFIG_TEMPORARY,
            temporary_identity,
            "temporary configuration path",
        )
        try:
            os.link(
                _CONFIG_TEMPORARY,
                CONFIG_FILENAME,
                src_dir_fd=target_descriptor,
                dst_dir_fd=target_descriptor,
                follow_symlinks=False,
            )
            config_created = True
            _verify_regular_binding(
                target_descriptor,
                CONFIG_FILENAME,
                temporary_identity,
                "repository configuration",
            )
        except FileExistsError:
            existing_config = _read_config(target_descriptor, missing_ok=False)
            if existing_config != config:
                raise RepositoryTargetError("repository configuration is incompatible") from None
        _remove_verified_regular(target_descriptor, _CONFIG_TEMPORARY)
        temporary_created = False
        os.fsync(target_descriptor)
        _verify_complete_binding(target, tuple(observed_identities))
        _validate_layout_fd(target_descriptor)
        return target.paths
    except RepositoryTargetError as error:
        binding_failed = "identity changed" in str(error)
        _rollback_created(
            target_descriptor,
            temporary_created,
            temporary_identity,
            config_created,
            created_layout,
            owned_tail,
            lock_created and binding_failed,
            lock_identity,
        )
        raise
    except (LocalConfigError, OSError):
        _rollback_created(
            target_descriptor,
            temporary_created,
            temporary_identity,
            config_created,
            created_layout,
            owned_tail,
            lock_created and binding_failed,
            lock_identity,
        )
        raise RepositoryTargetError("repository layout could not be initialized") from None
    finally:
        if lock_descriptor is not None:
            with suppress(OSError):
                os.close(lock_descriptor)
        for descriptor in reversed(descriptors):
            with suppress(OSError):
                os.close(descriptor)


def initialize_local_repository(
    root: str | Path, config: LocalRepositoryConfig
) -> LocalRepositoryPaths:
    """Compatibility entry point for an explicit trusted host path."""

    return initialize_repository_target(resolve_explicit_repository_target(root), config)


def validate_local_repository_layout(paths: LocalRepositoryPaths) -> None:
    """Validate an opened repository layout for compatibility."""

    for directory in (paths.root, paths.state, paths.blobs, paths.exports):
        if directory.is_symlink() or not directory.is_dir():
            raise RepositoryTargetError("repository layout contains an incompatible directory")
    if paths.config.is_symlink() or not paths.config.is_file():
        raise RepositoryTargetError("repository configuration is incompatible")
    lock = paths.root / _REPOSITORY_LOCK
    if lock.is_symlink() or not lock.is_file():
        raise RepositoryTargetError("repository lock is incompatible")
    for database in (paths.events, paths.runs, paths.retrieval):
        if database.is_symlink() or (database.exists() and not database.is_file()):
            raise RepositoryTargetError("repository database path is incompatible")


def _openat_directory(parent_descriptor: int, name: str) -> int:
    try:
        return os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor)
    except OSError:
        raise RepositoryTargetError(
            "repository target contains an incompatible component"
        ) from None


def _verify_child_binding(parent_descriptor: int, name: str, expected: tuple[int, int]) -> None:
    try:
        descriptor = _openat_directory(parent_descriptor, name)
    except RepositoryTargetError:
        raise RepositoryTargetError("repository target identity changed") from None
    try:
        if _directory_identity(descriptor) != expected:
            raise RepositoryTargetError("repository target identity changed")
    finally:
        os.close(descriptor)


def _verify_complete_binding(
    target: ResolvedRepositoryTarget, expected_identities: tuple[tuple[int, int], ...]
) -> None:
    if len(expected_identities) != len(target.relative_parts):
        raise RepositoryTargetError("repository target identity changed")
    _verify_prefix_binding(target, target.relative_parts, expected_identities)


def _verify_prefix_binding(
    target: ResolvedRepositoryTarget,
    components: tuple[str, ...],
    expected_identities: tuple[tuple[int, int], ...],
) -> None:
    descriptors: list[int] = []
    try:
        root_descriptor = _open_directory(target.trusted_root)
        descriptors.append(root_descriptor)
        if _directory_identity(root_descriptor) != target.verified_root_identity:
            raise RepositoryTargetError("repository target identity changed")
        parent_descriptor = root_descriptor
        for component, expected in zip(components, expected_identities, strict=True):
            descriptor = _openat_directory(parent_descriptor, component)
            descriptors.append(descriptor)
            if _directory_identity(descriptor) != expected:
                raise RepositoryTargetError("repository target identity changed")
            parent_descriptor = descriptor
    except RepositoryTargetError:
        raise RepositoryTargetError("repository target identity changed") from None
    finally:
        for descriptor in reversed(descriptors):
            with suppress(OSError):
                os.close(descriptor)


def _directory_entries(descriptor: int) -> set[str]:
    try:
        return set(os.listdir(descriptor))
    except OSError:
        raise RepositoryTargetError("repository directory could not be inspected") from None


def _entry_is_absent(directory_descriptor: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return True
        raise RepositoryTargetError("repository target could not be inspected") from None
    return False


def _read_config(directory_descriptor: int, *, missing_ok: bool) -> LocalRepositoryConfig | None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(CONFIG_FILENAME, flags, dir_fd=directory_descriptor)
    except OSError as error:
        if missing_ok and error.errno == errno.ENOENT:
            return None
        raise RepositoryTargetError("repository configuration is incompatible") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_CONFIG_BYTES:
            raise RepositoryTargetError("repository configuration is incompatible")
        chunks: list[bytes] = []
        remaining = MAX_CONFIG_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        try:
            final_name = os.stat(
                CONFIG_FILENAME,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            raise RepositoryTargetError("repository configuration is incompatible") from None
        if (
            _stable_file_identity(before) != _stable_file_identity(after)
            or (before.st_dev, before.st_ino) != (final_name.st_dev, final_name.st_ino)
            or not stat.S_ISREG(final_name.st_mode)
            or len(payload) > MAX_CONFIG_BYTES
        ):
            raise RepositoryTargetError("repository configuration is incompatible")
        try:
            return LocalRepositoryConfig.from_bytes(payload)
        except LocalConfigError:
            raise RepositoryTargetError("repository configuration is incompatible") from None
    finally:
        os.close(descriptor)


def _remove_verified_regular(directory_descriptor: int, name: str) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return
        raise RepositoryTargetError("temporary configuration path is incompatible") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RepositoryTargetError("temporary configuration path is incompatible")
        current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise RepositoryTargetError("temporary configuration path is incompatible")
        os.unlink(name, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
    finally:
        os.close(descriptor)


def _verify_regular_binding(
    directory_descriptor: int,
    name: str,
    expected: tuple[int, int] | None,
    label: str,
) -> None:
    if expected is None:
        raise RepositoryTargetError(f"{label} is incompatible")
    try:
        metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except OSError:
        raise RepositoryTargetError(f"{label} is incompatible") from None
    if not stat.S_ISREG(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != expected:
        raise RepositoryTargetError(f"{label} is incompatible")


def _ensure_empty_directory(parent_descriptor: int, name: str) -> tuple[int, bool]:
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        created = True
    except FileExistsError:
        pass
    descriptor = _openat_directory(parent_descriptor, name)
    if _directory_entries(descriptor):
        os.close(descriptor)
        raise RepositoryTargetError("interrupted repository initialization is incompatible")
    _verify_child_binding(parent_descriptor, name, _directory_identity(descriptor))
    if created:
        os.fsync(parent_descriptor)
    return descriptor, created


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("configuration write did not progress")
        offset += written


def _rollback_created(
    target_descriptor: int | None,
    temporary_created: bool,
    temporary_identity: tuple[int, int] | None,
    config_created: bool,
    created_layout: list[tuple[int, str, tuple[int, int]]],
    owned_tail: list[tuple[int, str, tuple[int, int]]],
    remove_lock: bool,
    lock_identity: tuple[int, int] | None,
) -> None:
    if target_descriptor is not None:
        if config_created and temporary_identity is not None:
            _remove_owned_regular(target_descriptor, CONFIG_FILENAME, temporary_identity)
        if temporary_created and temporary_identity is not None:
            _remove_owned_regular(target_descriptor, _CONFIG_TEMPORARY, temporary_identity)
        for parent, name, identity in reversed(created_layout):
            _remove_owned_directory(parent, name, identity)
        if remove_lock and lock_identity is not None:
            _remove_owned_regular(target_descriptor, _REPOSITORY_LOCK, lock_identity)
    for parent, name, identity in reversed(owned_tail):
        _remove_owned_directory(parent, name, identity)


def _remove_owned_regular(parent_descriptor: int, name: str, identity: tuple[int, int]) -> None:
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError:
        return
    if (current.st_dev, current.st_ino) != identity or not stat.S_ISREG(current.st_mode):
        return
    with suppress(OSError):
        os.unlink(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)


def _remove_owned_directory(parent_descriptor: int, name: str, identity: tuple[int, int]) -> None:
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError:
        return
    if (current.st_dev, current.st_ino) != identity or not stat.S_ISDIR(current.st_mode):
        return
    with suppress(OSError):
        os.rmdir(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)


def _validate_layout_fd(target_descriptor: int) -> None:
    for name in (_STATE_DIRECTORY, _BLOB_DIRECTORY, _EXPORT_DIRECTORY):
        descriptor = _openat_directory(target_descriptor, name)
        os.close(descriptor)
    _read_config(target_descriptor, missing_ok=False)
    lock_flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(_REPOSITORY_LOCK, lock_flags, dir_fd=target_descriptor)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RepositoryTargetError("repository lock is incompatible")
    finally:
        os.close(descriptor)
    state_descriptor = _openat_directory(target_descriptor, _STATE_DIRECTORY)
    try:
        for database in (_EVENT_DATABASE, _RUN_DATABASE, _RETRIEVAL_DATABASE):
            try:
                metadata = os.stat(database, dir_fd=state_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise RepositoryTargetError("repository database path is incompatible")
    finally:
        os.close(state_descriptor)


def _open_observation_handle(
    target: ResolvedRepositoryTarget,
    target_descriptor: int,
    observed_identities: tuple[tuple[int, int], ...],
    expected_config: LocalRepositoryConfig,
) -> RepositoryObservationHandle:
    """Retain the verified repository entries without reopening lexical paths."""
    if observed_identities != target.existing_prefix_identities:
        raise RepositoryTargetError("repository target identity changed")
    opened: dict[str, int | None] = {}
    identities: dict[str, tuple[int, int] | None] = {}
    try:
        target_duplicate = os.dup(target_descriptor)
        opened["target"] = target_duplicate
        identities["target"] = _directory_identity(target_duplicate)
        for name, entry, label in (
            ("config", CONFIG_FILENAME, "repository configuration"),
            ("lock", _REPOSITORY_LOCK, "repository lock"),
        ):
            descriptor = _open_optional_regular(target_descriptor, entry)
            if descriptor is None:
                raise RepositoryTargetError(f"{label} is incompatible")
            opened[name] = descriptor
            metadata = os.fstat(descriptor)
            identity = (metadata.st_dev, metadata.st_ino)
            identities[name] = identity
            _verify_regular_binding(target_descriptor, entry, identity, label)
        for name, entry in (
            ("state", _STATE_DIRECTORY),
            ("blobs", _BLOB_DIRECTORY),
            ("exports", _EXPORT_DIRECTORY),
        ):
            descriptor = _openat_directory(target_descriptor, entry)
            opened[name] = descriptor
            directory_identity = _directory_identity(descriptor)
            identities[name] = directory_identity
            _verify_child_binding(target_descriptor, entry, directory_identity)
        state_descriptor = opened["state"]
        if state_descriptor is None:
            raise RepositoryTargetError("repository state descriptor is unavailable")
        for name, entry in (
            ("events", _EVENT_DATABASE),
            ("runs", _RUN_DATABASE),
            ("retrieval", _RETRIEVAL_DATABASE),
        ):
            database_descriptor = _open_optional_regular(state_descriptor, entry)
            opened[name] = database_descriptor
            if database_descriptor is None:
                identities[name] = None
            else:
                metadata = os.fstat(database_descriptor)
                identity = (metadata.st_dev, metadata.st_ino)
                identities[name] = identity
                _verify_regular_binding(state_descriptor, entry, identity, "repository database")
        handle = RepositoryObservationHandle(target, opened, identities, expected_config)
        opened = {}
        return handle
    finally:
        _close_descriptors(tuple(value for value in opened.values() if value is not None))


def _open_optional_regular(parent_descriptor: int, name: str) -> int | None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RepositoryTargetError("repository database path is incompatible")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_descriptor_identity(
    descriptor: int, identity: tuple[int, int], *, directory: bool
) -> None:
    metadata = os.fstat(descriptor)
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != identity:
        raise RepositoryTargetError("repository observation descriptor changed")


def _stable_descriptor_path(descriptor: int) -> Path:
    """Return a supported path whose identity is exactly the retained descriptor."""
    expected = os.fstat(descriptor)
    for root in (Path("/dev/fd"), Path("/proc/self/fd")):
        candidate = root / str(descriptor)
        try:
            probe = os.open(candidate, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        except OSError:
            continue
        try:
            observed = os.fstat(probe)
            if (observed.st_dev, observed.st_ino) == (
                expected.st_dev,
                expected.st_ino,
            ):
                return candidate
        finally:
            os.close(probe)
    raise RepositoryTargetError("platform cannot bind SQLite reads to retained file descriptors")


def _close_descriptors(descriptors: tuple[int, ...]) -> None:
    for descriptor in reversed(descriptors):
        with suppress(OSError):
            os.close(descriptor)


def _absolute_lexical_path(path: str | Path) -> Path:
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded.absolute()


def _portable_relative_parts(path: str | Path) -> tuple[str, ...]:
    raw = os.fspath(path)
    if not isinstance(raw, str):
        raise RepositoryTargetError("repository target path must be text")
    if not raw or raw.startswith("/") or "\\" in raw or ":" in raw:
        raise RepositoryTargetError("repository target path is not a portable relative path")
    parts = tuple(raw.split("/"))
    if any(not _portable_component(component) for component in parts):
        raise RepositoryTargetError("repository target path is not a portable relative path")
    return parts


def _portable_component(component: str) -> bool:
    if not component or component in {".", ".."}:
        return False
    if "/" in component or "\\" in component or ":" in component:
        return False
    if component.endswith((".", " ")):
        return False
    if any(unicodedata.category(character).startswith("C") for character in component):
        return False
    device_stem = component.split(".", 1)[0].upper()
    return device_stem not in _WINDOWS_DEVICE_NAMES


def _validate_resolved_target(target: ResolvedRepositoryTarget) -> None:
    if not target.trusted_root.is_absolute():
        raise RepositoryTargetError("resolved repository target is invalid")
    if (
        not target.relative_parts
        or any(not _portable_component(part) for part in target.relative_parts)
        or target.existing_parts + target.missing_parts != target.relative_parts
        or len(target.existing_parts) != len(target.existing_prefix_identities)
        or not _valid_identity(target.verified_root_identity)
        or any(not _valid_identity(value) for value in target.existing_prefix_identities)
    ):
        raise RepositoryTargetError("resolved repository target is invalid")


def _valid_identity(value: tuple[int, int]) -> bool:
    return len(value) == 2 and all(isinstance(item, int) and item >= 0 for item in value)


def _stable_file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_directory(path: Path) -> int:
    try:
        descriptor = os.open(path, _DIRECTORY_OPEN_FLAGS)
    except OSError:
        raise RepositoryTargetError("trusted repository root is not a real directory") from None
    try:
        _directory_identity(descriptor)
    except RepositoryTargetError:
        os.close(descriptor)
        raise
    return descriptor


def _directory_identity(descriptor: int) -> tuple[int, int]:
    try:
        metadata = os.fstat(descriptor)
    except OSError:
        raise RepositoryTargetError("repository target could not be inspected") from None
    if not stat.S_ISDIR(metadata.st_mode):
        raise RepositoryTargetError("repository target contains an incompatible component")
    return metadata.st_dev, metadata.st_ino


__all__ = [
    "LocalRepositoryError",
    "LocalRepositoryPaths",
    "RepositoryTargetError",
    "RepositoryTargetInspection",
    "RepositoryTargetInspectionCode",
    "ResolvedRepositoryTarget",
    "initialize_local_repository",
    "initialize_repository_target",
    "inspect_repository_target",
    "resolve_explicit_repository_target",
    "resolve_repository_target",
    "validate_local_repository_layout",
]
