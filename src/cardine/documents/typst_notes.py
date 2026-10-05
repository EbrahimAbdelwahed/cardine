"""Render study notes (Markdown) to PDF through the Typst compiler.

The converter covers the Markdown that study notes use: headings, paragraphs,
emphasis, inline code, links, nested lists, block quotes, fenced code, GFM
tables and rules. Every piece of text is emitted as a Typst *string literal*
(`#"…"`), never as markup, so note content cannot inject Typst syntax or code.

The compiler runs offline in a private temporary root with only the bundled
fonts (`--ignore-system-fonts`), a timeout and an output size bound.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

TEMPLATE_VERSION = "cardine-notes@1"
MAX_MARKDOWN_CHARACTERS = 2_000_000
MAX_PDF_BYTES = 64 * 1024 * 1024
COMPILE_TIMEOUT_SECONDS = 60

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)\s*$")
_RULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_LIST = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_INLINE = re.compile(
    r"(?P<code>`+)(?P<code_text>.+?)(?P=code)"
    r"|\*\*(?P<strong>.+?)\*\*"
    r"|__(?P<strong_u>.+?)__"
    r"|\*(?P<em>[^*\s](?:.*?[^*\s])?)\*"
    r"|(?<![\w])_(?P<em_u>[^_\s](?:.*?[^_\s])?)_(?![\w])"
    r"|\[(?P<link_text>[^\]]+)\]\((?P<link_url>[^)\s]+)\)"
)


class TypstUnavailableError(RuntimeError):
    """No Typst compiler is installed or configured."""


class TypstRenderError(RuntimeError):
    """The compiler failed or produced an invalid document."""


def typst_string(text: str) -> str:
    """A Typst string literal for arbitrary text."""
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace("\t", "    ")
    )
    return f'"{escaped}"'


def _text(text: str) -> str:
    return f"#{typst_string(text)}" if text else ""


def inline(text: str) -> str:
    """Inline Markdown to Typst content made only of literals and functions."""
    output: list[str] = []
    cursor = 0
    for match in _INLINE.finditer(text):
        output.append(_text(text[cursor : match.start()]))
        groups = match.groupdict()
        if groups["code"]:
            output.append(f"#raw({typst_string(groups['code_text'].strip())})")
        elif groups["strong"] or groups["strong_u"]:
            output.append(f"#strong[{inline(groups['strong'] or groups['strong_u'])}]")
        elif groups["em"] or groups["em_u"]:
            output.append(f"#emph[{inline(groups['em'] or groups['em_u'])}]")
        else:
            url = groups["link_url"]
            label = inline(groups["link_text"])
            if url.startswith(("http://", "https://", "mailto:")):
                output.append(f"#link({typst_string(url)})[{label}]")
            else:
                output.append(label)
        cursor = match.end()
    output.append(_text(text[cursor:]))
    return "".join(output)


@dataclass(frozen=True, slots=True)
class _ListItem:
    indent: int
    ordered: bool
    number: int
    text: str


def _list_block(items: Sequence[_ListItem]) -> str:
    """Nested lists from indentation; the first item's kind names the list."""
    if not items:
        return ""
    base = items[0].indent
    groups: list[tuple[_ListItem, list[_ListItem]]] = []
    for item in items:
        if item.indent <= base or not groups:
            groups.append((item, []))
        else:
            groups[-1][1].append(item)
    entries = []
    for head, children in groups:
        nested = f"\n{_list_block(children)}" if children else ""
        entries.append(f"[{inline(head.text)}{nested}]")
    if items[0].ordered:
        return f"#enum(tight: true, start: {items[0].number}, {', '.join(entries)})"
    return f"#list(tight: true, {', '.join(entries)})"


