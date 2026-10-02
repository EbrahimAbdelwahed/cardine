from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from cardine.application.flashcard_proposals import (
    _lesson_unit,
    _LessonEvidenceResolver,
    _ScopedCourseSourceContent,
)
from cardine.application.study_semantics import (
    ConsentChoiceJudgementPort,
    FlashcardSemanticPreprocessor,
    document_revision,
)
from cardine.cli.repository import LocalRepository
from cardine.integrations.study_agent.course_policy import ProviderConsentRequiredError
from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.domain import CorrelationId, CourseId, ExecutionContext, PrincipalKind, SourceId
from study_agent.domain._validation import JsonObject
from study_agent.domain.features import FeatureMode
from study_agent.flashcards.lesson_worker_contracts import RevisionContentCommitment
from study_agent.flashcards.planning import LessonGenerationUnit, plan_flashcard_lesson
from study_agent.flashcards.semantic import FlashcardSemanticAnalyzer, SemanticPolicy
from study_agent.ports.judgement import ChoiceJudgement, ChoiceJudgementRequest, ChoiceProbability
from study_agent.repository_config import LocalRepositoryConfig, SemanticFeaturesConfig
from study_agent.retrieval import CourseSourceContent
from tests.course_fixtures import create_canonical_course

COURSE = CourseId("course-study-semantics")
TEXT = (
    b"# Lesson 1\n\nCORE facts.\n\nEXCLUDE noise.\n\nCONTEXT background.\n\n"
    b"# Lesson 2\n\nOTHER private.\n"
)
POLICY = SemanticPolicy("consumer-test", "1", 0.8, 0.95, 0.99, 0.2)


class Judge:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[ChoiceJudgementRequest] = []

    async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("private failure")
        state = cast(JsonObject, request.state)
        if request.metadata["use_case"] == "topic_anchor":
            selected = request.options[-1].key
        else:
            text = str(state["text"])
            selected = (
                "excluded"
                if "EXCLUDE" in text or text.startswith("#")
                else ("context_only" if "CONTEXT" in text else "core")
            )
        return ChoiceJudgement(
            selected,
            tuple(
                ChoiceProbability(option.key, 1.0 if option.key == selected else 0.0)
                for option in request.options
            ),
            1.0,
            "fake",
            "1",
            "test",
            1.0,
            {},
        )


def repository(tmp_path: Path) -> LocalRepository:
    root = tmp_path / "repository"
    initialize_local_repository(root, LocalRepositoryConfig())
    repo = LocalRepository.open(root)
    create_canonical_course(repo.events, COURSE)
    repo.for_course(COURSE).ingestion.ingest(
        filename="lessons.md",
        content=TEXT,
        source_id=SourceId("source-semantic"),
        title="Lessons",
        trust_level=100,
        source_role="reference",
        context=context(),
    )
    return repo


def context() -> ExecutionContext:
    return ExecutionContext(PrincipalKind.SERVICE, "test", COURSE, CorrelationId("semantic-test"))


def processor(
    repo: LocalRepository,
    judge: Judge,
    *,
    mode: FeatureMode = FeatureMode.ON,
    policy: SemanticPolicy = POLICY,
) -> FlashcardSemanticPreprocessor:
    return FlashcardSemanticPreprocessor(
        content=repo.for_course(COURSE).content,
        blobs=repo.blobs,
        indexes=repo.pageindex,
        runs=repo.runs,
        features=SemanticFeaturesConfig(
            document_index_mode=FeatureMode.ON, flashcard_semantic_mode=mode
        ),
        analyzer=FlashcardSemanticAnalyzer(judge, policy, judgement_identity="fake@1/test"),
    )


def prepare(
    preprocessor: FlashcardSemanticPreprocessor, content: CourseSourceContent
) -> LessonGenerationUnit:
    return asyncio.run(preprocessor.prepare(_lesson_unit(content), content.catalog()))


