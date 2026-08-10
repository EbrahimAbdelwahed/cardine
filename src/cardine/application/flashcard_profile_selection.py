"""Closed, host-owned routing for the two supported flashcard profiles."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
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

_MORPHOLOGY_TERMS = (
    "morphology",
    "morphological",
    "morfologia",
    "morfologico",
    "morfologica",
    "anatomy",
    "anatomical",
    "anatomia",
    "anatomico",
    "anatomica",
    "spatial",
    "spaziale",
    "topological",
    "topology",
    "topologico",
    "topologia",
    "relations",
    "rapporti",
    "decorso",
    "landmark",
    "landmarks",
    "ricostruzione",
    "reconstruction",
)
_HYBRID_TERMS = (
    "hybrid",
    "ibrido",
    "ibrida",
    "macro-detail",
    "macro detail",
    "macro-dettaglio",
    "macro dettaglio",
)
_UNSUPPORTED_CONTROL_TEXT = re.compile(
    r"(?:skill|abilit[aà]|filesystem|file system|percorso|path|directory|"
    r"profile\s*(?:id|=|:)|profilo\s*(?:id|=|:))",
    re.IGNORECASE,
)
_UNSUPPORTED_PROFILE = re.compile(
    r"(?:skill|profile|profilo)\s*(?:id|=|:)?\s*['\"]?"
    r"([a-z0-9][a-z0-9_-]*(?:@[0-9]+)?)",
    re.IGNORECASE,
)
class FlashcardProfileRouteKind(StrEnum):
    SELECTED = "selected"
    CLARIFICATION = "clarification"


@dataclass(frozen=True, slots=True)
class FlashcardProfileSelectionDecision:
    """A schema-like closed route result owned by the trusted host."""

    kind: FlashcardProfileRouteKind
    profile: PedagogicalProfileRef | None
    mode: ProfileSelectionMode | None
    evidence: tuple[str, ...]
    evidence_fingerprint: str
    clarification: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FlashcardProfileRouteKind):
            raise TypeError("flashcard route kind is invalid")
        if self.kind is FlashcardProfileRouteKind.SELECTED:
            if self.profile not in (HYBRID_MACRO_DETAIL_V1, MORPHOLOGY_FIRST_ANATOMY_V1):
                raise ValueError("flashcard route selected an unsupported profile")
            if not isinstance(self.mode, ProfileSelectionMode):
                raise TypeError("selected flashcard route requires a selection mode")
            if self.clarification is not None:
                raise ValueError("selected flashcard route cannot carry clarification")
        else:
            if self.profile is not None or self.mode is not None:
                raise ValueError("clarification route cannot select a profile")
            if not isinstance(self.clarification, str) or not self.clarification.strip():
                raise ValueError("clarification route requires a bounded question")
            if len(self.clarification) > 400:
                raise ValueError("clarification route question is too long")
        evidence = tuple(self.evidence)
        if not evidence or len(evidence) > 8 or any(not item for item in evidence):
            raise ValueError("flashcard route evidence is invalid")
        object.__setattr__(self, "evidence", evidence)
        if (
            len(self.evidence_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in self.evidence_fingerprint)
        ):
            raise ValueError("flashcard route evidence fingerprint is invalid")

    def receipt(self, interaction_id: InteractionId) -> ProfileSelectionReceipt:
        """Materialize the existing durable receipt from a learner interaction."""

        if self.kind is not FlashcardProfileRouteKind.SELECTED:
            raise ValueError("a clarification route has no profile receipt")
        if self.mode is ProfileSelectionMode.DEFAULT:
            return ProfileSelectionReceipt(
                HYBRID_MACRO_DETAIL_V1,
                ProfileSelectionMode.DEFAULT,
                ProfileSelectorKind.HOST,
                PrincipalKind.SERVICE,
                ProfileSelectionBasis(),
            )
        if not isinstance(interaction_id, InteractionId):
            raise TypeError("explicit flashcard selection requires InteractionId")
        profile = self.profile
        mode = self.mode
        if not isinstance(profile, PedagogicalProfileRef) or not isinstance(
            mode, ProfileSelectionMode
        ):
            raise TypeError("selected flashcard route is missing its profile or mode")
        return ProfileSelectionReceipt(
            profile,
            mode,
            ProfileSelectorKind.HUMAN,
            PrincipalKind.HUMAN,
            ProfileSelectionBasis(interaction_id=interaction_id),
        )


def select_flashcard_profile(prompt: str) -> FlashcardProfileSelectionDecision:
    """Select only a registered profile, or ask a bounded clarification."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("flashcard profile selection requires learner text")
    normalized = prompt.casefold()
    unsupported = _unsupported_profile_value(normalized)
    morphology = _signals(normalized, _MORPHOLOGY_TERMS)
    if re.search(
        r"\b(?:anatom(?:y|ia|ical|ico|ica)|morfolog\w*)\b"
        r".{0,48}\b(?:course|profil\w*)\b",
        normalized,
    ):
        morphology = (*morphology, "anatomy-profile")
    hybrid = _signals(normalized, _HYBRID_TERMS)
    evidence = tuple(sorted(set(morphology + hybrid)))
    if unsupported is not None:
        return _clarification(
            (*evidence, f"unsupported-profile:{unsupported}"),
            "Posso usare solo i profili hybrid o morphology-first. "
            "Quale preferisci?",
        )
    if morphology and hybrid:
        return _clarification(
            evidence,
            "Preferisci il profilo hybrid per i concetti o morphology-first "
            "per la ricostruzione anatomica?",
        )
    if morphology:
        return _selected(MORPHOLOGY_FIRST_ANATOMY_V1, evidence, explicit=True)
    if hybrid:
        return _selected(HYBRID_MACRO_DETAIL_V1, evidence, explicit=True)
    return _selected(HYBRID_MACRO_DETAIL_V1, ("default-general",), explicit=False)