def _cells(row: str) -> list[str]:
    stripped = row.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _table(header: str, rows: Sequence[str]) -> str:
    head = _cells(header)
    columns = len(head)
    body = [(_cells(row) + [""] * columns)[:columns] for row in rows]
    head_cells = ", ".join(f"[#strong[{inline(cell)}]]" for cell in head)
    body_cells = ", ".join(f"[{inline(cell)}]" for row in body for cell in row)
    separator = ", " if body_cells else ""
    return (
        f"#table(columns: {columns}, table.header({head_cells}){separator}{body_cells})"
    )


def markdown_to_typst(markdown: str) -> str:
    """Block-level Markdown to Typst markup."""
    if len(markdown) > MAX_MARKDOWN_CHARACTERS:
        raise TypstRenderError("the note is too long to render")
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    index = 0

    def flush() -> None:
        if paragraph:
            blocks.append(inline(" ".join(part.strip() for part in paragraph)))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            flush()
            index += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            flush()
            marker, language = fence.group(1), fence.group(2)
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith(marker):
                code.append(lines[index])
                index += 1
            index += 1
            lang = f", lang: {typst_string(language)}" if language else ""
            blocks.append(f"#raw(block: true{lang}, {typst_string(chr(10).join(code))})")
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            blocks.append(f"#heading(level: {level})[{inline(heading.group(2))}]")
            index += 1
            continue
        if _RULE.match(line):
            flush()
            blocks.append("#line(length: 100%, stroke: 0.5pt + rule-color)")
            index += 1
            continue
        if line.lstrip().startswith(">"):
            flush()
            quoted: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quoted.append(re.sub(r"^\s*>\s?", "", lines[index]))
                index += 1
            blocks.append(f"#note-quote[{markdown_to_typst(chr(10).join(quoted))}]")
            continue
        if (
            "|" in line
            and index + 1 < len(lines)
            and _TABLE_SEPARATOR.match(lines[index + 1])
            and "|" in lines[index + 1]
        ):
            flush()
            header = line
            index += 2
            rows: list[str] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(lines[index])
                index += 1
            blocks.append(_table(header, rows))
            continue
        item = _LIST.match(line)
        if item and not paragraph:
            items: list[_ListItem] = []
            while index < len(lines):
                current = _LIST.match(lines[index])
                if current:
                    marker = current.group(2)
                    ordered = marker[0].isdigit()
                    items.append(
                        _ListItem(
                            len(current.group(1).replace("\t", "    ")),
                            ordered,
                            int(marker[:-1]) if ordered else 1,
                            current.group(3),
                        )
                    )
                    index += 1
                elif lines[index].strip() and items and lines[index].startswith((" ", "\t")):
                    previous = items[-1]
                    items[-1] = _ListItem(
                        previous.indent,
                        previous.ordered,
                        previous.number,
                        f"{previous.text} {lines[index].strip()}",
                    )
                    index += 1
                else:
                    break
            blocks.append(_list_block(items))
            continue
        paragraph.append(line)
        index += 1
    flush()
    return "\n\n".join(block for block in blocks if block)


