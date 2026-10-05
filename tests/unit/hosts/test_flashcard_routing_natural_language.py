"""Jev request behavior without a second language interpreter."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from cardine.hosts import (
    StartCapabilityDecision,
)


@pytest.mark.parametrize("action", (
    "Fammi", "Dammi", "Preparami", "Produci", "Costruisci", "Proponi", "Fai",
    "Make", "Making", "Prepare", "Produce", "Build", "Draft", "Give me",
))
def test_jev_owns_topic_query_for_natural_requests(action: str) -> None:
    from cardine.application.flashcard_scope import FlashcardScope, learner_fingerprint
    from study_agent.capabilities.builtin import PROPOSE_FLASHCARDS_MANIFEST
    from tests.unit.hosts.test_routing import Judge, Model, capability, router
    from tests.unit.hosts.test_routing import context as routing_context

    prompt = f"{action} una flashcard sulla mitosi please per favore"
    ctx = replace(routing_context(capabilities=(capability(
        "propose_flashcards", PROPOSE_FLASHCARDS_MANIFEST.input_schema),)),
        tutor_snapshot={"timeline": ({"kind": "learner", "content": prompt,
                                      "interaction_id": "current-request"},)})
    scope = FlashcardScope("explicit_topic", "default", learner_fingerprint(prompt),
                          "current-request")
    judge = Judge("capability:propose_flashcards", "explicit_topic", "default", "supported")
    model = Model({"query": "mitosi", "scope": scope.encode(), "language": "it",
                   "candidate_ceiling": 24, "continuation_summary_json": None})
    decision = asyncio.run(router(judge, model).decide(ctx, _Token()))
    assert isinstance(decision, StartCapabilityDecision)
    assert decision.inputs["query"] == "mitosi"
    assert FlashcardScope.parse(decision.inputs["scope"]).kind == "explicit_topic"



class _Token:
    def is_interrupted(self) -> bool:
        return False
