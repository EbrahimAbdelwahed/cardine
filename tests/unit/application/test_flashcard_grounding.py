from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from cardine.application.flashcard_grounding import (
    FlashcardGroundingPolicy,
    FlashcardGroundingValidator,
    GroundingContentPort,
)
from study_agent.artifacts.candidates import (
    FlashcardAnswerBlock,
    FlashcardCandidate,
    FlashcardCandidateBatch,
    FlashcardPedagogicalRole,
)
from study_agent.domain import ResolvedCitation, RetrievalForm
from study_agent.domain._validation import JsonObject
from study_agent.domain.features import FeatureMode
from study_agent.playbooks.contracts import ValidationOutcome, ValidatorDisposition
from study_agent.ports.judgement import (
    ChoiceJudgement,
    ChoiceJudgementRequest,
    ChoiceProbability,
)
from study_agent.skills import SemanticVersion
from tests.unit.flashcards.test_lesson_worker_contracts import _request, _wrapper


@pytest.mark.parametrize(
    "scenario",
    (
        "positive",
        "partial",
        "key-point",
        "label",
        "foreign",
        "tampered",
        "retired",
        "excluded",
        "oversize",
        "denied",
        "schema-failure",
        "coverage",
        "margin",
        "shadow",
        "off",
        "cancellation",
        "deadline",
        "retired-during-judgement",
    ),
)
def test_complete_cited_evidence_gate(scenario: str) -> None:
    prepared = _wrapper(_request())
    evidence = prepared.prepared_scope.evidence.items[0].evidence
    handle = prepared.prepared_scope.evidence.items[0].handle
    blocks = (FlashcardAnswerBlock("label", "complete answer", ("complete key point",)),)
    candidate = FlashcardCandidate(
        "candidate",
        None,
        RetrievalForm.DIRECT_RECALL,
        "question",
        blocks,
        FlashcardPedagogicalRole.SECTION,
        None,
        None,
        "rationale",
        ("foreign" if scenario == "foreign" else handle,),
        (),
    )
    output = FlashcardCandidateBatch((candidate,), ()).to_json()
    calls: list[ChoiceJudgementRequest] = []

    class Core:
        id = "hybrid_flashcards_integrity"
        version = SemanticVersion.parse("1.0.0")

        async def validate(self, inputs: JsonObject) -> ValidationOutcome:
            if scenario == "schema-failure":
                return ValidationOutcome(False, ValidatorDisposition.TERMINATE, {}, "schema")
            return ValidationOutcome(True, ValidatorDisposition.CONTINUE, output)

    class Judge:
        async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
            calls.append(request)
            state = cast(Mapping[str, object], request.state)
            assert state["cited_canonical_excerpts"] == (evidence.text,)
            assert state["question"] == "question"
            assert state["answer_blocks"] == tuple(block.to_json() for block in blocks)
            if scenario == "retired-during-judgement":
                content.documents.return_value = ()
            if scenario == "cancellation":
                raise asyncio.CancelledError
            if scenario == "deadline":
                await asyncio.Event().wait()
            key = "insufficient" if scenario in ("partial", "key-point", "label") else "supported"
            other = 0.01 if scenario != "margin" else 0.25
            probs = tuple(
                ChoiceProbability(option.key, 1 - 2 * other if option.key == key else other)
                for option in request.options
            )
            if scenario == "coverage":
                probs = (ChoiceProbability("supported", 0.99), ChoiceProbability("foreign", 0.01))
            return ChoiceJudgement(key, probs, None, "fixture", "1", "resolved", 1)

    content = MagicMock()
    content.documents.return_value = (
        ()
        if scenario in ("retired", "excluded")
        else (SimpleNamespace(chunk=evidence.chunk, source_id=evidence.citation.source_id),)
    )
    content.resolve.return_value = (
        SimpleNamespace(citation=evidence.citation, text="manipulated")
        if scenario == "tampered"
        else ResolvedCitation(evidence.citation, evidence.text)
    )
    mode = FeatureMode(scenario) if scenario in ("shadow", "off") else FeatureMode.ON
    policy = FlashcardGroundingPolicy(
        mode,
        "resolved",
        0.45,
        0.3,
        max_input_bytes=1 if scenario == "oversize" else 64000,
        timeout_seconds=0.01 if scenario == "deadline" else 1,
    )
    judge_port = Judge()
    from study_agent.ports.judgement import ChoiceJudgementPort

    selected_port: ChoiceJudgementPort = judge_port
    if scenario == "denied":
        from cardine.application.study_semantics import ConsentChoiceJudgementPort
        from cardine.integrations.study_agent.course_policy import ProjectionConsentView
        from study_agent.domain import CourseId

        consent = MagicMock()
        consent.get.return_value = None
        selected_port = ConsentChoiceJudgementPort(
            judge_port,
            CourseId("course"),
            cast(ProjectionConsentView, consent),
        )
    gate = FlashcardGroundingValidator(
        Core(),
        content=cast(GroundingContentPort, content),
        judgement=selected_port,
        policy=policy,
        retired_source_ids=lambda: frozenset(),
    )
    inputs: JsonObject = {"prepared_scope": prepared.to_json(), "draft": {}, "requested_ceiling": 1}
    if scenario == "cancellation":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(gate.validate(inputs))
        return
    result = asyncio.run(gate.validate(inputs))
    assert result.passed == (scenario in ("positive", "shadow", "off"))
    if scenario in (
        "foreign",
        "tampered",
        "retired",
        "excluded",
        "oversize",
        "schema-failure",
        "denied",
        "off",
    ):
        assert calls == []
    if result.passed:
        assert result.result == output
    elif scenario != "schema-failure":
        assert result.result == {"grounding": "unverified"}
        assert result.reason == "flashcard_grounding_unverified"


