"""Bind closed semantic profile choices to registered pedagogical receipts."""
from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from study_agent.domain import InteractionId, PrincipalKind
from study_agent.pedagogy import (
    HYBRID_MACRO_DETAIL_V1,
    MORPHOLOGY_FIRST_ANATOMY_V1,
    PedagogicalProfileRef,
    ProfileSelectionBasis,
    ProfileSelectionMode,
    ProfileSelectionReceipt,
    ProfileSelectorKind,
)


@dataclass(frozen=True, slots=True)
class FlashcardProfileSelectionDecision:
    profile: PedagogicalProfileRef
    mode: ProfileSelectionMode
    evidence: tuple[str, ...]
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        if self.profile not in (HYBRID_MACRO_DETAIL_V1, MORPHOLOGY_FIRST_ANATOMY_V1):
            raise ValueError("unsupported flashcard profile")
        if not isinstance(self.mode, ProfileSelectionMode):
            raise TypeError("flashcard profile mode is invalid")
        if self.mode is ProfileSelectionMode.DEFAULT and self.profile != HYBRID_MACRO_DETAIL_V1:
            raise ValueError("the default profile must be hybrid")
        if not self.evidence or len(self.evidence) > 8 or any(not item for item in self.evidence):
            raise ValueError("flashcard profile evidence is invalid")
        if (len(self.evidence_fingerprint) != 64
                or any(c not in "0123456789abcdef" for c in self.evidence_fingerprint)):
            raise ValueError("flashcard profile fingerprint is invalid")

    def receipt(self, interaction_id: InteractionId) -> ProfileSelectionReceipt:
        if self.mode is ProfileSelectionMode.DEFAULT:
            return ProfileSelectionReceipt(self.profile, self.mode, ProfileSelectorKind.HOST,
                                           PrincipalKind.SERVICE, ProfileSelectionBasis())
        if not isinstance(interaction_id, InteractionId):
            raise TypeError("explicit profile selection requires InteractionId")
        return ProfileSelectionReceipt(self.profile, self.mode, ProfileSelectorKind.HUMAN,
                                       PrincipalKind.HUMAN,
                                       ProfileSelectionBasis(interaction_id=interaction_id))


def semantic_flashcard_profile(profile: str) -> FlashcardProfileSelectionDecision:
    if profile not in {"default", "hybrid", "morphology"}:
        raise ValueError("unsupported semantic flashcard profile")
    selected = MORPHOLOGY_FIRST_ANATOMY_V1 if profile == "morphology" else HYBRID_MACRO_DETAIL_V1
    evidence = (f"semantic-profile:{profile}",)
    fingerprint = sha256(b"cardine-flashcard-profile-route@1\0" + json.dumps(
        {"profile": selected.identity, "evidence": evidence},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return FlashcardProfileSelectionDecision(selected,
        (ProfileSelectionMode.DEFAULT if profile == "default"
         else ProfileSelectionMode.EXPLICIT_REQUEST),
        evidence, fingerprint)
