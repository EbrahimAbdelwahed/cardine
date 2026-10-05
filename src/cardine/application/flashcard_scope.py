"""Closed semantic flashcard request, serialized as exact structured scope JSON.

The host binds the learner fingerprint; no model-selected canonical identifier
is accepted. Conversation references describe context, never source evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

SCOPE_DESCRIPTIONS = {
    "explicit_topic": "The current request names an explicit topic; keep it authoritative",
    "latest_explanation": "Reference to the latest explanation, including spelling errors",
    "selected_lesson": "The selected lesson or an explicit lesson named by the current request",
    "conversation": "Topics discussed in the conversation; context only, not evidence",
    "ambiguous": "Missing or ambiguous scope; ask the learner, do not guess a topic",
}
PROFILE_DESCRIPTIONS = {
    "default": "No explicit profile requested; use the default hybrid profile",
    "hybrid": "The learner explicitly requests the registered hybrid profile",
    "morphology": "The learner explicitly requests anatomy or morphological reconstruction",
    "ambiguous": "Conflicting or unsupported profile request; ask the learner",
}


def learner_fingerprint(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FlashcardScope:
    kind: str
    profile: str
    learner_fingerprint: str
    learner_interaction_id: str
    version: str = "jev-flashcard-scope@1"

    def __post_init__(self) -> None:
        if (not isinstance(self.learner_interaction_id, str)
                or not self.learner_interaction_id.strip()
                or len(self.learner_interaction_id) > 128):
            raise ValueError("flashcard learner interaction is invalid")
        if self.version != "jev-flashcard-scope@1":
            raise ValueError("unsupported flashcard scope contract")
        if self.kind not in SCOPE_DESCRIPTIONS or self.kind == "ambiguous":
            raise ValueError("flashcard scope is ambiguous or invalid")
        if self.profile not in PROFILE_DESCRIPTIONS or self.profile == "ambiguous":
            raise ValueError("flashcard profile is ambiguous or invalid")
        if (len(self.learner_fingerprint) != 64
                or any(c not in "0123456789abcdef" for c in self.learner_fingerprint)):
            raise ValueError("flashcard learner fingerprint is invalid")

    def encode(self) -> str:
        return json.dumps({"version": self.version, "kind": self.kind,
                           "profile": self.profile,
                           "learner_fingerprint": self.learner_fingerprint,
                           "learner_interaction_id": self.learner_interaction_id}, sort_keys=True)

    @classmethod
    def parse(cls, raw: object) -> FlashcardScope:
        if not isinstance(raw, str) or len(raw) > 1000:
            raise ValueError("structured flashcard scope is missing")
        try:
            value = json.loads(raw)
        except (ValueError, TypeError) as error:
            raise ValueError("structured flashcard scope is invalid") from error
        if (not isinstance(value, dict)
                or set(value) != {"version", "kind", "profile",
                                  "learner_fingerprint", "learner_interaction_id"}
                or any(not isinstance(value[k], str)
                       for k in ("version", "kind", "profile",
                                 "learner_fingerprint", "learner_interaction_id"))):
            raise ValueError("structured flashcard scope is not exact")
        return cls(**value)
