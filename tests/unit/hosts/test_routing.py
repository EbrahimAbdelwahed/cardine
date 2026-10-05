from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

import pytest

from cardine.hosts.contracts import (
    AdvertisedCapability,
    AnswerDialogueDecision,
    AssistantMessageDecision,
    InvokeToolDecision,
    PendingContinuationDescriptor,
    StartCapabilityDecision,
    TutorDecision,
    TutorDecisionKind,
    TutorHostContext,
    validate_decision,
)
from cardine.hosts.routing import (
    RoutingThreshold,
    RoutingTutorDecisionPort,
    TutorRoutingPolicy,
    TutorRoutingReceipt,
)
from study_agent.domain._validation import JsonObject
from study_agent.domain.features import FeatureMode
from study_agent.ports.judgement import (
    ChoiceJudgement,
    ChoiceJudgementRequest,
    ChoiceProbability,
)
from study_agent.ports.model import (
    ModelCapabilities,
    ModelError,
    ModelErrorCode,
    ModelFinishReason,
    ModelInvocation,
    ModelPort,
    ModelRequest,
    ModelResponse,
)
from study_agent.ports.tutor_host import RetryableTutorDecisionError, TutorInterruptionToken

EMPTY: JsonObject = {
    "type": "object",
    "properties": {},
    "required": (),
    "additionalProperties": False,
}
TOPIC: JsonObject = {
    "type": "object",
    "properties": {"topic": {"type": "string", "minLength": 1}},
    "required": ("topic",),
    "additionalProperties": False,
}


class Token:
    interrupted = False

    def is_interrupted(self) -> bool:
        return self.interrupted


class Judge:
    def __init__(self, *selections: str | Exception) -> None:
        self.selections = list(selections)
        self.requests: list[ChoiceJudgementRequest] = []
        self.malformed: str | None = None
        self.after: Token | None = None
        self.probability = 0.97

    async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
        self.requests.append(request)
        selected = self.selections.pop(0)
        if isinstance(selected, Exception):
            raise selected
        probabilities = tuple(
            ChoiceProbability(
                option.key,
                self.probability
                if option.key == selected
                else (1 - self.probability) / (len(request.options) - 1),
            )
            for option in request.options
        )
        result = ChoiceJudgement(
            selected,
            probabilities,
            self.probability,
            "fake",
            "1",
            "fake-model",
            1,
            {"tokens": 2, "raw_extension": "PRIVATE SOURCE TEXT"},
        )
        if self.malformed == "unknown":
            object.__setattr__(result, "selected_key", "not-advertised")
        if self.malformed == "nan":
            object.__setattr__(
                result,
                "probabilities",
                (
                    ChoiceProbability(probabilities[0].key, 0.5),
                    ChoiceProbability(probabilities[1].key, 0.5),
                ),
            )
            object.__setattr__(result.probabilities[0], "probability", float("nan"))
        if self.after is not None:
            self.after.interrupted = True
        return result


class Model:
    capabilities = ModelCapabilities(structured_output=True)

    def __init__(self, *payloads: JsonObject | Exception) -> None:
        self.payloads = list(payloads)
        self.requests: list[ModelRequest] = []
        self.finish_reason = ModelFinishReason.STOP

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return ModelResponse(
            "",
            None,
            self.finish_reason,
            ModelInvocation("fake", "1", "payload-model"),
            structured_output=payload,
        )


class Legacy:
    def __init__(self, decision: TutorDecision | Exception | None = None) -> None:
        self.decision = decision or AssistantMessageDecision("Legacy result")
        self.calls = 0

    async def decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        self.calls += 1
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


def capability(name: str = "a.capability", schema: JsonObject = EMPTY) -> AdvertisedCapability:
    return AdvertisedCapability(name, f"{name}@1.0.0", "a" * 64, schema, True)


def context(
    *,
    capabilities: tuple[AdvertisedCapability, ...] = (capability(),),
    pending_schema: JsonObject | None = None,
    tools: tuple[JsonObject, ...] = (),
) -> TutorHostContext:
    return TutorHostContext(
        "course",
        "session",
        8,
        8,
        {
            "timeline": (
                {"kind": "learner", "content": "IRRELEVANT OLD MESSAGE"},
                {"kind": "assistant", "content": "PRIVATE SOURCE TEXT"},
                {"kind": "learner", "content": "Review valves"},
            ),
            "harness_tools": tools,
        },
        {"estimates": ({"label": "PRIVATE EVIDENCE"},)},
        capabilities,
        None
        if pending_schema is None
        else PendingContinuationDescriptor(
            "b" * 64,
            capabilities[0].identity,
            "confirm",
            "Continue?",
            pending_schema,
        ),
    )


