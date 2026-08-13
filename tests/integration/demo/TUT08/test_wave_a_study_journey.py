from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import MethodType
from typing import Protocol, cast

import pytest

from cardine.adapters.pageindex import PageIndexCoordinator, PageIndexWorker
from cardine.adapters.pageindex.worker import PageIndexWorkerError
from cardine.application.artifact_decisions import decide_artifacts
from cardine.cli.repository import LocalRepository
from cardine.knowledge import PageIndexStatus, SearchDisposition
from study_agent.domain import (
    AnswerStatus,
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    SessionId,
    SourceId,
)
from study_agent.ports import (
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
)
from study_agent.recall import (
    RecallCommandError,
    RecallRating,
    SchedulingRequest,
    SchedulingResult,
)
from tests.integration.demo.TUT08.test_flashcard_proposals import (
    _install_hybrid_flashcard_model,
)
from tests.integration.demo.TUT08.test_repository_backed_chat import _repository

COURSE = CourseId("cardine-course")
SESSION = SessionId("cardine-session")
FLASHCARD_SESSION = SessionId("cardine-flashcard-session")
SOURCE = SourceId("valves")
_EVIDENCE_ID = re.compile(r'"evidence_id":"([^"]+)"')


class _ModelWithGenerate(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


def _install_grounding_model(model: object) -> None:
    typed_model = cast(_ModelWithGenerate, model)
    original = typed_model.generate

    async def generate(
        self: _ModelWithGenerate, request: ModelRequest
    ) -> ModelResponse:
        if request.metadata.get("prompt_id") == "grounded_answer.v1":
            rendered = "\n".join(message.content for message in request.messages)
            match = _EVIDENCE_ID.search(rendered)
            if match is None:
                raise AssertionError("grounding fixture expected canonical evidence")
            return ModelResponse(
                "",
                None,
                ModelFinishReason.STOP,
                ModelInvocation("fixture", "1.0.0", "fixture", "wave-a-grounding"),
                structured_output={
                    "status": "answered",
                    "segments": (
                        {
                            "kind": "supported_claim",
                            "text": "The aortic valve has three cusps.",
                            "evidence_ids": (match.group(1),),
                        },
                    ),
                    "unsupported_information_note": None,
                },
            )
        return await original(request)

    object.__setattr__(model, "generate", MethodType(generate, typed_model))


class _DeterministicScheduler:
    def decide(self, request: SchedulingRequest) -> SchedulingResult:
        from dataclasses import replace

        from study_agent.recall import (
            effective_policy_fingerprint,
            result_fingerprint,
        )

        policy_fingerprint = effective_policy_fingerprint(
            request.policy, "wave-a-fixture", "1", "wave-a-fixture", "1"
        )
        due_at = (
            request.enrollment_at
            if not request.history
            else request.history[-1].occurred_at
        )
        partial = SchedulingResult(
            due_at,
            "wave-a-fixture",
            "1",
            policy_fingerprint,
            "wave-a-fixture",
            "1",
            request.history_fingerprint,
            "0" * 64,
        )
        return replace(partial, result_fingerprint=result_fingerprint(request, partial))


class _FailingPageIndexWorker(PageIndexWorker):
    def run(self, _markdown: str) -> list[object]:
        raise PageIndexWorkerError("fixture_pageindex_failure")


def _context(
    key: str,
    *,
    principal: PrincipalKind,
    capabilities: frozenset[str] = frozenset(),
    session_id: SessionId | None = SESSION,
) -> ExecutionContext:
    return ExecutionContext(
        principal,
        "wave-a-journey",
        COURSE,
        CorrelationId(f"wave-a-{key}"),
        capabilities,
        session_id,
        idempotency_key=key,
    )


def test_wave_a_aggregate_markdown_study_journey(tmp_path: Path) -> None:
    root, adapters, model = _repository(
        tmp_path,
        source_content=(
            b"# Lezione 1\nLa valvola aortica ha tre cuspidi.\n"
            b"# Lezione 2\nLa valvola mitrale ha due lembi.\n"
        ),
    )
    _install_grounding_model(model)
    flashcard_requests = _install_hybrid_flashcard_model(model)

    with LocalRepository.open(
        root,
        model_adapters=adapters,
        recall_scheduler=_DeterministicScheduler(),
    ) as repository:
        # Consent is an explicit, durable HUMAN policy receipt before any
        # provider-backed grounding or generation is attempted.
        consent = repository.provider_consent.get(COURSE)
        assert consent is not None and consent.granted

        repository.rebuild_retrieval()
        source = next(
            item
            for item in repository.for_course(COURSE).content.catalog()
            if item.source.source_id == SOURCE
        )
        queued = repository.pageindex_status(COURSE)
        assert queued and queued[0].status is PageIndexStatus.QUEUED
        ready = repository.reconcile_pageindex(COURSE, budget=1)
        assert ready[0].status is PageIndexStatus.READY

        search = repository.search_lessons(COURSE, "Lezione 1")
        assert search.disposition is SearchDisposition.UNIQUE
        pin_one = repository.select_lesson(COURSE, "Lezione 1", search.candidates[0].candidate_id)
        pin_two = repository.select_lesson(
            COURSE,
            "Lezione 2",
            repository.search_lessons(COURSE, "Lezione 2").candidates[0].candidate_id,
        )
        assert repository.validate_lesson_pin(pin_one).text[
            pin_one.start_offset : pin_one.end_offset
        ].startswith("# Lezione 1")
        assert "Lezione 2" not in repository.validate_lesson_pin(pin_one).text[
            pin_one.start_offset : pin_one.end_offset
        ]

        # Restart leaves the derived projection ready and the explicit pin
        # remains valid against the same canonical revision.
        with LocalRepository.open(
            root,
            model_adapters=adapters,
            recall_scheduler=_DeterministicScheduler(),
        ) as restarted:
            assert restarted.pageindex_status(COURSE)[0].status is PageIndexStatus.READY
            assert restarted.validate_lesson_pin(pin_one).revision_id == pin_one.revision_id

            receipt = restarted.course_index_receipt(COURSE, restarted.rebuild_retrieval())
            grounding = restarted.grounding_service(COURSE, receipt, lesson_pin=pin_one)
            answer_result = asyncio.run(
                grounding.ask(
                    "Quante cuspidi ha la valvola aortica?",
                    _context(
                        "grounding",
                        principal=PrincipalKind.HUMAN,
                        capabilities=frozenset({"study:ask"}),
                    ),
                )
            )
            assert answer_result.answer.answer.status is AnswerStatus.ANSWERED
            citation = answer_result.answer.answer.segments[0].citations[0]
            resolved = restarted.for_course(COURSE).content.resolve(citation)
            assert citation.source_id == SOURCE
            assert resolved.citation == citation
            assert "Lezione 1" in resolved.citation.locator
            assert "tre cuspidi" in resolved.text
            assert "Lezione 2" not in resolved.citation.locator

            restarted.session_service.start(
                _context(
                    "flashcard-session",
                    principal=PrincipalKind.HUMAN,
                    session_id=FLASHCARD_SESSION,
                )
            )

            first_proposals = asyncio.run(
                restarted.propose_flashcards_for_pin(
                    COURSE,
                    FLASHCARD_SESSION,
                    pin_one,
                    "Crea flashcard per Lezione 1",
                    _context(
                        "flashcards-one",
                        principal=PrincipalKind.HUMAN,
                        session_id=FLASHCARD_SESSION,
                    ),
                )
            )
            second_proposals = asyncio.run(
                restarted.propose_flashcards_for_pin(
                    COURSE,
                    FLASHCARD_SESSION,
                    pin_two,
                    "Crea flashcard per Lezione 2",
                    _context(
                        "flashcards-two",
                        principal=PrincipalKind.HUMAN,
                        session_id=FLASHCARD_SESSION,
                    ),
                )
            )
            assert first_proposals.run_id != second_proposals.run_id
            assert len(flashcard_requests) == 2

            pending = restarted.artifacts.get(COURSE).pending()
            assert len(pending) >= 2
            accepted_ids = tuple(item.id for item in pending[: len(pending) // 2])
            rejected_ids = tuple(item.id for item in pending[len(pending) // 2 :])
            decision_receipt = decide_artifacts(
                restarted.artifact_service,
                tuple(
                    {"revision_id": str(revision_id), "decision": "accepted"}
                    for revision_id in accepted_ids
                )
                + tuple(
                    {"revision_id": str(revision_id), "decision": "rejected"}
                    for revision_id in rejected_ids
                ),
                _context(
                    "mixed-decisions",
                    principal=PrincipalKind.HUMAN,
                    session_id=FLASHCARD_SESSION,
                ),
                restarted.events.projection(COURSE).sequence,
            )
            assert len(decision_receipt.results) == len(pending)
            snapshot = restarted.artifacts.get(COURSE)
            assert all(snapshot.revision(item).status.value == "accepted" for item in accepted_ids)
            assert all(snapshot.revision(item).status.value == "rejected" for item in rejected_ids)

            assert restarted.recall is not None and restarted.recall.commands is not None
            recall = restarted.recall.commands
            for ordinal, accepted_id in enumerate(accepted_ids):
                enrollment = recall.enroll(
                    accepted_id,
                    _context(
                        f"enroll-accepted-{ordinal}",
                        principal=PrincipalKind.SERVICE,
                        capabilities=frozenset({"study:recall"}),
                        session_id=FLASHCARD_SESSION,
                    ),
                    restarted.events.projection(COURSE).sequence,
                )
            assert tuple(item.revision_id for item in enrollment.enrollments) == accepted_ids
            due_before_review = restarted.recall_composition.due.due(COURSE)
            assert {row.revision_id for row in due_before_review} == set(accepted_ids)
            with pytest.raises(RecallCommandError):
                recall.enroll(
                    rejected_ids[0],
                    _context(
                        "enroll-rejected",
                        principal=PrincipalKind.SERVICE,
                        capabilities=frozenset({"study:recall"}),
                        session_id=FLASHCARD_SESSION,
                    ),
                restarted.events.projection(COURSE).sequence,
            )
            reviewed = recall.review(
                accepted_ids[0],
                RecallRating.GOOD,
                _context(
                    "review-accepted",
                    principal=PrincipalKind.HUMAN,
                    capabilities=frozenset({"study:recall"}),
                    session_id=FLASHCARD_SESSION,
                ),
                restarted.events.projection(COURSE).sequence,
            )
            assert len(reviewed.reviews) == 1

            disabled = restarted.disable_pageindex(COURSE, SOURCE, source.source.revision_id)
            assert disabled.status is PageIndexStatus.DISABLED
            lexical = restarted.search_lessons(COURSE, "Lezione 1")
            assert lexical.disposition is SearchDisposition.UNIQUE
            assert lexical.candidates[0].start_offset == pin_one.start_offset

            restarted.enable_pageindex(COURSE, SOURCE, source.source.revision_id)
            restarted.reconcile_pageindex(COURSE, budget=1)
            restarted.pageindex = PageIndexCoordinator(
                restarted.runs,
                worker=_FailingPageIndexWorker(),
                max_attempts=1,
            )
            failed = restarted.rebuild_pageindex(COURSE, SOURCE, source.source.revision_id)
            assert failed.status is PageIndexStatus.FAILED
            assert (
                restarted.search_lessons(COURSE, "Lezione 1").disposition
                is SearchDisposition.UNIQUE
            )

            retired = restarted.source_lifetime_service.retire(
                _context("retire-source", principal=PrincipalKind.HUMAN, session_id=None),
                SOURCE,
                "retire-source",
                expected_sequence=restarted.events.projection(COURSE).sequence,
            )
            assert retired.retired
            assert (
                restarted.search_lessons(COURSE, "Lezione 1").disposition
                is SearchDisposition.NOT_FOUND
            )
            assert restarted.for_course(COURSE).content.resolve(citation).text == resolved.text
            assert (
                restarted.artifacts.get(COURSE).revision(accepted_ids[0]).status.value
                == "accepted"
            )
            assert restarted.recall_composition.due.due(COURSE)

    with LocalRepository.open(
        root,
        model_adapters=adapters,
        recall_scheduler=_DeterministicScheduler(),
    ) as final_restart:
        assert (
            final_restart.search_lessons(COURSE, "Lezione 1").disposition
            is SearchDisposition.NOT_FOUND
        )
        assert final_restart.for_course(COURSE).content.resolve(citation).text == resolved.text
        assert (
            final_restart.artifacts.get(COURSE).revision(accepted_ids[0]).status.value
            == "accepted"
        )
        assert final_restart.recall_composition.due.due(COURSE)