def _selected(
    profile: PedagogicalProfileRef, evidence: tuple[str, ...], *, explicit: bool
) -> FlashcardProfileSelectionDecision:
    return FlashcardProfileSelectionDecision(
        FlashcardProfileRouteKind.SELECTED,
        profile,
        ProfileSelectionMode.EXPLICIT_REQUEST if explicit else ProfileSelectionMode.DEFAULT,
        evidence or ("default-general",),
        _fingerprint(FlashcardProfileRouteKind.SELECTED, profile.identity, evidence),
    )


def _clarification(
    evidence: tuple[str, ...], question: str
) -> FlashcardProfileSelectionDecision:
    normalized = evidence or ("ambiguous",)
    return FlashcardProfileSelectionDecision(
        FlashcardProfileRouteKind.CLARIFICATION,
        None,
        None,
        normalized,
        _fingerprint(FlashcardProfileRouteKind.CLARIFICATION, None, normalized),
        question,
    )


def _signals(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        term
        for term in terms
        if re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", text)
    )


def _unsupported_profile_value(text: str) -> str | None:
    if not _UNSUPPORTED_CONTROL_TEXT.search(text):
        return None
    for match in _UNSUPPORTED_PROFILE.finditer(text):
        value = match.group(1).casefold()
        if value not in {
            HYBRID_MACRO_DETAIL_V1.id.value,
            MORPHOLOGY_FIRST_ANATOMY_V1.id.value,
            HYBRID_MACRO_DETAIL_V1.identity,
            MORPHOLOGY_FIRST_ANATOMY_V1.identity,
        }:
            return value
    return "unsupported-control-input"


def _fingerprint(
    kind: FlashcardProfileRouteKind, profile: str | None, evidence: tuple[str, ...]
) -> str:
    payload = json.dumps(
        {"kind": kind.value, "profile": profile, "evidence": tuple(sorted(evidence))},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(b"cardine-flashcard-profile-route@1\0" + payload).hexdigest()


__all__ = [
    "FlashcardProfileRouteKind",
    "FlashcardProfileSelectionDecision",
    "select_flashcard_profile",
]