def policy(mode: FeatureMode = FeatureMode.ON) -> TutorRoutingPolicy:
    threshold = RoutingThreshold(0.8, 0.3)
    return TutorRoutingPolicy("test-only-v1", threshold, threshold, threshold, threshold, mode)


def router(
    judge: Judge,
    model: Model,
    legacy: Legacy | None = None,
    *,
    mode: FeatureMode = FeatureMode.ON,
    receipts: list[TutorRoutingReceipt] | None = None,
    configured: TutorRoutingPolicy | None = None,
) -> RoutingTutorDecisionPort:
    return RoutingTutorDecisionPort(
        judge,
        cast(ModelPort, model),
        configured or policy(mode),
        legacy=legacy,
        record_receipt=None if receipts is None else receipts.append,
    )


def decide(port: RoutingTutorDecisionPort, ctx: TutorHostContext | None = None) -> TutorDecision:
    return asyncio.run(port.decide(ctx or context(), Token()))


def test_off_never_calls_judgement_or_payload_model() -> None:
    judge, model, legacy = Judge(), Model(), Legacy()
    assert decide(router(judge, model, legacy, mode=FeatureMode.OFF)) == legacy.decision
    assert legacy.calls == 1 and not judge.requests and not model.requests


def test_policy_defaults_to_shadow_and_requires_legacy() -> None:
    configured = replace(policy(), mode=FeatureMode.SHADOW)
    assert configured.mode is FeatureMode.SHADOW
    with pytest.raises(ValueError, match="legacy"):
        router(Judge(), Model(), configured=configured)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), -0.1, 1.1, True))
def test_policy_rejects_invalid_thresholds(value: float) -> None:
    with pytest.raises(ValueError):
        RoutingThreshold(value, 0.2)


def test_general_routes_are_legal_and_projection_is_minimal() -> None:
    judge, model = Judge("assistant_message"), Model({"message": "Try another topic."})
    result = decide(router(judge, model), context(capabilities=()))
    assert result == AssistantMessageDecision("Try another topic.")
    assert {item.key for item in judge.requests[0].options} == {
        "assistant_message",
        "ask_learner",
        "stop",
    }
    assert judge.requests[0].state == {"latest_learner_utterance": "Review valves"}
    assert len(model.requests) == 1
    schema = model.requests[0].structured_output
    assert schema is not None
    properties = schema.schema["properties"]
    assert isinstance(properties, Mapping) and "decision" not in properties
    payload = model.requests[0].messages[1].content
    assert "PRIVATE" not in payload and "IRRELEVANT" not in payload
    assert "harness_tools" not in payload


def test_selected_payload_receives_recent_conversation_and_tool_results() -> None:
    ctx = replace(
        context(capabilities=(capability(schema=TOPIC),)),
        tutor_snapshot={
            "timeline": (
                {"kind": "learner", "content": "Explain DNA polymerase", "course_sequence": 5},
                {
                    "kind": "learner",
                    "content": "Create a flashcard about this",
                    "course_sequence": 8,
                },
            ),
            "tutor_presentations": (
                {
                    "kind": "assistant_message",
                    "content": "DNA polymerase explanation",
                    "course_sequence": 6,
                },
            ),
            "agent_observations": (
                {
                    "tool_name": "conversation.read",
                    "status": "succeeded",
                    "action_fingerprint": "f" * 64,
                    "result": {"entries": ({"excerpt": "DNA polymerase"},)},
                },
            ),
        },
    )
    judge, model = Judge("capability:a.capability"), Model({"topic": "DNA polymerase"})
    decide(router(judge, model), ctx)
    payload = json.loads(model.requests[0].messages[1].content)
    assert "DNA polymerase explanation" in json.dumps(payload)
    assert "conversation.read" in json.dumps(payload)
    assert "action_fingerprint" not in json.dumps(payload)
    assert "input_schema" not in json.dumps(payload)


def test_selected_payload_context_is_bounded_and_excludes_future_or_private_data() -> None:
    ctx = replace(
        context(capabilities=(capability(schema=TOPIC),)),
        tutor_snapshot_sequence=100,
        tutor_snapshot={
            "timeline": (
                {"kind": "learner", "content": "FUTURE", "course_sequence": 101},
                {"kind": "learner", "content": "UNSEQUENCED"},
                *(
                    {"kind": "learner", "content": "x" * 4_000, "course_sequence": i}
                    for i in range(1, 16)
                ),
            ),
            "agent_observations": ({
                "tool_name": "conversation.read", "status": "succeeded",
                "action_fingerprint": "PRIVATE-AUTHORITY",
                "result": {"entries": ({"excerpt": "recent result"},)},
            },),
        },
    )
    judge, model = Judge("capability:a.capability"), Model({"topic": "valves"})
    decide(router(judge, model), ctx)
    payload = json.loads(model.requests[0].messages[1].content)
    assert sum(len(item["content"]) for item in payload["recent_conversation"]) <= 4_000
    assert len(payload["recent_conversation"]) <= 8
    rendered = json.dumps(payload)
    assert all(value not in rendered for value in ("FUTURE", "UNSEQUENCED", "PRIVATE"))
    assert "recent result" in rendered



