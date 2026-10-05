from __future__ import annotations

import pytest

from cardine.documents.typst_notes import (
    TypstRenderer,
    TypstUnavailableError,
    inline,
    markdown_to_typst,
    notes_document,
    typst_string,
)


def test_text_is_always_a_string_literal_never_markup() -> None:
    hostile = '#set text(red) $x$ @ref <tag> [x] // c \\ "q"'
    converted = markdown_to_typst(hostile)
    assert converted == "#" + typst_string(hostile)
    assert typst_string('a"b\\c\nd') == '"a\\"b\\\\c\\nd"'


def test_inline_emphasis_code_and_links() -> None:
    assert inline("a **b** *c* `d`") == (
        '#"a "#strong[#"b"]#" "#emph[#"c"]#" "#raw("d")'
    )
    assert inline("[sito](https://example.org)") == '#link("https://example.org")[#"sito"]'
    # Only web and mail links become links; anything else stays plain text.
    assert inline("[x](javascript:alert(1))") == '#"x"#")"'


def test_blocks_headings_lists_quotes_tables_code_and_rules() -> None:
    markdown = "\n".join(
        [
            "## Titolo",
            "",
            "1. uno",
            "   - annidato",
            "2. due",
            "",
            "> citazione",
            "",
            "| A | B |",
            "|---|---|",
            "| 1 | 2 |",
            "",
            "```python",
            "x = 1",
            "```",
            "",
            "---",
        ]
    )
    converted = markdown_to_typst(markdown)
    assert '#heading(level: 2)[#"Titolo"]' in converted
    nested = '[#"uno"\n#list(tight: true, [#"annidato"])], [#"due"]'
    assert f"#enum(tight: true, start: 1, {nested})" in converted
    assert '#note-quote[#"citazione"]' in converted
    header = 'table.header([#strong[#"A"]], [#strong[#"B"]])'
    assert f'#table(columns: 2, {header}, [#"1"], [#"2"])' in converted
    assert '#raw(block: true, lang: "python", "x = 1")' in converted
    assert "#line(length: 100%, stroke: 0.5pt + rule-color)" in converted


def test_the_document_template_carries_title_and_subtitle_as_literals() -> None:
    source = notes_document(title='Ti"tolo', subtitle="Corso · data", markdown="Testo")
    assert '#set document(title: "Ti\\"tolo", author: "Cardine")' in source
    assert '[#"Corso · data"]' in source
    assert source.rstrip().endswith('#"Testo"')


def test_a_missing_compiler_is_reported_not_crashed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARDINE_TYPST_BIN", "/nonexistent/typst")
    renderer = TypstRenderer()
    assert renderer.available() is False
    with pytest.raises(TypstUnavailableError):
        renderer.render("x")


@pytest.mark.skipif(not TypstRenderer().available(), reason="Typst is not installed")
def test_a_note_renders_to_a_pdf_offline() -> None:
    pdf = TypstRenderer().render(
        notes_document(title="Note", subtitle="Corso", markdown="# A\n\n- b\n- **c**")
    )
    assert pdf.startswith(b"%PDF-")
