from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cardine.cli import (
    EMPTY_CONFIG,
    LocalRepository,
    LocalRepositoryConfig,
    LocalRepositoryError,
    ModelAdapterConfig,
    ModelAdapterConfigurationError,
    ModelAdapterRegistry,
    initialize_local_repository,
)
from study_agent.adapters.model import OpenAICompatibleModel, OpenAIGpt56LunaModel
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    SessionId,
    SourceId,
)
from study_agent.domain._validation import JsonObject
from study_agent.domain.grounding import AnswerStatus
from study_agent.ports import (
    CancellationToken,
    IndexReceipt,
    ModelCapabilities,
    ModelPort,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
)
from tests.course_fixtures import create_canonical_course


class DummyModel:
    capabilities = ModelCapabilities(structured_output=True)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError(request)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise AssertionError(request)
        yield  # pragma: no cover

    async def cancel(self, token: CancellationToken) -> None:
        raise AssertionError(token)


def model_config() -> LocalRepositoryConfig:
    return LocalRepositoryConfig(
        ModelAdapterConfig(
            "openai-compatible-http",
            {
                "endpoint_url": "https://models.example.test/v1/chat/completions",
                "model_id": "model-a",
                "timeout_seconds": 10,
            },
            "MODEL_KEY",
        )
    )


def test_initialization_is_offline_idempotent_and_exact(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    first = initialize_local_repository(root, EMPTY_CONFIG)
    lock_inode = (root / ".study-agent.lock").stat().st_ino
    second = initialize_local_repository(root, EMPTY_CONFIG)

    assert first == second
    assert first.config.read_bytes() == EMPTY_CONFIG.to_bytes()
    assert first.state.is_dir()
    assert first.blobs.is_dir()
    assert first.exports.is_dir()
    assert not first.events.exists()
    assert not first.runs.exists()
    assert not first.retrieval.exists()
    assert (root / ".study-agent.lock").stat().st_ino == lock_inode


def test_initialization_rejects_nonempty_and_incompatible_collisions(
    tmp_path: Path,
) -> None:
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "owned-by-user.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(LocalRepositoryError, match="non-empty"):
        initialize_local_repository(nonempty, EMPTY_CONFIG)
    assert (nonempty / "owned-by-user.txt").read_text(encoding="utf-8") == "preserve"

    initialized = tmp_path / "initialized"
    initialize_local_repository(initialized, EMPTY_CONFIG)
    with pytest.raises(LocalRepositoryError, match="incompatible"):
        initialize_local_repository(initialized, model_config())


def test_initialization_rejects_layout_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    paths = initialize_local_repository(root, EMPTY_CONFIG)
    paths.blobs.rmdir()
    paths.blobs.symlink_to(tmp_path)

    with pytest.raises(LocalRepositoryError, match="incompatible"):
        initialize_local_repository(root, EMPTY_CONFIG)


def test_initialization_rolls_back_owned_paths_after_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"

    def fail_link(
        source: object,
        destination: object,
        **kwargs: object,
    ) -> None:
        raise OSError(
            f"simulated no-replace publication failure: "
            f"{source!r} {destination!r} {kwargs!r}"
        )

    monkeypatch.setattr(
        "study_agent.adapters.filesystem.repository_target.os.link", fail_link
    )
    with pytest.raises(LocalRepositoryError, match="could not be initialized"):
        initialize_local_repository(root, EMPTY_CONFIG)

    assert {entry.name for entry in root.iterdir()} == {".study-agent.lock"}


def test_interrupted_marker_layout_is_recovered_without_guessing_user_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".study-agent.lock").write_text("", encoding="utf-8")
    (root / "state").mkdir()
    (root / "blobs").mkdir()

    paths = initialize_local_repository(root, EMPTY_CONFIG)

    assert paths.config.read_bytes() == EMPTY_CONFIG.to_bytes()
    assert paths.exports.is_dir()
    assert (root / ".study-agent.lock").is_file()