def test_tool_observations_include_omission_markers_inside_the_byte_budget() -> None:
    result = {f"field_{i:02}": "x" * 500 for i in range(15)}
    ctx = replace(
        context(capabilities=(capability(schema=TOPIC),)),
        tutor_snapshot={"agent_observations": tuple({
            "tool_name": "conversation.read", "status": "succeeded", "result": result,
        } for _ in range(4))},
    )
    judge, model = Judge("capability:a.capability"), Model({"topic": "valves"})
    decide(router(judge, model), ctx)
    payload = json.loads(model.requests[0].messages[1].content)
    observations = payload["tool_observations"]
    assert len(json.dumps({"tool_observations": observations}, separators=(",", ":"))
               .encode()) <= 8_000
    assert any(item.get("result_omitted") for item in observations)

def test_capability_options_only_advertised_and_empty_inputs_skip_model() -> None:
    judge, model = Judge("capability:b.capability"), Model()
    result = decide(
        router(judge, model),
        context(
            capabilities=(
                capability("a.capability"),
                capability("b.capability"),
            )
        ),
    )
    assert result == StartCapabilityDecision("b.capability", {})
    assert {item.key for item in judge.requests[0].options} >= {
        "capability:a.capability", "capability:b.capability"}
    assert len(judge.requests) == 1 and not model.requests
    assert "input_schema" not in json.dumps(dict(cast(JsonObject, judge.requests[0].state)))


def test_selected_capability_gets_only_its_input_schema() -> None:
    judge = Judge("capability:a.capability")
    model = Model({"topic": "valves"})
    result = decide(
        router(judge, model),
        context(
            capabilities=(
                capability("a.capability", TOPIC),
                capability("b.capability"),
            )
        ),
    )
    assert result == StartCapabilityDecision("a.capability", {"topic": "valves"})
    assert len(model.requests) == 1
    constraint = model.requests[0].structured_output
    assert constraint is not None and constraint.schema == TOPIC
    content = model.requests[0].messages[1].content
    assert "b.capability" not in content and "continuation" not in content


def test_fixed_required_literal_inputs_skip_model() -> None:
    fixed: JsonObject = {
        **TOPIC,
        "properties": {
            "topic": {"type": "string", "enum": ("valves",)},
        },
    }
    judge, model = Judge("capability:a.capability"), Model()
    assert decide(router(judge, model), context(capabilities=(capability(schema=fixed),))) == (
        StartCapabilityDecision("a.capability", {"topic": "valves"})
    )
    assert len(judge.requests) == 1 and not model.requests


@pytest.mark.parametrize(
    ("schema", "selected", "expected"),
    (
        ({"type": "boolean"}, "response_1", True),
        ({"type": "string", "enum": ("yes", "no")}, "response_0", "yes"),
    ),
)
def test_closed_pending_dialogue_uses_legal_values_and_trusted_fingerprint(
    schema: JsonObject,
    selected: str,
    expected: object,
) -> None:
    judge, model = Judge(selected), Model()
    result = decide(router(judge, model), context(pending_schema=schema))
    assert result == AnswerDialogueDecision("b" * 64, expected)  # type: ignore[arg-type]
    assert len(judge.requests) == 1 and not model.requests
    assert "b" * 64 not in json.dumps(dict(cast(JsonObject, judge.requests[0].state)))
    assert {item.key for item in judge.requests[0].options} == {"response_0", "response_1"}


def test_pending_literal_requires_no_semantic_choice_or_generation() -> None:
    judge, model = Judge(), Model()
    result = decide(
        router(judge, model),
        context(
            pending_schema={
                "type": "string",
                "enum": ("continue",),
            }
        ),
    )
    assert result == AnswerDialogueDecision("b" * 64, "continue")
    assert not judge.requests and not model.requests


def test_complex_dialogue_generates_only_response_payload() -> None:
    judge, model = Judge(), Model({"response": {"topic": "valves"}})
    result = decide(router(judge, model), context(pending_schema=TOPIC))
    assert result == AnswerDialogueDecision("b" * 64, {"topic": "valves"})
    assert not judge.requests and len(model.requests) == 1
    constraint = model.requests[0].structured_output
    assert constraint is not None
    properties = constraint.schema["properties"]
    assert isinstance(properties, Mapping) and properties == {"response": TOPIC}
    assert "b" * 64 not in model.requests[0].messages[1].content