_TEMPLATE = """\
#let accent = rgb("#a94a2a")
#let ink = rgb("#262521")
#let muted = rgb("#63615a")
#let rule-color = rgb("#d9d6cd")
#let note-quote(body) = block(
  width: 100%, inset: (left: 12pt, y: 4pt), stroke: (left: 2pt + accent), body,
)

#set document(title: {title}, author: "Cardine")
#set page(
  paper: "a4",
  margin: (x: 2.3cm, top: 2.5cm, bottom: 2.6cm),
  header: context {{
    if counter(page).get().first() > 1 {{
      set text(size: 8pt, fill: muted)
      [#{title}#h(1fr)#"Cardine"]
    }}
  }},
  footer: context {{
    set text(size: 8pt, fill: muted)
    align(center, counter(page).display("1 / 1", both: true))
  }},
)
#set text(font: "Libertinus Serif", size: 10.5pt, lang: "it", fill: ink, hyphenate: true)
#set par(justify: true, leading: 0.7em, spacing: 1.15em)
#set list(indent: 0.4em, body-indent: 0.55em, marker: ([\\u{{2013}}], [\\u{{00B7}}]))
#set enum(indent: 0.2em, body-indent: 0.55em)
#show heading: set block(above: 1.6em, below: 0.75em, sticky: true)
#show heading.where(level: 1): set text(size: 15pt, weight: "bold", fill: accent)
#show heading.where(level: 2): set text(size: 12.5pt, weight: "bold")
#show heading.where(level: 3): set text(size: 11pt, weight: "bold", style: "italic")
#show heading.where(level: 4): set text(size: 10.5pt, weight: "bold", fill: muted)
#show raw: set text(font: "DejaVu Sans Mono", size: 8.5pt)
#show raw.where(block: true): block.with(
  width: 100%, inset: 9pt, radius: 3pt, fill: rgb("#f3f1ec"),
)
#show link: set text(fill: accent)
#set table(
  stroke: (x, y) => (bottom: 0.5pt + rule-color, top: if y == 0 {{ 0.8pt + ink }}),
  inset: (x: 6pt, y: 5pt),
  fill: (x, y) => if y == 0 {{ rgb("#f3f1ec") }},
)
#show table: set text(size: 9.5pt)
#show table: set par(justify: false)

#block(width: 100%, below: 1.8em)[
  #text(size: 8pt, tracking: 0.12em, fill: accent, weight: "bold")[#"NOTE DI STUDIO"]
  #v(0.35em)
  #text(size: 22pt, weight: "bold", fill: ink)[#{title}]
  #v(0.2em)
  #text(size: 9pt, fill: muted)[#{subtitle}]
  #v(0.6em)
  #line(length: 100%, stroke: 0.8pt + accent)
]

{body}
"""


def notes_document(*, title: str, subtitle: str, markdown: str) -> str:
    """A complete Typst document for one note."""
    return _TEMPLATE.format(
        title=typst_string(title),
        subtitle=typst_string(subtitle),
        body=markdown_to_typst(markdown),
    )


def typst_binary() -> str | None:
    configured = os.environ.get("CARDINE_TYPST_BIN", "").strip()
    if configured:
        return configured if Path(configured).is_file() else None
    return shutil.which("typst")


@dataclass(frozen=True, slots=True)
class TypstRenderer:
    """Offline Typst CLI adapter; one private temporary root per render."""

    binary: str | None = None

    def resolved(self) -> str | None:
        return self.binary or typst_binary()

    def available(self) -> bool:
        return self.resolved() is not None

    def render(self, source: str) -> bytes:
        binary = self.resolved()
        if binary is None:
            raise TypstUnavailableError("Typst is not installed")
        with tempfile.TemporaryDirectory(prefix="cardine-typst-") as directory:
            root = Path(directory)
            (root / "main.typ").write_text(source, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [
                        binary,
                        "compile",
                        "--root",
                        str(root),
                        "--ignore-system-fonts",
                        "main.typ",
                        "main.pdf",
                    ],
                    cwd=root,
                    capture_output=True,
                    timeout=COMPILE_TIMEOUT_SECONDS,
                    check=False,
                    env={"PATH": os.environ.get("PATH", ""), "TYPST_PACKAGE_PATH": str(root)},
                )
            except subprocess.TimeoutExpired as error:
                raise TypstRenderError("the PDF took too long to render") from error
            except OSError as error:
                raise TypstUnavailableError("Typst could not be started") from error
            output = root / "main.pdf"
            if completed.returncode != 0 or not output.is_file():
                raise TypstRenderError("Typst could not render this note")
            if output.stat().st_size > MAX_PDF_BYTES:
                raise TypstRenderError("the rendered PDF is too large")
            pdf = output.read_bytes()
            if not pdf.startswith(b"%PDF-"):
                raise TypstRenderError("Typst produced an invalid PDF")
            return pdf