def test_concurrent_initializers_converge_on_one_complete_layout(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(
            pool.map(
                lambda _: initialize_local_repository(root, EMPTY_CONFIG),
                range(16),
            )
        )

    assert all(result == results[0] for result in results)
    assert results[0].config.read_bytes() == EMPTY_CONFIG.to_bytes()
    assert (root / ".study-agent.lock").is_file()


def test_config_publication_follows_durable_lock_and_layout_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    events: list[str] = []
    real_fsync = os.fsync
    real_link = os.link

    def observed_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        events.append("fsync-file" if stat.S_ISREG(mode) else "fsync-directory")
        real_fsync(descriptor)

    def observed_link(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        assert source == ".study-agent.json.tmp"
        assert destination == "study-agent.json"
        assert src_dir_fd is not None
        assert dst_dir_fd == src_dir_fd
        assert follow_symlinks is False
        events.append("link-config")
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(
        "study_agent.adapters.filesystem.repository_target.os.fsync", observed_fsync
    )
    monkeypatch.setattr(
        "study_agent.adapters.filesystem.repository_target.os.link", observed_link
    )

    initialize_local_repository(root, EMPTY_CONFIG)

    publication = events.index("link-config")
    before = events[:publication]
    lock_sync = before.index("fsync-file")
    assert before[lock_sync + 1] == "fsync-directory"
    assert before[-1] == "fsync-file"
    assert before.count("fsync-directory") >= 7
    assert events[publication + 1] == "fsync-directory"


def test_open_composes_durable_adapters_and_existing_services(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)

    with LocalRepository.open(root) as repository:
        assert repository.course_catalog.list_courses() == ()
        scoped = repository.for_course(CourseId("course-local"))
        assert scoped.content.documents() == ()
        assert repository.paths.events.is_file()
        assert repository.paths.runs.is_file()
        assert repository.paths.retrieval.is_file()

    with LocalRepository.open(root) as reopened:
        assert reopened.course_catalog.list_courses() == ()


def test_generic_registry_selects_by_adapter_id_without_provider_branch() -> None:
    captured: list[tuple[ModelAdapterConfig, str | None]] = []
    dummy = DummyModel()

    def build(config: ModelAdapterConfig, credential: str | None) -> ModelPort:
        captured.append((config, credential))
        return dummy

    registry = ModelAdapterRegistry({"fixture-adapter": build})
    config = ModelAdapterConfig("fixture-adapter", {}, "FIXTURE_KEY")

    assert registry.adapter_ids == ("fixture-adapter",)
    assert str(registry.artifact("fixture-adapter").version) == "1.0.0"
    assert registry.create(config, {"FIXTURE_KEY": "credential-value"}) is dummy
    assert captured == [(config, "credential-value")]
    assert "credential-value" not in repr(registry)


def test_registry_does_not_expose_unrelated_environment_or_builder_errors() -> None:
    observed: list[str | None] = []

    def fail(config: ModelAdapterConfig, credential: str | None) -> ModelPort:
        observed.append(credential)
        raise RuntimeError(f"builder leaked {credential} for {config.adapter_id}")

    registry = ModelAdapterRegistry({"fixture-adapter": fail})
    config = ModelAdapterConfig("fixture-adapter", {}, "FIXTURE_KEY")
    with pytest.raises(ModelAdapterConfigurationError) as raised:
        registry.create(
            config,
            {"FIXTURE_KEY": "credential-value", "UNRELATED_SECRET": "do-not-expose"},
        )

    assert observed == ["credential-value"]
    assert str(raised.value) == "configured model adapter could not be constructed"
    assert "credential-value" not in str(raised.value)
    assert "do-not-expose" not in str(raised.value)


def test_default_adapter_resolves_key_only_at_construction() -> None:
    registry = __import__(
        "cardine.cli", fromlist=["default_model_adapters"]
    ).default_model_adapters(allow_configurable_endpoints=True)
    config = model_config().model
    assert config is not None

    with pytest.raises(ModelAdapterConfigurationError, match="unavailable"):
        registry.create(config, {})
    adapter = registry.create(config, {"MODEL_KEY": "credential-value"})
    assert isinstance(adapter, OpenAICompatibleModel)
    assert "credential-value" not in repr(adapter)
    assert "credential-value" not in repr(registry)


def test_default_adapter_selects_provider_json_object_mode() -> None:
    registry = __import__(
        "cardine.cli", fromlist=["default_model_adapters"]
    ).default_model_adapters(allow_configurable_endpoints=True)
    config = ModelAdapterConfig(
        "openai-compatible-http",
        {
            "endpoint_url": "https://models.example.test/v1/chat/completions",
            "model_id": "model-with-json-text-only",
            "timeout_seconds": 10,
            "structured_output_format": "json_object",
        },
        "MODEL_KEY",
    )

    adapter = registry.create(config, {"MODEL_KEY": "credential-value"})

    assert adapter.capabilities.structured_output is True


def test_default_registry_selects_fixed_luna_adapter_from_credential_reference() -> None:
    registry = __import__(
        "cardine.cli", fromlist=["default_model_adapters"]
    ).default_model_adapters()
    config = ModelAdapterConfig(
        "openai-gpt-5.6-luna",
        {"timeout_seconds": 45},
        "OPENAI_API_KEY",
    )

    adapter = registry.create(config, {"OPENAI_API_KEY": "credential-value"})

    assert isinstance(adapter, OpenAIGpt56LunaModel)
    assert "openai-gpt-5.6-luna" in registry.adapter_ids
    assert "credential-value" not in repr(adapter)
    assert str(registry.artifact("openai-gpt-5.6-luna").version) == "1.0.0"


def test_default_registry_exposes_only_the_fixed_luna_network_destination() -> None:
    registry = __import__(
        "cardine.cli", fromlist=["default_model_adapters"]
    ).default_model_adapters()

    assert registry.adapter_ids == ("openai-gpt-5.6-luna",)


def test_luna_registry_rejects_an_alternate_credential_environment() -> None:
    registry = __import__(
        "cardine.cli", fromlist=["default_model_adapters"]
    ).default_model_adapters()
    config = ModelAdapterConfig(
        "openai-gpt-5.6-luna",
        {"timeout_seconds": 45},
        "AWS_SECRET_ACCESS_KEY",
    )

    with pytest.raises(ModelAdapterConfigurationError):
        registry.create(config, {"AWS_SECRET_ACCESS_KEY": "credential-value"})


@pytest.mark.parametrize(
    "settings",
    (
        {},
        {"timeout_seconds": 45, "model_id": "gpt-5.6-sol"},
        {"timeout_seconds": 45, "endpoint_url": "https://example.invalid"},
    ),
)
def test_luna_registry_rejects_missing_or_overridable_fixed_settings(
    settings: JsonObject,
) -> None:
    registry = __import__(
        "cardine.cli", fromlist=["default_model_adapters"]
    ).default_model_adapters()
    config = ModelAdapterConfig(
        "openai-gpt-5.6-luna",
        settings,
        "OPENAI_API_KEY",
    )

    with pytest.raises(ModelAdapterConfigurationError):
        registry.create(config, {"OPENAI_API_KEY": "credential-value"})


def test_open_does_not_require_or_resolve_model_credentials(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, model_config())

    with LocalRepository.open(root, environment={}) as repository:
        assert repository.config == model_config()


def test_single_retrieval_database_is_composed_over_all_courses(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    first_id = CourseId("course-a")
    second_id = CourseId("course-b")

    with LocalRepository.open(root) as repository:
        create_canonical_course(repository.events, first_id)
        create_canonical_course(repository.events, second_id)
        for course_id, text in (
            (first_id, b"The mitral valve has two leaflets."),
            (second_id, b"The aortic valve has three cusps."),
        ):
            repository.for_course(course_id).ingestion.ingest(
                filename="cardiology.txt",
                content=text,
                source_id=SourceId(f"source-{course_id}"),
                title="Cardiology",
                trust_level=100,
                source_role="reference",
                context=ExecutionContext(
                    PrincipalKind.SERVICE,
                    "composition-test",
                    course_id,
                    CorrelationId(f"ingest-{course_id}"),
                ),
            )

        repository_receipt = repository.rebuild_retrieval()
        first_receipt = repository.course_index_receipt(first_id, repository_receipt)
        second_receipt = repository.course_index_receipt(second_id, repository_receipt)

        assert repository_receipt.indexed_chunks == 2
        assert first_receipt.indexed_chunks == 1
        assert second_receipt.indexed_chunks == 1
        assert first_receipt.index_version == second_receipt.index_version
        assert first_receipt.catalog_fingerprint != second_receipt.catalog_fingerprint

        forged = IndexReceipt(
            repository_receipt.indexed_chunks,
            repository_receipt.index_version,
            "0" * 64,
        )
        with pytest.raises(LocalRepositoryError, match="stale or incompatible"):
            repository.course_index_receipt(first_id, forged)
        bool_count = IndexReceipt(
            True,
            repository_receipt.index_version,
            repository_receipt.catalog_fingerprint,
        )
        with pytest.raises(LocalRepositoryError, match="incompatible"):
            repository.course_index_receipt(first_id, bool_count)


def test_stale_repository_receipt_fails_after_canonical_catalog_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    course_id = CourseId("course-stale-index")
    with LocalRepository.open(root) as repository:
        create_canonical_course(repository.events, course_id)
        stale = repository.rebuild_retrieval()
        repository.for_course(course_id).ingestion.ingest(
            filename="new-source.txt",
            content=b"New canonical material invalidates the previous index receipt.",
            source_id=SourceId("source-stale-index"),
            title="New source",
            trust_level=100,
            source_role="reference",
            context=ExecutionContext(
                PrincipalKind.SERVICE,
                "composition-test",
                course_id,
                CorrelationId("ingest-stale-index"),
            ),
        )

        with pytest.raises(LocalRepositoryError, match="canonical repository catalog"):
            repository.course_index_receipt(course_id, stale)


def test_grounding_composition_uses_injected_adapter_and_durable_run_store(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    config = LocalRepositoryConfig(ModelAdapterConfig("fixture-adapter"))
    initialize_local_repository(root, config)
    model = DummyModel()

    def build(config: ModelAdapterConfig, credential: str | None) -> ModelPort:
        assert config.adapter_id == "fixture-adapter"
        assert credential is None
        return model

    registry = ModelAdapterRegistry({"fixture-adapter": build})
    course_id = CourseId("course-grounding-composition")
    session_id = SessionId("session-grounding-composition")
    context = ExecutionContext(
        PrincipalKind.SERVICE,
        "composition-test",
        course_id,
        CorrelationId("ask-composition"),
        frozenset({"study:ask"}),
        session_id,
        idempotency_key="ask-1",
    )

    with LocalRepository.open(
        root, model_adapters=registry, environment={}
    ) as repository:
        create_canonical_course(repository.events, course_id)
        repository.session_service.start(context)
        repository_receipt = repository.rebuild_retrieval()
        receipt = repository.course_index_receipt(course_id, repository_receipt)
        result = asyncio.run(
            repository.grounding_service(course_id, receipt).ask(
                "What do the sources establish?", context
            )
        )
        run_id = result.answer.run_id

        assert result.answer.answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE
        assert repository.runs.load(run_id)

    with LocalRepository.open(root) as reopened:
        assert reopened.runs.load(run_id)


def test_pageindex_restart_search_and_lexical_fallback_share_canonical_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    course_id = CourseId("course-pageindex")
    markdown = b"# Lezione 1\nIl nervo vago e il decimo nervo cranico.\n# Lezione 2\nAltro.\n"

    with LocalRepository.open(root) as repository:
        create_canonical_course(repository.events, course_id)
        admitted = repository.for_course(course_id).ingestion.ingest(
            filename="lezioni.md",
            content=markdown,
            source_id=SourceId("source-pageindex"),
            title="Lezioni",
            trust_level=100,
            source_role="reference",
            context=ExecutionContext(
                PrincipalKind.SERVICE,
                "pageindex-test",
                course_id,
                CorrelationId("ingest-pageindex"),
            ),
        )
        repository.rebuild_retrieval()
        assert repository.pageindex_status(course_id)[0].status.value == "queued"
        assert repository.reconcile_pageindex(course_id, budget=1)[0].status.value == "ready"
        structural = repository.search_lessons(course_id, "Lezione 1")
        assert structural.disposition.value == "unique"
        pin = repository.select_lesson(
            course_id, "Lezione 1", structural.candidates[0].candidate_id
        )
        assert repository.validate_lesson_pin(pin).source_id == "source-pageindex"
        assert pin.end_offset < len(markdown.decode())
        projection = repository.disable_pageindex(
            course_id,
            admitted.source.source_id,
            admitted.source.revision_id,
        )
        assert projection.status.value == "disabled"
        lexical = repository.search_lessons(course_id, "Lezione 1")
        assert lexical.disposition.value == "unique"
        assert lexical.candidates[0].start_offset == pin.start_offset

    with LocalRepository.open(root) as restarted:
        assert restarted.pageindex_status(course_id)[0].status.value == "disabled"
        assert restarted.search_lessons(course_id, "Lezione 1").disposition.value == "unique"