@pytest.mark.parametrize("bad", ("unknown", "nan"))
def test_malformed_judgement_falls_back_exactly_once(bad: str) -> None:
    judge, model, legacy = Judge("assistant_message"), Model(), Legacy()
    judge.malformed = bad
    receipts: list[TutorRoutingReceipt] = []
    assert decide(router(judge, model, legacy, receipts=receipts)) == legacy.decision
    assert legacy.calls == 1 and not model.requests
    assert receipts[0].fallback_reason == "malformed_judgement"


def test_judgement_provider_failure_falls_back_once_without_raw_error_text() -> None:
    receipts: list[TutorRoutingReceipt] = []
    judge, legacy = Judge(RuntimeError("PRIVATE SOURCE TEXT")), Legacy()
    assert decide(router(judge, Model(), legacy, receipts=receipts)) == legacy.decision
    assert legacy.calls == 1
    assert receipts[0].fallback_reason == "judgement_provider_failure"
    assert "PRIVATE SOURCE TEXT" not in repr(receipts)


def test_insufficient_separation_falls_back_before_generation() -> None:
    judge, legacy = Judge("assistant_message"), Legacy()
    judge.probability = 0.5
    assert decide(router(judge, Model(), legacy)) == legacy.decision
    assert legacy.calls == 1


def test_invalid_payload_and_host_authority_injection_fall_back_once() -> None:
    for payload in ({"wrong": 2}, {"topic": "valves", "course_id": "forged"}):
        judge, model, legacy = Judge("capability:a.capability"), Model(payload), Legacy()
        assert (
            decide(router(judge, model, legacy), context(capabilities=(capability(schema=TOPIC),)))
            == legacy.decision
        )
        assert legacy.calls == 1 and len(model.requests) == 1


def test_legacy_failure_is_not_retried_or_converted_to_stop() -> None:
    legacy = Legacy(RuntimeError("legacy unavailable"))
    with pytest.raises(RuntimeError, match="legacy unavailable"):
        decide(router(Judge(RuntimeError("judge unavailable")), Model(), legacy))
    assert legacy.calls == 1


def test_disabled_emergency_fallback_surfaces_failure() -> None:
    legacy = Legacy()
    with pytest.raises(RetryableTutorDecisionError, match="routing failed"):
        decide(
            router(
                Judge(RuntimeError("unavailable")),
                Model(),
                legacy,
                configured=replace(policy(), emergency_fallback=False),
            )
        )
    assert legacy.calls == 0


def test_shadow_records_candidate_but_returns_legacy_with_no_text_receipt() -> None:
    judge = Judge("capability:a.capability")
    legacy = Legacy()
    receipts: list[TutorRoutingReceipt] = []
    result = decide(router(judge, Model(), legacy, mode=FeatureMode.SHADOW, receipts=receipts))
    assert result == legacy.decision and legacy.calls == 1
    receipt = receipts[0]
    assert receipt.candidate_kind is TutorDecisionKind.START_CAPABILITY
    assert receipt.legacy_kind is TutorDecisionKind.ASSISTANT_MESSAGE
    assert receipt.disagreement and receipt.candidate_validated
    assert not receipt.narrow_generation_used
    assert receipt.judgements[0].usage == {"tokens": 2}
    assert "Review valves" not in repr(receipt) and "PRIVATE" not in repr(receipt)


def test_interruption_before_or_after_judgement_never_falls_back() -> None:
    for before in (True, False):
        judge, legacy, token = Judge("assistant_message"), Legacy(), Token()
        token.interrupted = before
        judge.after = token
        with pytest.raises(RetryableTutorDecisionError, match="interrupted"):
            asyncio.run(router(judge, Model(), legacy).decide(context(), token))
        assert legacy.calls == 0
        assert len(judge.requests) == (0 if before else 1)


def test_narrow_model_cancellation_never_falls_back() -> None:
    model, legacy = Model(ModelError(ModelErrorCode.CANCELLED, "cancelled")), Legacy()
    with pytest.raises(RetryableTutorDecisionError, match="interrupted"):
        decide(router(Judge("assistant_message"), model, legacy))
    assert legacy.calls == 0


def test_async_cancellation_never_falls_back() -> None:
    class CancelJudge(Judge):
        async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
            raise asyncio.CancelledError

    legacy = Legacy()
    with pytest.raises(asyncio.CancelledError):
        decide(router(CancelJudge(), Model(), legacy))
    assert legacy.calls == 0


def test_ask_learner_generation_is_a_single_question_payload() -> None:
    from cardine.hosts.contracts import AskLearnerDecision

    judge, model = Judge("ask_learner"), Model({"question": "Which lesson?"})
    assert decide(router(judge, model)) == AskLearnerDecision("Which lesson?")
    assert len(model.requests) == 1
    constraint = model.requests[0].structured_output
    assert constraint is not None and constraint.name == "question"


