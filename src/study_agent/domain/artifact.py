"""Inward study-artifact vocabulary and provenance leaves."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from ._validation import require_text
from .identifiers import BlobId


class StudyArtifactKind(StrEnum):
    FLASHCARD = "flashcard"
    ASSESSMENT_ITEM = "assessment_item"
    EXAM_BLUEPRINT = "exam_blueprint"
    STUDY_BRIEF = "study_brief"
    LESSON_MATERIAL = "lesson_material"


class LessonMaterialVariant(StrEnum):
    COMPLETE = "complete"
    STUDY = "study"


class ArtifactRevisionStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ArtifactDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class RetrievalForm(StrEnum):
    DIRECT_RECALL = "direct_recall"
    CONTEXTUAL_GAP = "contextual_gap"


class HybridFlashcardRole(StrEnum):
    OVERVIEW = "overview"
    SECTION = "section"
    DETAIL = "detail"


class MorphologyFlashcardRole(StrEnum):
    MACRO_RECONSTRUCTION = "macro_reconstruction"
    ATOMIC_DISCRIMINATION = "atomic_discrimination"


class MorphologyFamily(StrEnum):
    COMPONENTS = "components"
    TOPOLOGY = "topology"
    RELATIONS = "relations"
    COURSE = "course"
    PROFILES = "profiles"
    LANDMARKS = "landmarks"


class MorphologyCognitiveFunction(StrEnum):
    RECONSTRUCT = "reconstruct"
    LOCALIZE = "localize"
    RELATE = "relate"
    DISCRIMINATE = "discriminate"


class AssessmentFormat(StrEnum):
    FREE_RESPONSE = "free_response"
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"


@dataclass(frozen=True, slots=True)
class ArtifactReadDependency:
    """Closed inward copy of one verified technical read dependency."""

    kind: str
    id: str
    version: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.kind, "read dependency kind"),
            (self.id, "read dependency id"),
            (self.version, "read dependency version"),
        ):
            require_text(value, name)
        if not self.kind.replace("_", "").replace("-", "").isalnum():
            raise ValueError("read dependency kind must be portable")


@dataclass(frozen=True, slots=True)
class VerifiedMediaRef:
    blob_id: BlobId
    sha256: str
    source_commitment_index: int
    verifier_id: str
    verifier_version: str
    verifier_fingerprint: str
    alt_text: str

    def __post_init__(self) -> None:
        if not isinstance(self.blob_id, BlobId):
            raise TypeError("verified media blob_id must be BlobId")
        if "." in str(self.blob_id) or "/" in str(self.blob_id) or "\\" in str(self.blob_id):
            raise ValueError("verified media blob identity cannot be a filename or path")
        _require_sha256(self.sha256, "verified media sha256")
        if str(self.blob_id) != f"sha256:{self.sha256}":
            raise ValueError("verified media blob identity must match its sha256 digest")
        if type(self.source_commitment_index) is not int or self.source_commitment_index < 0:
            raise ValueError("source_commitment_index must be non-negative")
        for value, name in (
            (self.verifier_id, "verifier_id"),
            (self.verifier_version, "verifier_version"),
            (self.alt_text, "alt_text"),
        ):
            require_text(value, name)
        if re.fullmatch(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*", self.verifier_id) is None:
            raise ValueError("verifier_id must be a portable lowercase identifier")
        if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", self.verifier_version) is None:
            raise ValueError("verifier_version must be a portable version")
        _require_sha256(self.verifier_fingerprint, "verifier_fingerprint")
        receipt_values = " ".join(
            (self.verifier_id, self.verifier_version, str(self.blob_id))
        ).lower()
        if any(
            token in receipt_values
            for token in (
                "access_token",
                "api_key",
                "apikey",
                "bearer ",
                "password",
                "secret",
                "sk-live-",
                "sk_test_",
                "token=",
            )
        ):
            raise ValueError("verified media cannot contain secret-shaped values")
        if "<" in self.alt_text or ">" in self.alt_text:
            raise ValueError("verified media alt text cannot contain HTML")


def require_sha256(value: str, name: str) -> None:
    _require_sha256(value, name)


def _require_sha256(value: str, name: str) -> None:
    require_text(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 fingerprint")


__all__ = [
    "ArtifactDecision",
    "ArtifactReadDependency",
    "ArtifactRevisionStatus",
    "AssessmentFormat",
    "HybridFlashcardRole",
    "LessonMaterialVariant",
    "MorphologyCognitiveFunction",
    "MorphologyFamily",
    "MorphologyFlashcardRole",
    "RetrievalForm",
    "StudyArtifactKind",
    "VerifiedMediaRef",
]
