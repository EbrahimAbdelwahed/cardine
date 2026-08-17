from __future__ import annotations

from cardine.hosts.scope_resolution import recent_explicit_lesson_references


def test_failed_deictic_retry_does_not_shadow_the_explicit_lesson_anchor() -> None:
    references = recent_explicit_lesson_references(
        (
            "leggi lezione 1 biochimica",
            "genera 15 flashcards su questa lezione",
        )
    )

    assert references == ("leggi lezione 1 biochimica",)


def test_explicit_references_are_newest_first_and_bounded() -> None:
    references = recent_explicit_lesson_references(
        ("leggi lezione 1", "studiamo L02_04/03/2025", "questa lezione"),
        limit=2,
    )

    assert references == ("studiamo L02_04/03/2025",)