def test_stop_reason_uses_bounded_choice_without_generation() -> None:
    from cardine.hosts.contracts import StopDecision, TutorStopReason

    judge, model = Judge("stop", "completed"), Model()
    assert decide(router(judge, model)) == StopDecision(TutorStopReason.COMPLETED)
    assert {item.key for item in judge.requests[1].options} == {
        "completed",
        "no_safe_action",
    }
    assert not model.requests


def test_host_validation_failure_uses_one_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import cardine.hosts.routing as routing_module

    original = validate_decision

    def reject_candidate(decision: TutorDecision, ctx: TutorHostContext) -> None:
        if isinstance(decision, StartCapabilityDecision):
            raise ValueError("stale advertised capability")
        original(decision, ctx)

    monkeypatch.setattr(routing_module, "validate_decision", reject_candidate)
    legacy = Legacy()
    receipts: list[TutorRoutingReceipt] = []
    assert decide(router(Judge("capability:a.capability"), Model(), legacy, receipts=receipts)) == (
        legacy.decision
    )
    assert legacy.calls == 1
    assert receipts[0].fallback_reason == "validation_failure"
    assert receipts[0].candidate_kind is TutorDecisionKind.START_CAPABILITY
    assert not receipts[0].candidate_validated


def test_overlong_latest_utterance_is_bounded_before_provider_calls() -> None:
    ctx = replace(
        context(), tutor_snapshot={"timeline": ({"kind": "learner", "content": "x" * 10_000},)}
    )
    judge = Judge("capability:a.capability")
    configured = replace(policy(), maximum_utterance_characters=100)
    decide(router(judge, Model(), configured=configured), ctx)
    assert cast(JsonObject, judge.requests[0].state)["latest_learner_utterance"] == "x" * 100


def test_generation_failure_in_shadow_runs_legacy_once() -> None:
    legacy = Legacy()
    receipts: list[TutorRoutingReceipt] = []
    assert (
        decide(
            router(
                Judge("assistant_message"),
                Model(RuntimeError("PRIVATE PAYLOAD")),
                legacy,
                mode=FeatureMode.SHADOW,
                receipts=receipts,
            )
        )
        == legacy.decision
    )
    assert legacy.calls == 1 and receipts[0].narrow_generation_used
    assert "PRIVATE" not in repr(receipts)


def test_cancelled_finish_reason_never_runs_legacy() -> None:
    model = Model({"message": "Discard me"})
    model.finish_reason = ModelFinishReason.CANCELLED
    legacy = Legacy()
    with pytest.raises(RetryableTutorDecisionError, match="interrupted"):
        decide(router(Judge("assistant_message"), model, legacy))
    assert legacy.calls == 0


def test_telemetry_failure_cannot_change_the_returned_decision() -> None:
    def fail(receipt: TutorRoutingReceipt) -> None:
        raise RuntimeError("observer down")

    port = RoutingTutorDecisionPort(
        Judge("capability:a.capability"),
        cast(ModelPort, Model()),
        policy(),
        record_receipt=fail,
    )
    assert decide(port) == StartCapabilityDecision("a.capability", {})


def tool(name: str, schema: JsonObject = EMPTY) -> JsonObject:
    return {"name": name, "input_schema": schema}


def test_tool_route_and_selection_are_only_currently_advertised() -> None:
    judge, model, legacy = Judge("tool:study.read"), Model(), Legacy()
    ctx = context(tools=(tool("study.read"), tool("study.write")))
    assert decide(router(judge, model, legacy), ctx) == InvokeToolDecision("study.read", {})
    assert legacy.calls == 0 and not model.requests
    keys = {item.key for item in judge.requests[0].options}
    assert {"tool:study.read", "tool:study.write"} <= keys and len(judge.requests) == 1
    projected = cast(JsonObject, judge.requests[0].state)
    assert "tools" not in projected
    assert "input_schema" not in repr(judge.requests[0])
    assert "PRIVATE" not in repr(judge.requests[0])


def test_no_advertised_tools_means_no_tool_route() -> None:
    judge = Judge("capability:a.capability")
    decide(router(judge, Model()))
    assert not any(item.key.startswith("tool:") for item in judge.requests[0].options)


def test_pending_dialogue_cannot_select_an_advertised_tool() -> None:
    judge, model = Judge("response_1"), Model()
    ctx = context(pending_schema={"type": "boolean"}, tools=(tool("study.write"),))
    assert decide(router(judge, model), ctx) == AnswerDialogueDecision("b" * 64, True)
    assert {item.key for item in judge.requests[0].options} == {"response_0", "response_1"}
    assert "study.write" not in repr(judge.requests[0])