def test_real_canonical_worker_gate_and_restart_cache(tmp_path: Path) -> None:
    with repository(tmp_path) as repo:
        content = repo.for_course(COURSE).content
        before = tuple(repo.events.read(COURSE))
        judge = Judge()
        unit = prepare(processor(repo, judge), content)
        assert tuple(repo.events.read(COURSE)) == before
        plan = plan_flashcard_lesson(unit)
        assert plan.bundles
        commitments = tuple(
            RevisionContentCommitment(record.source.revision_id, record.source.checksum_sha256)
            for record in content.catalog()
        )
        resolver = _LessonEvidenceResolver(content, lambda: frozenset())
        for bundle in plan.bundles:
            resolved = resolver.resolve(plan, bundle, commitments, context())
            resolved.validate(plan, bundle, commitments)
            text = " ".join(item.evidence.text for item in resolved.envelope.items)
            assert "CORE facts" in text
            assert "EXCLUDE" not in text and "CONTEXT" not in text
            assert all(
                item.evidence.chunk in content.catalog()[0].chunks
                for item in resolved.envelope.items
            )
        calls = len(judge.requests)
        assert prepare(processor(repo, judge), content) == unit
        assert len(judge.requests) == calls
    with LocalRepository.open(tmp_path / "repository") as reopened:
        new_judge = Judge()
        assert prepare(processor(reopened, new_judge), reopened.for_course(COURSE).content) == unit
        assert new_judge.requests == []


def test_selected_lesson_never_sends_other_source_content(tmp_path: Path) -> None:
    with repository(tmp_path) as repo:
        source = repo.for_course(COURSE).content
        result = repo.search_lessons(COURSE, "Lesson 1")
        pin = repo.select_lesson(COURSE, "Lesson 1", result.candidates[0].candidate_id)
        selected = cast(CourseSourceContent, _ScopedCourseSourceContent(source, pin))
        judge = Judge()
        unit = prepare(processor(repo, judge), selected)
        assert unit.paragraphs
        assert all(paragraph.span.end_offset <= pin.end_offset for paragraph in unit.paragraphs)
        assert not any("OTHER private" in str(request.state) for request in judge.requests)
        plan = plan_flashcard_lesson(unit)
        record = source.catalog()[0]
        commitments = (
            RevisionContentCommitment(record.source.revision_id, record.source.checksum_sha256),
        )
        for bundle in plan.bundles:
            resolved = _LessonEvidenceResolver(selected, lambda: frozenset()).resolve(
                plan, bundle, commitments, context()
            )
            resolved.validate(plan, bundle, commitments)


def test_policy_changes_recompute_analysis_without_changing_canonical_ids(tmp_path: Path) -> None:
    with repository(tmp_path) as repo:
        source = repo.for_course(COURSE).content
        ids = tuple(chunk.chunk_id for chunk in source.catalog()[0].chunks)
        judge = Judge()
        prepare(processor(repo, judge), source)
        count = len(judge.requests)
        prepare(processor(repo, judge, policy=replace(POLICY, version="2")), source)
        assert len(judge.requests) > count
        assert tuple(chunk.chunk_id for chunk in source.catalog()[0].chunks) == ids


def test_provider_failure_preserves_all_chunks_and_does_not_poison_cache(tmp_path: Path) -> None:
    with repository(tmp_path) as repo:
        source = repo.for_course(COURSE).content
        judge = Judge(fail=True)
        unit = prepare(processor(repo, judge), source)
        assert len(unit.paragraphs) == len(source.catalog()[0].chunks)
        judge.fail = False
        recovered = prepare(processor(repo, judge), source)
        assert len(recovered.paragraphs) < len(unit.paragraphs)


def test_shadow_preserves_exact_original_and_off_calls_no_provider(tmp_path: Path) -> None:
    with repository(tmp_path) as repo:
        source = repo.for_course(COURSE).content
        original = _lesson_unit(source)
        judge = Judge()
        shadow = asyncio.run(
            processor(repo, judge, mode=FeatureMode.SHADOW).prepare(original, source.catalog())
        )
        assert shadow is original and judge.requests
        before = len(judge.requests)
        off = FlashcardSemanticPreprocessor(
            content=source,
            blobs=repo.blobs,
            indexes=repo.pageindex,
            runs=repo.runs,
            features=SemanticFeaturesConfig(),
            analyzer=None,
        )
        assert asyncio.run(off.prepare(original, source.catalog())) is original
        assert len(judge.requests) == before


def test_primary_index_failure_is_explicit_and_never_calls_judgement(tmp_path: Path) -> None:
    with repository(tmp_path) as repo:
        source = repo.for_course(COURSE).content
        repo.pageindex.disable(document_revision(source.catalog()[0], repo.blobs))
        judge = Judge()
        with pytest.raises(ValueError, match="document_index_unavailable:disabled"):
            prepare(processor(repo, judge), source)
        assert judge.requests == []


def test_choice_provider_consent_is_checked_before_transport(tmp_path: Path) -> None:
    with repository(tmp_path) as repo:
        from study_agent.ports.judgement import ChoiceOption

        judge = Judge()
        guarded = ConsentChoiceJudgementPort(judge, COURSE, repo.provider_consent)
        request = ChoiceJudgementRequest(
            "Choose", {}, (ChoiceOption("a", "A"), ChoiceOption("b", "B"))
        )
        with pytest.raises(ProviderConsentRequiredError):
            asyncio.run(guarded.judge(request))
        assert judge.requests == []


