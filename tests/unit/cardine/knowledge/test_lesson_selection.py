from __future__ import annotations

from hashlib import sha256

import pytest

from cardine.knowledge import (
    LessonChunk,
    LessonSearchResult,
    LessonSelectionError,
    LessonSelectionService,
    LessonSource,
    SearchDisposition,
)


class _Evidence:
    def __init__(self, chunks: tuple[LessonChunk, ...] = ()) -> None:
        self.chunks = chunks

    def search(self, source: LessonSource, query: str) -> tuple[LessonChunk, ...]:
        del source, query
        return self.chunks


def _source(text: str, *, kind: str = "markdown", revision: str = "revision-1") -> LessonSource:
    return LessonSource(
        "course-1",
        "source-1",
        revision,
        "Lezioni",
        kind,
        text,
        sha256(text.encode()).hexdigest(),
        "a" * 64,
    )


def test_markdown_search_ignores_fences_and_pins_only_selected_section() -> None:
    text = (
        "# Lezione 1 ###\n"
        "Valvola mitrale.\n"
        "```md\n# Lezione 1 falsa\n```\n"
        "## Dettaglio\nCordae.\n"
        "# Lezione 2\nValvola aortica.\n"
    )
    source = _source(text)
    service = LessonSelectionService(_Evidence())

    result = service.search("course-1", "Lezione 1", (source,))
    assert result.disposition is SearchDisposition.UNIQUE
    pin = service.select(result.candidates[0].candidate_id, result)
    assert text[pin.start_offset : pin.end_offset].startswith("# Lezione 1")
    assert "Lezione 2" not in text[pin.start_offset : pin.end_offset]
    assert service.validate_pin(pin, (source,)) is source

    with pytest.raises(LessonSelectionError, match="stale"):
        service.validate_pin(pin, (_source(text + "x", revision="revision-2"),))


def test_ambiguity_requires_explicit_choice_and_plain_text_uses_exact_evidence() -> None:
    first = _source("# Lezione 1\nA\n")
    second_text = "Lezione uno: contenuto canonico."
    chunk = LessonChunk(0, len(second_text), second_text)
    second = LessonSource(
        "course-1",
        "source-2",
        "revision-2",
        "Lezione 1",
        "text",
        second_text,
        sha256(second_text.encode()).hexdigest(),
        "b" * 64,
        (chunk,),
    )
    service = LessonSelectionService(_Evidence((chunk,)))
    result = service.search("course-1", "Lezione 1", (first, second))
    assert result.disposition is SearchDisposition.AMBIGUOUS
    with pytest.raises(LessonSelectionError):
        service.select("missing", result)

    forged = LessonChunk(0, 8, "sbagliato")
    with pytest.raises(LessonSelectionError, match="canonical"):
        LessonSelectionService(_Evidence((forged,))).search("course-1", "Lezione 1", (second,))


def test_search_result_rejects_false_unique_disposition() -> None:
    with pytest.raises(ValueError, match="cardinality"):
        LessonSearchResult(SearchDisposition.UNIQUE, ())


@pytest.mark.parametrize("heading", ("L01_04/03/2025", "L_01", "L-01"))
def test_natural_lesson_title_matches_converted_heading_aliases(heading: str) -> None:
    text = f"# {heading}\nContenuto uno.\n# L02\nContenuto due.\n"
    result = LessonSelectionService(_Evidence()).search(
        "course-1", "Lezione 1", (_source(text),)
    )

    assert result.disposition is SearchDisposition.UNIQUE
    pin = LessonSelectionService(_Evidence()).select(
        result.candidates[0].candidate_id, result
    )
    assert pin.section_title == heading
    assert "Contenuto due" not in text[pin.start_offset : pin.end_offset]