def test_selected_tool_model_sees_only_its_schema_and_minimum_binding_context() -> None:
    judge, model = Judge("tool:study.write"), Model({"topic": "valves"})
    ctx = context(tools=(tool("study.read"), tool("study.write", TOPIC)))
    result = decide(router(judge, model), ctx)
    assert result == InvokeToolDecision("study.write", {"topic": "valves"})
    assert len(model.requests) == 1
    constraint = model.requests[0].structured_output
    assert constraint is not None and constraint.schema == TOPIC
    payload = json.loads(model.requests[0].messages[1].content)
    assert payload == {"latest_learner_utterance": "Review valves", "tool_name": "study.write"}
    assert "capability" not in model.requests[0].messages[1].content
    assert "study.read" not in model.requests[0].messages[1].content
    assert "course" not in model.requests[0].messages[1].content
    assert "session" not in model.requests[0].messages[1].content
    assert "fingerprint" not in model.requests[0].messages[1].content


def test_fixed_tool_arguments_require_no_generation_or_second_router() -> None:
    fixed: JsonObject = {
        **TOPIC,
        "properties": {
            "topic": {"type": "string", "enum": ("valves",)},
        },
    }
    judge, model, legacy = Judge("tool:study.write"), Model(), Legacy()
    result = decide(router(judge, model, legacy), context(tools=(tool("study.write", fixed),)))
    assert result == InvokeToolDecision("study.write", {"topic": "valves"})
    assert len(judge.requests) == 1 and not model.requests and legacy.calls == 0


def test_duplicate_tool_names_preserve_validators_first_descriptor_schema() -> None:
    judge, model = Judge("tool:study.write"), Model({"topic": "valves"})
    ctx = context(tools=(tool("study.write", TOPIC), tool("study.write")))
    result = decide(router(judge, model), ctx)
    assert result == InvokeToolDecision("study.write", {"topic": "valves"})
    validate_decision(result, ctx)
    assert len(judge.requests) == 1
    constraint = model.requests[0].structured_output
    assert constraint is not None and constraint.schema == TOPIC


def test_invalid_tool_descriptors_never_enter_choice_options() -> None:
    invalid_schema: JsonObject = {"type": "unsupported"}
    ctx = context(tools=(tool("bad.schema", invalid_schema), tool(" "), tool("x" * 129)))
    judge = Judge("capability:a.capability")
    decide(router(judge, Model()), ctx)
    assert not any(item.key.startswith("tool:") for item in judge.requests[0].options)


def test_unknown_selected_tool_emergency_fallback_is_exactly_once() -> None:
    class UnknownToolJudge(Judge):
        async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
            result = await super().judge(request)
            object.__setattr__(result, "selected_key", "tool:not.advertised")
            return result

    judge, legacy = UnknownToolJudge("tool:study.read"), Legacy()
    result = decide(
        router(judge, Model(), legacy),
        context(
            tools=(
                tool("study.read"),
                tool("study.write"),
            )
        ),
    )
    assert result == legacy.decision and legacy.calls == 1


def test_bad_tool_argument_payload_falls_back_only_when_configured() -> None:
    legacy = Legacy()
    with pytest.raises(RetryableTutorDecisionError, match="routing failed"):
        decide(
            router(
                Judge("tool:study.write"),
                Model({"topic": 17}),
                legacy,
                configured=replace(policy(), emergency_fallback=False),
            ),
            context(tools=(tool("study.write", TOPIC),)),
        )
    assert legacy.calls == 0
    assert (
        decide(
            router(
                Judge("tool:study.write"),
                Model({"topic": 17}),
                legacy,
            ),
            context(tools=(tool("study.write", TOPIC),)),
        )
        == legacy.decision
    )
    assert legacy.calls == 1


def test_tool_argument_generation_failure_in_shadow_calls_legacy_once() -> None:
    legacy = Legacy()
    receipts: list[TutorRoutingReceipt] = []
    result = decide(
        router(
            Judge("tool:study.write"),
            Model(RuntimeError("PRIVATE RAW PAYLOAD")),
            legacy,
            mode=FeatureMode.SHADOW,
            receipts=receipts,
        ),
        context(tools=(tool("study.write", TOPIC),)),
    )
    assert result == legacy.decision and legacy.calls == 1
    assert receipts[0].selected_tool_name == "study.write"
    assert "PRIVATE" not in repr(receipts)


def test_tool_shadow_receipt_records_selection_and_returns_legacy() -> None:
    legacy = Legacy()
    receipts: list[TutorRoutingReceipt] = []
    result = decide(
        router(
            Judge("tool:study.read"),
            Model(),
            legacy,
            mode=FeatureMode.SHADOW,
            receipts=receipts,
        ),
        context(tools=(tool("study.read"),)),
    )
    assert result == legacy.decision and legacy.calls == 1
    assert receipts[0].candidate_kind is TutorDecisionKind.INVOKE_TOOL
    assert receipts[0].selected_tool_name == "study.read"
    assert receipts[0].selected_capability_id is None
    assert receipts[0].candidate_validated and receipts[0].disagreement


