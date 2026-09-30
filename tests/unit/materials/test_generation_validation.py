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
