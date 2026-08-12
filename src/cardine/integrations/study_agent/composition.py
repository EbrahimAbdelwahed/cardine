"""Explicit composition for the temporary legacy repository backend."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from .adapters import InitT, RepositoryT, StudyRuntimeAdapter, _LegacyStudyRuntimeAdapter
from .errors import CardineConfigError


@dataclass(frozen=True, slots=True, repr=False)
class CardineRuntimeConfig[InitT, RepositoryT]:
    """Explicit lifecycle callables; no discovery, selectors, or secrets."""

    initializer: Callable[[], InitT] | None = None
    opener: Callable[[], AbstractContextManager[RepositoryT]] | None = None

    def __post_init__(self) -> None:
        if self.initializer is None and self.opener is None:
            raise CardineConfigError()
        if self.initializer is not None and not callable(self.initializer):
            raise CardineConfigError()
        if self.opener is not None and not callable(self.opener):
            raise CardineConfigError()

    def __repr__(self) -> str:
        return "CardineRuntimeConfig(<redacted>)"


def compose_study_runtime[InitT, RepositoryT](
    config: CardineRuntimeConfig[InitT, RepositoryT],
) -> StudyRuntimeAdapter[InitT, RepositoryT]:
    """Construct the sole private legacy adapter for one host runtime."""

    if type(config) is not CardineRuntimeConfig:
        raise CardineConfigError()
    return _LegacyStudyRuntimeAdapter(config.initializer, config.opener)


__all__ = ["CardineRuntimeConfig", "StudyRuntimeAdapter", "compose_study_runtime"]
