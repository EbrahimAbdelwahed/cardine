from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from types import TracebackType

import pytest

from cardine.integrations.study_agent import (
    CardineConfigError,
    CardineInternalError,
    CardineRuntimeConfig,
    CardineSourceContentUnavailableError,
    CardineUnavailableError,
    StudyRuntimeAdapter,
    compose_study_runtime,
)
from study_agent.retrieval import SourceContentError, SourceContentErrorCode


def test_runtime_requires_explicit_lifecycle_dependency() -> None:
    with pytest.raises(CardineConfigError):
        CardineRuntimeConfig()


def test_init_result_and_lease_are_closed_and_redacted() -> None:
    repository = object()

    @contextmanager
    def lease() -> Iterator[object]:
        yield repository

    runtime: StudyRuntimeAdapter[str, object] = compose_study_runtime(
        CardineRuntimeConfig(
            initializer=lambda: "initialized",
            opener=lease,
        )
    )
    assert runtime.initialize_repository() == "initialized"
    with runtime.open_repository() as opened:
        assert opened is repository
    assert "lambda" not in repr(CardineRuntimeConfig(initializer=lambda: object()))


def test_missing_operation_is_unavailable() -> None:
    runtime: StudyRuntimeAdapter[str, object] = compose_study_runtime(
        CardineRuntimeConfig(initializer=lambda: "initialized")
    )
    with pytest.raises(CardineUnavailableError):
        runtime.open_repository()


def test_foreign_failure_is_redacted_but_process_control_is_preserved() -> None:
    def fail() -> str:
        raise RuntimeError("secret backend detail")

    runtime: StudyRuntimeAdapter[str, object] = compose_study_runtime(
        CardineRuntimeConfig(initializer=fail)
    )
    with pytest.raises(CardineInternalError) as caught:
        runtime.initialize_repository()
    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

    def interrupt() -> str:
        raise KeyboardInterrupt

    interrupting: StudyRuntimeAdapter[str, object] = compose_study_runtime(
        CardineRuntimeConfig(initializer=interrupt)
    )
    with pytest.raises(KeyboardInterrupt):
        interrupting.initialize_repository()


def test_source_content_failure_keeps_one_closed_product_classification() -> None:
    def fail() -> str:
        raise SourceContentError(
            SourceContentErrorCode.NOT_FOUND, "secret blob path"
        )

    runtime: StudyRuntimeAdapter[str, object] = compose_study_runtime(
        CardineRuntimeConfig(initializer=fail)
    )
    with pytest.raises(CardineSourceContentUnavailableError) as caught:
        runtime.initialize_repository()
    assert "blob" not in str(caught.value).casefold()
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_source_integrity_failure_is_not_mislabeled_as_missing_content() -> None:
    def fail() -> str:
        raise SourceContentError(
            SourceContentErrorCode.INTEGRITY_ERROR, "secret corrupt blob path"
        )

    runtime: StudyRuntimeAdapter[str, object] = compose_study_runtime(
        CardineRuntimeConfig(initializer=fail)
    )
    with pytest.raises(CardineInternalError) as caught:
        runtime.initialize_repository()
    assert "secret" not in str(caught.value)
    assert "missing" not in str(caught.value).casefold()
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


class _FailingLease(AbstractContextManager[object]):
    def __init__(self, phase: str, *, interrupt: bool = False) -> None:
        self._phase = phase
        self._failure: BaseException = (
            KeyboardInterrupt() if interrupt else RuntimeError("secret lease detail")
        )

    def __enter__(self) -> object:
        if self._phase == "enter":
            raise self._failure
        return object()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._phase == "exit":
            raise self._failure


@pytest.mark.parametrize("phase", ["open", "enter", "exit"])
def test_repository_lease_failures_are_redacted(phase: str) -> None:
    def opener() -> AbstractContextManager[object]:
        if phase == "open":
            raise RuntimeError("secret opener detail")
        return _FailingLease(phase)

    runtime: StudyRuntimeAdapter[object, object] = compose_study_runtime(
        CardineRuntimeConfig(opener=opener)
    )
    with pytest.raises(CardineInternalError) as caught, runtime.open_repository():
        pass
    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("phase", ["open", "enter", "exit"])
def test_repository_lease_preserves_process_control(phase: str) -> None:
    def opener() -> AbstractContextManager[object]:
        if phase == "open":
            raise KeyboardInterrupt
        return _FailingLease(phase, interrupt=True)

    runtime: StudyRuntimeAdapter[object, object] = compose_study_runtime(
        CardineRuntimeConfig(opener=opener)
    )
    with pytest.raises(KeyboardInterrupt), runtime.open_repository():
        pass