def test_existing_v2_config_defaults_gate_off_without_rewrite() -> None:
    import json

    from study_agent.repository_config import LocalRepositoryConfig

    config = LocalRepositoryConfig()
    raw = json.loads(config.to_bytes())
    for key in ("flashcard_grounding_mode", "grounding_probability", "grounding_margin"):
        del raw["features"][key]
    payload = json.dumps(raw).encode()
    decoded = LocalRepositoryConfig.from_bytes(payload)
    assert decoded.features.flashcard_grounding_mode is FeatureMode.OFF
    assert payload == json.dumps(raw).encode()


def test_grounding_policy_changes_checkpoint_identity() -> None:
    from dataclasses import replace

    policy = FlashcardGroundingPolicy(FeatureMode.ON, "resolved", 0.9, 0.2)
    for changed in (
        replace(policy, mode=FeatureMode.SHADOW),
        replace(policy, probability=0.95),
        replace(policy, resolved_model_id="new-resolved"),
    ):
        assert changed.fingerprint != policy.fingerprint


@pytest.mark.parametrize("missing", (
    ("flashcard_grounding_mode",), ("grounding_probability",), ("grounding_margin",),
    ("flashcard_grounding_mode", "grounding_probability"),
    ("flashcard_grounding_mode", "grounding_margin"),
    ("grounding_probability", "grounding_margin"),
))
def test_partial_grounding_configuration_is_rejected(missing: tuple[str, ...]) -> None:
    import json

    from study_agent.repository_config import LocalConfigError, LocalRepositoryConfig
    raw = json.loads(LocalRepositoryConfig().to_bytes())
    for key in missing:
        del raw["features"][key]
    with pytest.raises(LocalConfigError):
        LocalRepositoryConfig.from_bytes(json.dumps(raw).encode())


@pytest.mark.parametrize("fingerprint", (None, "old-policy"))
def test_recovery_rejects_ungrounded_checkpoint_before_proposal_registration(
    fingerprint: str | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    import cardine.application.flashcard_proposals as module
    from cardine.hosts import TutorCapabilityCompletionReference
    from study_agent.capabilities import PROPOSE_FLASHCARDS_MANIFEST
    from study_agent.domain import CorrelationId, CourseId, ExecutionContext, PrincipalKind, RunId
    from study_agent.flashcards.lesson_worker_contracts import LessonWorkerCheckpoint

    composition = object.__new__(module.FlashcardProposalComposition)
    composition._grounding_policy = FlashcardGroundingPolicy(FeatureMode.ON, "resolved", 0.9, 0.2)
    composition._lesson_store = MagicMock()
    composition._artifact_service = MagicMock()
    summary = {} if fingerprint is None else {"grounding_policy_fingerprint": fingerprint}
    request = replace(_request(), continuation_summary=summary)
    monkeypatch.setattr(
        LessonWorkerCheckpoint, "from_bytes", lambda _: SimpleNamespace(request=request)
    )
    reached_worker = MagicMock(side_effect=RuntimeError("stale request reached worker"))
    monkeypatch.setattr(composition, "_worker_for_request", reached_worker)
    reference = TutorCapabilityCompletionReference(
        "propose_flashcards@1", PROPOSE_FLASHCARDS_MANIFEST.fingerprint,
        RunId("run-recovery-policy"), "a" * 64, "b" * 64,
    )
    context = ExecutionContext(
        PrincipalKind.SERVICE, "fixture", CourseId("course"), CorrelationId("recovery")
    )
    assert composition.recover(reference, context) is None
    reached_worker.assert_not_called()
    composition._artifact_service.record_generated.assert_not_called()