def test_tool_generation_cancellation_never_enters_emergency_fallback() -> None:
    legacy = Legacy()
    with pytest.raises(RetryableTutorDecisionError, match="interrupted"):
        decide(
            router(
                Judge("tool:study.write"),
                Model(ModelError(ModelErrorCode.CANCELLED, "cancelled")),
                legacy,
            ),
            context(tools=(tool("study.write", TOPIC),)),
        )
    assert legacy.calls == 0


def test_existing_host_runner_executes_selected_tool_once_with_host_owned_authority() -> None:
    from types import SimpleNamespace

    from cardine.hosts import TutorHostLimits, TutorHostRunner, TutorHostRunStatus
    from study_agent.domain import CourseId, SessionId

    ctx = context(tools=(tool("study.write", TOPIC),))
    judge = Judge("tool:study.write", "assistant_message")
    model = Model({"topic": "valves"}, {"message": "Recorded valves."})
    legacy = Legacy()
    port = router(judge, model, legacy)

    class Assembler:
        def assemble(self, *args: object, **kwargs: object) -> TutorHostContext:
            return ctx

    class ToolGateway:
        def __init__(self) -> None:
            self.calls: list[tuple[str, JsonObject, CourseId, SessionId, str, int]] = []

        async def invoke(
            self,
            name: str,
            arguments: JsonObject,
            course: CourseId,
            session: SessionId,
            turn: str,
            snapshot_sequence: int,
        ) -> object:
            self.calls.append((name, arguments, course, session, turn, snapshot_sequence))
            return SimpleNamespace(error=None, value={"high_water_sequence": 9})

    tool_gateway = ToolGateway()
    runner = TutorHostRunner(
        port,
        None,
        None,
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        TutorHostLimits(2, 2, 1, 1_000),
        context_assembler=Assembler(),  # type: ignore[arg-type]
        tool_gateway=tool_gateway,
    )
    result = asyncio.run(runner.run(CourseId("course"), SessionId("session"), "turn-1", Token()))
    assert result.status is TutorHostRunStatus.ASSISTANT_MESSAGE
    assert tool_gateway.calls == [
        (
            "study.write",
            {"topic": "valves"},
            CourseId("course"),
            SessionId("session"),
            "turn-1",
            ctx.tutor_snapshot_sequence,
        )
    ]
    assert legacy.calls == 0 and len(model.requests) == 2
    assert result.presentation_receipt is not None
    assert result.presentation_receipt.observed_host_context_sequence == ctx.tutor_snapshot_sequence


@pytest.mark.parametrize("kind", ("explicit_topic", "latest_explanation", "selected_lesson",
                                   "conversation"))
def test_jev_flashcard_scope_is_fixed_before_selected_payload_generation(kind: str) -> None:
    from cardine.application.flashcard_scope import FlashcardScope, learner_fingerprint
    from study_agent.capabilities.builtin import PROPOSE_FLASHCARDS_MANIFEST

    utterance = "genera una flashcard su quesot"
    ctx = replace(context(capabilities=(capability(
        "propose_flashcards", PROPOSE_FLASHCARDS_MANIFEST.input_schema),)),
        tutor_snapshot={"timeline": ({"kind": "learner", "content": utterance,
                                      "interaction_id": "current-human"},)})
    scope = FlashcardScope(kind, "default", learner_fingerprint(utterance), "current-human")
    payload: JsonObject = {
        "query": "latest explanation" if kind == "latest_explanation" else "request label",
        "scope": scope.encode(), "language": "it",
                          "candidate_ceiling": 24, "continuation_summary_json": None}
    judge = Judge("capability:propose_flashcards", kind, "default", "supported")
    model = Model(payload)
    legacy = Legacy()
    port = RoutingTutorDecisionPort(judge, cast(ModelPort, model),
                                    policy(mode=FeatureMode.ON), legacy=legacy)
    decision = asyncio.run(port.decide(ctx, Token()))
    assert isinstance(decision, StartCapabilityDecision)
    assert FlashcardScope.parse(decision.inputs["scope"]) == scope
    assert [item.metadata["use_case"] for item in judge.requests] == [
        "route", "flashcard_scope", "flashcard_profile",
        *(("flashcard_topic_binding",) if kind != "latest_explanation" else ())]
    constraint = model.requests[0].structured_output
    assert constraint is not None
    props = cast(Mapping[str, JsonObject], constraint.schema["properties"])
    assert props["scope"]["enum"] == (scope.encode(),)
    assert legacy.calls == 0


