import pytest

from cardine.materials.validation import MaterialValidationError, validate_markdown


@pytest.mark.parametrize("marker", ["forse", "probabilmente", "incerto", "incerta", "potrebbe"])
def test_italian_uncertainty_cannot_be_removed(marker: str) -> None:
    source = f"Il meccanismo è {marker} collegato alla glicolisi."
    with pytest.raises(MaterialValidationError, match="uncertainty"):
        validate_markdown(
            "# Lezione\nIl meccanismo è collegato alla glicolisi.",
            source_text=source,
            stage="complete",
            max_characters=1000,
        )
    accepted = validate_markdown(
        f"# Lezione\n{source}", source_text=source, stage="complete", max_characters=1000
    )
    assert marker in accepted.text


@pytest.mark.parametrize("stage", ["complete", "study"])
def test_all_qualifier_commitments_must_remain(stage: str) -> None:
    source = "Forse regola la glicolisi. Probabilmente agisce sulla membrana."
    with pytest.raises(MaterialValidationError, match="uncertainty"):
        validate_markdown(
            "# Lezione\nForse regola la glicolisi. Agisce sulla membrana.",
            source_text=source,
            stage=stage,
            max_characters=1000,
        )


def test_capitalized_month_is_not_an_uncertainty_qualifier() -> None:
    accepted = validate_markdown(
        "# Lezione\nNel 2024 si studia la glicolisi.",
        source_text="Recorded in May 2024. Si studia la glicolisi.",
        stage="complete",
        max_characters=1000,
    )
    assert "glicolisi" in accepted.text


def test_every_teacher_emphasis_commitment_must_remain() -> None:
    source = "Important: remember this fondamentale mechanism."
    with pytest.raises(MaterialValidationError, match="emphasis"):
        validate_markdown(
            "# Lesson\nImportant mechanism.",
            source_text=source,
            stage="complete",
            max_characters=1000,
        )

    accepted = validate_markdown(
        "# Lesson\nImportant: remember this fondamentale mechanism.",
        source_text=source,
        stage="complete",
        max_characters=1000,
    )
    assert "fondamentale" in accepted.text


@pytest.mark.parametrize("limitation", ["incertezza", "ambigua", "limitazione"])
def test_italian_uncertainty_limitation_is_accepted_and_fail_closed(
    limitation: str,
) -> None:
    source = "La causa è ambigua."
    accepted = validate_markdown(
        "# Lezione\nLa causa è ambigua.",
        source_text=source,
        stage="complete",
        max_characters=1000,
        limitations=(f"Il testo conserva l'{limitation} dichiarata.",),
    )
    assert accepted.limitations == (f"Il testo conserva l'{limitation} dichiarata.",)

    with pytest.raises(MaterialValidationError, match="not truthful about uncertainty"):
        validate_markdown(
            "# Lezione\nLa causa è ambigua.",
            source_text=source,
            stage="complete",
            max_characters=1000,
            limitations=("Il testo è completo.",),
        )


@pytest.mark.parametrize(
    "source,output,marker",
    [
        ("Maybe A. Maybe B.", "# Lesson\nMaybe A. B.", "uncertainty"),
        ("A uses 5 mg. B uses 5 mg.", "# Lesson\nA uses 5 mg. B uses a dose.", "numeric"),
        ("A is important. B is important.", "# Lesson\nA is important. B exists.", "emphasis"),
    ],
)
def test_repeated_claim_markers_cannot_lose_an_occurrence(
    source: str, output: str, marker: str
) -> None:
    with pytest.raises(MaterialValidationError, match=marker):
        validate_markdown(output, source_text=source, stage="complete", max_characters=1000)
    assert validate_markdown(
        "# Lesson\n" + source, source_text=source, stage="complete", max_characters=1000
    ).text.endswith(source)
