"""ADR-0027: one flat Jev action choice, bounded clarification and ON fallback binding."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from cardine.application.flashcard_scope import FlashcardScope, learner_fingerprint
from cardine.hosts.clarification_recovery import ClarificationRecoveryTutorDecisionPort
from cardine.hosts.contracts import (
    AskLearnerDecision,
    AssistantMessageDecision,
    StartCapabilityDecision,
    TutorHostContext,
    decision_schema,
    validate_decision,
)
from cardine.hosts.routing import TutorRoutingReceipt
from study_agent.adapters.judgement.jev import JevProviderError
from study_agent.capabilities.builtin import PROPOSE_FLASHCARDS_MANIFEST
from study_agent.domain._validation import JsonObject
from study_agent.domain.features import FeatureMode
from study_agent.ports.tutor_host import TutorInterruptionToken
from tests.unit.hosts.test_routing import (
    EMPTY,
    Judge,
    Legacy,
    Model,
    Token,
    capability,
    context,
    decide,
    router,
    tool,
)

QUESTION = "Vuoi una flashcard sul complesso acido grasso sintasi I?"
FLASHCARDS = capability("propose_flashcards", PROPOSE_FLASHCARDS_MANIFEST.input_schema)


def answered(
    answer: str = "si",
    *,
    tools: tuple[JsonObject, ...] = (),
) -> TutorHostContext:
    """Newest learner message answers the newest tutor learner_question."""

    return replace(
        context(capabilities=(FLASHCARDS,), tools=tools),
        tutor_snapshot={
            "timeline": (
                {"kind": "learner", "content": "genera una flashcard sul complesso",
                 "course_sequence": 5, "interaction_id": "older"},
                {"kind": "learner", "content": answer, "course_sequence": 7,
                 "interaction_id": "current-human"},
            ),
            "tutor_presentations": (
                {"kind": "learner_question", "content": QUESTION, "course_sequence": 6},
            ),
            "harness_tools": tools,
        },
    )


def branch_kinds(schema: JsonObject) -> set[object]:
    decision = cast(Any, schema)["properties"]["decision"]
    return {branch["properties"]["kind"]["enum"][0] for branch in decision["anyOf"]}


def flashcard_inputs(utterance: str = "si") -> tuple[FlashcardScope, JsonObject]:
    scope = FlashcardScope(
        "explicit_topic", "default", learner_fingerprint(utterance), "current-human"
    )
    return scope, {
        "query": "acido grasso sintasi", "scope": scope.encode(), "language": "it",
        "candidate_ceiling": 24, "continuation_summary_json": None,
    }


def test_one_flat_action_choice_carries_versioned_guidance_as_criteria() -> None:
    explain = capability("explain_concept", EMPTY)
    ctx = context(capabilities=(explain, FLASHCARDS), tools=(tool("artifact.get"),))
    judge = Judge("capability:explain_concept")
    assert decide(router(judge, Model()), ctx) == StartCapabilityDecision("explain_concept", {})
    assert len(judge.requests) == 1
    request = judge.requests[0]
    assert request.metadata["use_case"] == "route"
    options = {item.key: item.description for item in request.options}
    assert set(options) == {
        "assistant_message", "ask_learner", "stop", "capability:explain_concept",
        "capability:propose_flashcards", "tool:artifact.get",
    }
    assert "Spiegami il legame peptidico" in options["capability:explain_concept"]
    assert "Genera 3 cards" in options["capability:propose_flashcards"]
    assert "proposal status" in options["tool:artifact.get"]
    assert "confirm" in options["ask_learner"]
    assert "single action" in request.instruction


def test_unknown_operations_still_receive_a_bounded_generic_criterion() -> None:
    judge = Judge("tool:study.read")
    decide(router(judge, Model()), context(tools=(tool("study.read"),)))
    options = {item.key: item.description for item in judge.requests[0].options}
    assert options["capability:a.capability"] == "Start the advertised study workflow a.capability."
    assert options["tool:study.read"] == "Invoke the advertised repository tool study.read."


def test_route_threshold_applies_to_the_single_concrete_action() -> None:
    receipts: list[TutorRoutingReceipt] = []
    judge, legacy = Judge("capability:a.capability"), Legacy()
    judge.probability = 0.6
    assert decide(router(judge, Model(), legacy, receipts=receipts)) == legacy.decision
    assert receipts[0].fallback_reason == "insufficient_separation"
    assert [item.use_case for item in receipts[0].judgements] == ["route"]


def test_answered_clarification_makes_another_question_illegal_for_every_port() -> None:
    ctx = answered()
    kinds = branch_kinds(decision_schema(ctx))
    assert "ask_learner" not in kinds and "assistant_message" in kinds
    assert "ask_learner" in branch_kinds(decision_schema(context()))
    with pytest.raises(ValueError, match="clarification"):
        validate_decision(AskLearnerDecision("Vuoi davvero?"), ctx)
    validate_decision(AskLearnerDecision("Quale lezione?"), context())


def test_jev_receives_the_answered_question_and_cannot_ask_again() -> None:
    scope, payload = flashcard_inputs()
    judge = Judge("capability:propose_flashcards", "explicit_topic", "default", "supported")
    decision = decide(router(judge, Model(payload)), answered())
    assert isinstance(decision, StartCapabilityDecision)
    assert FlashcardScope.parse(decision.inputs["scope"]) == scope
    route = judge.requests[0]
    assert "ask_learner" not in {item.key for item in route.options}
    state = cast(JsonObject, route.state)
    assert state["answered_tutor_question"] == {"question": QUESTION, "answer": "si"}
    binding = cast(JsonObject, judge.requests[-1].state)
    assert binding["answered_tutor_question"] == {"question": QUESTION, "answer": "si"}


@pytest.mark.parametrize("scope,profile", (("ambiguous", None), ("explicit_topic", "ambiguous")))
def test_ambiguous_flashcards_after_a_clarification_end_with_guidance_not_a_question(
    scope: str, profile: str | None,
) -> None:
    judge = Judge("capability:propose_flashcards", scope, *(() if profile is None else (profile,)))
    legacy = Legacy()
    decision = decide(router(judge, Model(), legacy), answered())
    assert isinstance(decision, AssistantMessageDecision)
    assert "?" not in decision.message and "argomento" in decision.message
    assert legacy.calls == 0


def test_unsupported_topic_binding_after_a_clarification_ends_with_guidance() -> None:
    _, payload = flashcard_inputs()
    judge = Judge("capability:propose_flashcards", "explicit_topic", "default", "ambiguous")
    decision = decide(router(judge, Model(payload)), answered())
    assert isinstance(decision, AssistantMessageDecision)


def test_on_fallback_flashcard_route_is_bound_by_jev_scope_contract() -> None:
    scope, payload = flashcard_inputs()
    legacy = Legacy(StartCapabilityDecision("propose_flashcards", {
        "query": "complesso", "scope": "free-form legacy scope", "language": "it",
        "candidate_ceiling": 24, "continuation_summary_json": None,
    }))
    receipts: list[TutorRoutingReceipt] = []
    judge = Judge(JevProviderError("jev_timeout"), "explicit_topic", "default", "supported")
    decision = decide(router(judge, Model(payload), legacy, receipts=receipts), answered())
    assert isinstance(decision, StartCapabilityDecision)
    assert FlashcardScope.parse(decision.inputs["scope"]) == scope
    assert decision.inputs["query"] == "acido grasso sintasi"
    validate_decision(decision, answered())
    receipt = receipts[0]
    assert receipt.fallback_reason == "judgement_provider_failure"
    assert receipt.judgements[0].error_code == "jev_timeout"
    assert receipt.fallback_binding == "flashcard_scope_bound"
    assert receipt.selected_capability_id == "propose_flashcards"
    assert [item.use_case for item in receipt.judgements] == [
        "route", "flashcard_scope", "flashcard_profile", "flashcard_topic_binding"]


def test_unresolved_fallback_flashcard_binding_ends_with_fixed_guidance() -> None:
    legacy = Legacy(StartCapabilityDecision("propose_flashcards", {
        "query": "complesso", "scope": "free-form legacy scope", "language": "it",
        "candidate_ceiling": 24, "continuation_summary_json": None,
    }))
    receipts: list[TutorRoutingReceipt] = []
    judge = Judge(RuntimeError("PRIVATE"), RuntimeError("PRIVATE"))
    decision = decide(router(judge, Model(), legacy, receipts=receipts), answered())
    assert isinstance(decision, AssistantMessageDecision)
    assert receipts[0].fallback_binding == "flashcard_scope_unresolved"
    assert receipts[0].judgements[0].error_code == "unclassified"
    assert "PRIVATE" not in repr(receipts)


def test_shadow_fallback_is_returned_without_flashcard_binding() -> None:
    shadow_legacy = Legacy(AssistantMessageDecision("Legacy only"))
    judge = Judge("capability:propose_flashcards", "explicit_topic", "default", "supported")
    _, payload = flashcard_inputs()
    decision = decide(
        router(judge, Model(payload), shadow_legacy, mode=FeatureMode.SHADOW), answered()
    )
    assert decision == AssistantMessageDecision("Legacy only")


def test_failed_fallback_still_records_a_content_free_receipt() -> None:
    from cardine.adapters.model.tutor_decision import ModelTutorDecisionError

    receipts: list[TutorRoutingReceipt] = []
    legacy = Legacy(ModelTutorDecisionError("PRIVATE", failure_reason="protocol_error"))
    with pytest.raises(ModelTutorDecisionError):
        decide(router(Judge(RuntimeError("down")), Model(), legacy, receipts=receipts))
    assert receipts[0].legacy_failure == "protocol_error"
    assert receipts[0].legacy_kind is None
    assert "PRIVATE" not in repr(receipts)


def test_invalid_fallback_decision_is_recorded_before_it_surfaces() -> None:
    receipts: list[TutorRoutingReceipt] = []
    legacy = Legacy(AskLearnerDecision("Vuoi una flashcard?"))
    with pytest.raises(ValueError, match="clarification"):
        decide(router(Judge(RuntimeError("down")), Model(), legacy, receipts=receipts), answered())
    assert receipts[0].legacy_failure == "invalid_decision"


def test_unsafe_failure_codes_are_never_copied_into_receipts() -> None:
    receipts: list[TutorRoutingReceipt] = []
    judge = Judge(JevProviderError("PRIVATE SOURCE TEXT"))
    decide(router(judge, Model(), Legacy(), receipts=receipts))
    assert receipts[0].judgements[0].error_code == "unclassified"


def test_receipt_fields_serialize_through_the_repository_store(tmp_path: Path) -> None:
    from cardine.application.routing_calibration import load_receipts
    from cardine.application.study_semantics import RoutingReceiptStore
    from study_agent.adapters.sqlite.run_store import SQLiteRunStore

    receipts: list[TutorRoutingReceipt] = []
    decide(router(Judge(JevProviderError("jev_timeout")), Model(), Legacy(), receipts=receipts))
    RoutingReceiptStore(SQLiteRunStore(tmp_path / "runs.sqlite3")).record(receipts[0])
    (stored,) = load_receipts(tmp_path / "runs.sqlite3")
    assert stored["legacy_failure"] is None and stored["fallback_binding"] is None
    assert cast(list[JsonObject], stored["judgements"])[0]["error_code"] == "jev_timeout"


def test_clarification_recovery_uses_the_shared_answered_question_state() -> None:
    seen: list[TutorHostContext] = []

    class Delegate:
        async def decide(
            self, ctx: TutorHostContext, interruption: TutorInterruptionToken
        ) -> AssistantMessageDecision:
            seen.append(ctx)
            return AssistantMessageDecision("ok")

    asyncio.run(ClarificationRecoveryTutorDecisionPort(Delegate()).decide(answered(), Token()))
    resolution = cast(JsonObject, seen[0].tutor_snapshot["clarification_resolution"])
    assert resolution["previous_question"] == QUESTION
    assert resolution["current_answer"] == "si"



def test_fallback_never_repeats_jev_scope_steps_that_already_failed_this_turn() -> None:
    legacy = Legacy(StartCapabilityDecision("propose_flashcards", {
        "query": "complesso", "scope": "free-form legacy scope", "language": "it",
        "candidate_ceiling": 24, "continuation_summary_json": None,
    }))
    receipts: list[TutorRoutingReceipt] = []
    judge = Judge("capability:propose_flashcards", RuntimeError("scope down"))
    decision = decide(router(judge, Model(), legacy, receipts=receipts), answered())
    assert decision == AssistantMessageDecision(
        "Non riesco a stabilire con sicurezza l'argomento delle flashcard. Scrivi "
        "l'argomento in una frase, per esempio «flashcard sull'acido grasso sintasi», "
        "oppure seleziona una lezione."
    )
    assert len(judge.requests) == 2 and legacy.calls == 1
    assert receipts[0].fallback_binding == "flashcard_scope_unresolved"