@pytest.mark.parametrize("scope,profile", (("ambiguous", None), ("explicit_topic", "ambiguous")))
def test_jev_ambiguous_flashcard_request_asks_without_generation(
    scope: str, profile: str | None,
) -> None:
    from cardine.hosts.contracts import AskLearnerDecision
    from study_agent.capabilities.builtin import PROPOSE_FLASHCARDS_MANIFEST

    judge = Judge("capability:propose_flashcards", scope, *(() if profile is None else (profile,)))
    model = Model()
    legacy = Legacy()
    port = RoutingTutorDecisionPort(judge, cast(ModelPort, model),
                                    policy(mode=FeatureMode.ON), legacy=legacy)
    decision = asyncio.run(port.decide(context(capabilities=(capability(
        "propose_flashcards", PROPOSE_FLASHCARDS_MANIFEST.input_schema),)), Token()))
    assert isinstance(decision, AskLearnerDecision)
    assert model.requests == [] and legacy.calls == 0


@pytest.mark.parametrize("bad", ("incomplete", "duplicate", "unnormalized", "infinite", "weak"))
def test_flashcard_choice_distribution_failure_calls_emergency_once(bad: str) -> None:
    from study_agent.capabilities.builtin import PROPOSE_FLASHCARDS_MANIFEST

    class ScopeJudge(Judge):
        async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
            result = await super().judge(request)
            if request.metadata["use_case"] != "flashcard_scope":
                return result
            entries = result.probabilities
            if bad == "incomplete":
                object.__setattr__(result, "probabilities", entries[:-1])
            elif bad == "duplicate":
                object.__setattr__(result, "probabilities", (*entries[:-1], entries[0]))
            elif bad == "unnormalized":
                object.__setattr__(entries[0], "probability", 0.5)
            elif bad == "infinite":
                object.__setattr__(entries[0], "probability", float("inf"))
            else:
                object.__setattr__(result, "probabilities", tuple(ChoiceProbability(
                    option.key, 0.2) for option in request.options))
            return result

    judge = ScopeJudge("capability:propose_flashcards", "latest_explanation")
    model = Model()
    legacy = Legacy()
    decision = asyncio.run(router(judge, model, legacy).decide(context(capabilities=(capability(
        "propose_flashcards", PROPOSE_FLASHCARDS_MANIFEST.input_schema),)), Token()))
    assert isinstance(decision, AssistantMessageDecision)
    assert legacy.calls == 1 and model.requests == []


def test_cancelled_flashcard_scope_never_enters_emergency_fallback() -> None:
    from study_agent.capabilities.builtin import PROPOSE_FLASHCARDS_MANIFEST

    token = Token()
    class ScopeJudge(Judge):
        async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
            result = await super().judge(request)
            if request.metadata["use_case"] == "flashcard_scope":
                token.interrupted = True
            return result

    judge = ScopeJudge("capability:propose_flashcards", "latest_explanation")
    legacy = Legacy()
    model = Model()
    with pytest.raises(RetryableTutorDecisionError):
        asyncio.run(router(judge, model, legacy).decide(context(capabilities=(capability(
            "propose_flashcards", PROPOSE_FLASHCARDS_MANIFEST.input_schema),)), token))
    assert legacy.calls == 0 and model.requests == []


@pytest.mark.parametrize("bad", ("overlong", "scope_override", "invented_topic"))
def test_flashcard_payload_cannot_override_semantic_scope_or_invent_topic(bad: str) -> None:
    from cardine.application.flashcard_scope import FlashcardScope, learner_fingerprint
    from cardine.hosts.contracts import AskLearnerDecision
    from study_agent.capabilities.builtin import PROPOSE_FLASHCARDS_MANIFEST

    prompt = "genera una flashcard su mitosi"
    ctx = replace(context(capabilities=(capability(
        "propose_flashcards", PROPOSE_FLASHCARDS_MANIFEST.input_schema),)),
        tutor_snapshot={"timeline": ({"kind": "learner", "content": prompt,
                                      "interaction_id": "current-human"},)})
    fixed = FlashcardScope(
        "explicit_topic", "default", learner_fingerprint(prompt), "current-human")
    judge = Judge("capability:propose_flashcards", "explicit_topic", "default", "ambiguous")
    model = Model({"query": "x" * 513 if bad == "overlong" else "inventedtopic",
        "scope": "free-form" if bad == "scope_override" else fixed.encode(),
        "language": "it", "candidate_ceiling": 24, "continuation_summary_json": None})
    legacy = Legacy()
    decision = asyncio.run(router(judge, model, legacy).decide(ctx, Token()))
    assert legacy.calls == (0 if bad == "invented_topic" else 1)
    assert isinstance(decision, AskLearnerDecision if bad == "invented_topic"
                      else AssistantMessageDecision)
    assert len(model.requests) == 1