def test_production_primary_routing_only_generates_the_selected_payload(tmp_path: Path) -> None:
    from cardine.cli.repository import ModelAdapterRegistry
    from cardine.hosts.contracts import AssistantMessageDecision
    from study_agent.domain import SessionId
    from study_agent.ports import ModelPort
    from study_agent.repository_config import JudgementAdapterConfig, ModelAdapterConfig
    from tests.unit.hosts.test_routing import Judge as RouterJudge
    from tests.unit.hosts.test_routing import Model, Token
    from tests.unit.hosts.test_routing import context as routing_context

    root = tmp_path / "primary-router"
    config = LocalRepositoryConfig(
        ModelAdapterConfig("fixture"),
        judgement=JudgementAdapterConfig(),
        features=SemanticFeaturesConfig(tutor_routing_mode=FeatureMode.ON),
    )
    initialize_local_repository(root, config)
    model = Model({"message": "A bounded tutor reply"})
    judge = RouterJudge("assistant_message")
    registry = ModelAdapterRegistry({"fixture": lambda config, credential: cast(ModelPort, model)})
    with LocalRepository.open(root, model_adapters=registry, judgement=judge) as repo:
        create_canonical_course(repo.events, COURSE)
        repo.provider_consent_service.grant(
            replace(context(), principal_kind=PrincipalKind.HUMAN), "grant"
        )
        session_id = SessionId("primary-router-session")
        repo.session_service.start(
            replace(context(), principal_kind=PrincipalKind.HUMAN, session_id=session_id)
        )
        app = repo.tutor_conversation(COURSE, session_id=session_id)
        decision = asyncio.run(app._runner._decision_port.decide(routing_context(), Token()))
        assert isinstance(decision, AssistantMessageDecision)
        assert decision.message == "A bounded tutor reply"
        assert len(judge.requests) == 1 and len(model.requests) == 1
        constraint = model.requests[0].structured_output
        assert constraint is not None
        assert constraint.schema["properties"] == {"message": {"type": "string", "minLength": 1}}


def test_primary_navigation_uses_shared_index_for_text_and_reports_failure(tmp_path: Path) -> None:
    root = tmp_path / "primary-index"
    config = LocalRepositoryConfig(
        features=SemanticFeaturesConfig(document_index_mode=FeatureMode.ON)
    )
    initialize_local_repository(root, config)
    with LocalRepository.open(root) as repo:
        create_canonical_course(repo.events, COURSE)
        result = repo.for_course(COURSE).ingestion.ingest(
            filename="notes.txt",
            content=b"First paragraph.\n\nSecond paragraph.",
            source_id=SourceId("text-source"),
            title="Text lesson",
            trust_level=100,
            source_role="reference",
            context=context(),
        )
        with pytest.raises(ValueError, match="document index is unavailable"):
            repo.search_lessons(COURSE, "Text lesson")
        repo.reconcile_pageindex(COURSE, budget=1)
        found = repo.search_lessons(COURSE, "Text lesson")
        assert len(found.candidates) == 1
        pin = repo.select_lesson(COURSE, "Text lesson", found.candidates[0].candidate_id)
        assert repo.validate_lesson_pin(pin).title == "Text lesson"
        repo.disable_pageindex(COURSE, result.source.source_id, result.source.revision_id)
        with pytest.raises(ValueError, match="document index is unavailable"):
            repo.search_lessons(COURSE, "Text lesson")


def test_shadow_index_failure_preserves_original_and_skips_judgement(tmp_path: Path) -> None:
    with repository(tmp_path) as repo:
        source = repo.for_course(COURSE).content
        repo.pageindex.disable(document_revision(source.catalog()[0], repo.blobs))
        judge = Judge()
        original = _lesson_unit(source)
        preprocessor = FlashcardSemanticPreprocessor(
            content=source,
            blobs=repo.blobs,
            indexes=repo.pageindex,
            runs=repo.runs,
            features=SemanticFeaturesConfig(
                document_index_mode=FeatureMode.SHADOW,
                flashcard_semantic_mode=FeatureMode.SHADOW,
            ),
            analyzer=FlashcardSemanticAnalyzer(judge, POLICY, judgement_identity="fake@1/test"),
        )
        assert asyncio.run(preprocessor.prepare(original, source.catalog())) is original
        assert judge.requests == []
