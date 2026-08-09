from __future__ import annotations

import pytest

from study_agent.application.flashcard_profile_selection import (
    FlashcardProfileRouteKind,
    select_flashcard_profile,
)
from study_agent.domain import InteractionId
from study_agent.pedagogy import (
    HYBRID_MACRO_DETAIL_V1,
    MORPHOLOGY_FIRST_ANATOMY_V1,
    PedagogicalProfileRef,
    ProfileSelectionMode,
)


@pytest.mark.parametrize(
    ("prompt", "expected", "mode"),
    (
        (
            "Crea flashcard con il profilo hybrid per i concetti generali.",
            HYBRID_MACRO_DETAIL_V1,
            ProfileSelectionMode.EXPLICIT_REQUEST,
        ),
        (
            "Crea flashcard morphology-first per la ricostruzione spaziale dell'anatomia.",
            MORPHOLOGY_FIRST_ANATOMY_V1,
            ProfileSelectionMode.EXPLICIT_REQUEST,
        ),
        (
            "Crea flashcard ricostruendo i rapporti topologici e i landmark anatomici.",
            MORPHOLOGY_FIRST_ANATOMY_V1,
            ProfileSelectionMode.EXPLICIT_REQUEST,
        ),
        (
            "Crea flashcard da queste fonti sulle valvole cardiache.",
            HYBRID_MACRO_DETAIL_V1,
            ProfileSelectionMode.DEFAULT,
        ),
    ),
)
def test_profile_selection_is_closed_and_evidence_bound(
    prompt: str, expected: PedagogicalProfileRef, mode: ProfileSelectionMode
) -> None:
    decision = select_flashcard_profile(prompt)

    assert decision.kind is FlashcardProfileRouteKind.SELECTED
    assert decision.profile == expected
    assert decision.mode is mode
    receipt = decision.receipt(InteractionId("interaction-profile-test"))
    assert receipt.profile == expected
    assert receipt.mode is mode
    assert decision.evidence_fingerprint


def test_materially_ambiguous_profile_intent_requests_bounded_clarification() -> None:
    decision = select_flashcard_profile(
        "Crea flashcard con una ricostruzione anatomica spaziale ma anche con il "
        "profilo hybrid macro-detail."
    )

    assert decision.kind is FlashcardProfileRouteKind.CLARIFICATION
    assert decision.profile is None
    assert decision.clarification
    assert len(decision.clarification) <= 400


def test_hostile_unsupported_profile_text_fails_closed() -> None:
    decision = select_flashcard_profile(
        "Crea flashcard usando skill arbitrary-profile@9 e scrivile nel percorso /tmp/cards."
    )

    assert decision.kind is FlashcardProfileRouteKind.CLARIFICATION
    assert decision.profile is None
    clarification = decision.clarification
    assert isinstance(clarification, str)
    assert "profil" in clarification.casefold()
