"""Executable gate for the Cardine design system.

The UI audit measured the shell and found 58 hardcoded font sizes, 16 radii,
32 gap values, two parallel token systems and two fonts that were named but
never shipped. Those are not opinions, they are counts — so they are asserted
here. A future change that reintroduces a magic number fails this file rather
than shipping and being rediscovered by a reviewer with a ruler.
"""

from __future__ import annotations

import re
from pathlib import Path

DEMO_DIR = Path(__file__).parents[3] / "src" / "cardine" / "demo"
SHELL_CSS = (DEMO_DIR / "browser.css").read_text(encoding="utf-8")
PRIMITIVES_CSS = (DEMO_DIR / "ai-primitives.css").read_text(encoding="utf-8")
ALL_CSS = SHELL_CSS + "\n" + PRIMITIVES_CSS
PAGE = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
SHELL_JS = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")
PRIMITIVES_JS = (DEMO_DIR / "ai-primitives.js").read_text(encoding="utf-8")


def _declarations_outside_root(css: str) -> str:
    """Strip comments and :root blocks, leaving only applied declarations."""

    body = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    for block in re.findall(r":root\s*\{.*?\}", body, flags=re.DOTALL):
        body = body.replace(block, "")
    return body


def _literal_lengths(css: str, property_pattern: str) -> set[str]:
    declarations = _declarations_outside_root(css)
    found: set[str] = set()
    for match in re.finditer(rf"(?<![-\w]){property_pattern}\s*:\s*([^;{{}}]+)", declarations):
        found.update(re.findall(r"(?<![\w-])(\d*\.?\d+(?:px|rem|em))", match.group(1)))
    return found


def _token_names(prefix: str) -> set[str]:
    return set(re.findall(rf"--({prefix}[a-z0-9-]*)\s*:", ALL_CSS))


def test_every_type_size_radius_and_gap_comes_from_the_scale() -> None:
    # `.9em` on <code> is a relative step off the surrounding text, not a
    # size of its own, so it is the single allowed literal.
    assert _literal_lengths(ALL_CSS, "font-size") <= {".9em"}
    assert _literal_lengths(ALL_CSS, "border-radius") == set()
    assert _literal_lengths(ALL_CSS, r"(?:row-|column-)?gap") == set()

    assert len(_token_names("text-")) == 9, "the type scale is nine steps"
    assert len(_token_names("radius")) == 5
    assert len(_token_names("space-")) == 8
    assert len(_token_names("duration")) == 3
    assert len(_token_names("ease")) == 2


def test_nothing_in_the_product_is_typeset_below_twelve_pixels() -> None:
    sizes = {
        name: value
        for name, value in re.findall(r"--(text-[a-z0-9]+)\s*:\s*([^;]+);", ALL_CSS)
        if "clamp" not in value
    }
    for name, value in sizes.items():
        rem = float(value.strip().rstrip("rem"))
        assert rem >= 0.75, f"--{name} is {rem}rem, below the 12px floor"


def test_there_is_exactly_one_token_system_and_one_theme_block() -> None:
    # A parallel --ai-* palette is what let a primitive and the shell disagree
    # about the same colour, and what produced fractional 17.6px paddings.
    assert "--ai-" not in ALL_CSS
    assert ALL_CSS.count("prefers-color-scheme") == 1
    assert ALL_CSS.count("prefers-reduced-motion") == 1
    # Layer order is what makes a shared class name safe, not specificity.
    assert "@layer reset, tokens, base, primitives, layout, components, utilities;" in SHELL_CSS
    assert "@layer primitives {" in PRIMITIVES_CSS


def test_no_font_is_named_that_the_product_does_not_ship() -> None:
    declared = set(re.findall(r'font-family:\s*"([^"]+)"', ALL_CSS))
    declared |= set(re.findall(r'--font-[a-z]+:\s*"([^"]+)"', ALL_CSS))
    shipped = {path.stem.rsplit("-", 1)[0] for path in (DEMO_DIR / "fonts").glob("*.woff2")}
    normalised = {name.lower().replace(" ", "-") for name in declared}
    assert normalised <= shipped, f"named but not packaged: {normalised - shipped}"
    # Neither of these was ever shipped; one is also a third party's typeface.
    assert "JetBrains" not in ALL_CSS
    assert "Anthropic" not in ALL_CSS + SHELL_JS + PAGE


def test_the_accent_is_a_marker_and_never_a_text_colour() -> None:
    # --accent is 2.96:1 on paper. It may fill and mark; it may not be read.
    text_uses = re.findall(r"color:\s*var\(--accent\)", ALL_CSS)
    assert text_uses == []


def test_a_closed_disclosure_occupies_and_paints_nothing() -> None:
    # The rule that stops a component stylesheet laying out the contents of a
    # collapsed <details> outside the scrollable extent.
    assert "details:not([open]) > *:not(summary) { display: none !important; }" in SHELL_CSS


def test_the_shell_has_one_tooltip_mechanism_and_no_native_ones() -> None:
    assert 'title="' not in PAGE
