"""Private legacy repository adapter behind the Cardine lifecycle seam."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from types import TracebackType
from typing import NoReturn, Protocol, TypeVar

from .errors import (
    CardineUnavailableError,
    translate_exception,
)

InitT = TypeVar("InitT")
RepositoryT = TypeVar("RepositoryT")


class StudyRuntimeAdapter[InitT, RepositoryT](Protocol):
    """Only the repository lifecycle operations currently owned by A0."""

    def initialize_repository(self) -> InitT: ...

    def open_repository(self) -> AbstractContextManager[RepositoryT]: ...


class _RepositoryLease[RepositoryT](AbstractContextManager[RepositoryT]):
    def __init__(self, lease: AbstractContextManager[RepositoryT]) -> None:
        self._lease = lease

    def __enter__(self) -> RepositoryT:
        try:
            return self._lease.__enter__()
        except Exception as error:
            translated = translate_exception(error)
        _raise_translated(translated)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        try:
            return self._lease.__exit__(exc_type, exc_value, traceback)
        except Exception as error:
            translated = translate_exception(error)
        _raise_translated(translated)


def _raise_translated(error: Exception) -> NoReturn:
    error.__cause__ = None
    error.__context__ = None
    raise error from None


class _LegacyStudyRuntimeAdapter[InitT, RepositoryT](StudyRuntimeAdapter[InitT, RepositoryT]):
    """One temporary adapter over the existing local repository owner."""

    def __init__(
        self,
        initializer: Callable[[], InitT] | None,
        opener: Callable[[], AbstractContextManager[RepositoryT]] | None,
    ) -> None:
        self._initializer = initializer
        self._opener = opener

    def initialize_repository(self) -> InitT:
        if self._initializer is None:
            raise CardineUnavailableError()
        try:
            return self._initializer()
        except Exception as error:
            translated = translate_exception(error)
        _raise_translated(translated)

    def open_repository(self) -> AbstractContextManager[RepositoryT]:
        if self._opener is None:
            raise CardineUnavailableError()
        try:
            lease = self._opener()
        except Exception as error:
            translated = translate_exception(error)
        else:
            return _RepositoryLease(lease)
        _raise_translated(translated)


__all__ = ["InitT", "RepositoryT", "StudyRuntimeAdapter"]
