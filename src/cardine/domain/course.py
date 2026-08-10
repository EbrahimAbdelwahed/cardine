from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ._validation import require_text
from .identifiers import CourseId


def _owned_typed_tuple[T](value: Sequence[T], item_type: type[T], name: str) -> tuple[T, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a sequence")
    owned = tuple(value)
    if not all(isinstance(item, item_type) for item in owned):
        raise TypeError(f"{name} items must be {item_type.__name__} instances")
    return owned


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    allowed_roles: tuple[str, ...] = ()
    minimum_trust_level: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_roles",
            _owned_typed_tuple(self.allowed_roles, str, "allowed_roles"),
        )
        if isinstance(self.minimum_trust_level, bool) or not isinstance(
            self.minimum_trust_level, int
        ):
            raise TypeError("minimum_trust_level must be an integer")
        if not 0 <= self.minimum_trust_level <= 100:
            raise ValueError("minimum_trust_level must be between 0 and 100")
        if len(set(self.allowed_roles)) != len(self.allowed_roles):
            raise ValueError("allowed_roles must not contain duplicates")
        for role in self.allowed_roles:
            require_text(role, "allowed_roles item")


@dataclass(frozen=True, slots=True)
class TerminologyEntry:
    concept: str
    preferred_term: str

    def __post_init__(self) -> None:
        if not isinstance(self.concept, str):
            raise TypeError("concept must be a string")
        if not isinstance(self.preferred_term, str):
            raise TypeError("preferred_term must be a string")
        require_text(self.concept, "concept")
        require_text(self.preferred_term, "preferred_term")


@dataclass(frozen=True, slots=True)
class TerminologyPolicy:
    entries: tuple[TerminologyEntry, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "entries",
            _owned_typed_tuple(self.entries, TerminologyEntry, "entries"),
        )
        concepts = [entry.concept for entry in self.entries]
        if len(set(concepts)) != len(concepts):
            raise ValueError("terminology concepts must be unique")


@dataclass(frozen=True, slots=True)
class CourseProfile:
    id: CourseId
    title: str
    language: str
    exam_date: date | None = None
    assessment_styles: tuple[str, ...] = ()
    learning_goals: tuple[str, ...] = ()
    source_policy: SourcePolicy = SourcePolicy()
    terminology_policy: TerminologyPolicy = TerminologyPolicy()

    def __post_init__(self) -> None:
        if not isinstance(self.id, CourseId):
            raise TypeError("id must be a CourseId")
        if self.exam_date is not None and type(self.exam_date) is not date:
            raise TypeError("exam_date must be a date or None")
        if not isinstance(self.source_policy, SourcePolicy):
            raise TypeError("source_policy must be a SourcePolicy")
        if not isinstance(self.terminology_policy, TerminologyPolicy):
            raise TypeError("terminology_policy must be a TerminologyPolicy")
        if not isinstance(self.title, str):
            raise TypeError("title must be a string")
        if not isinstance(self.language, str):
            raise TypeError("language must be a string")
        object.__setattr__(
            self,
            "assessment_styles",
            _owned_typed_tuple(self.assessment_styles, str, "assessment_styles"),
        )
        object.__setattr__(
            self,
            "learning_goals",
            _owned_typed_tuple(self.learning_goals, str, "learning_goals"),
        )
        require_text(self.title, "title")
        require_text(self.language, "language")
        if not self.learning_goals:
            raise ValueError("learning_goals must contain at least one goal")
        for name, values in (
            ("assessment_styles", self.assessment_styles),
            ("learning_goals", self.learning_goals),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
            for value in values:
                require_text(value, f"{name} item")
