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
    assert 'title="' not in SHELL_JS
    assert 'title="' not in PRIMITIVES_JS
    assert "content: attr(data-tooltip)" in SHELL_CSS


def test_every_overlay_shares_one_scrim_and_one_motion_treatment() -> None:
    assert "--scrim:" in SHELL_CSS
    # Every ::backdrop uses the same scrim token and none of them blurs: the
    # command palette used to be the only surface in the product that frosted
    # the whole application behind it.
    backdrops = re.findall(r"::backdrop[^{]*\{([^}]*)\}", ALL_CSS)
    assert backdrops, "no ::backdrop rule found"
    for rule in backdrops:
        assert "backdrop-filter" not in rule
        assert "background" not in rule or "var(--scrim)" in rule
    for name in ("sheet-enter", "sheet-exit", "modal-enter", "modal-exit"):
        assert f"@keyframes {name}" in SHELL_CSS
    # Exit is played before close(), so no dialog can disappear with a cut.
    assert "function closeDialog(dialog)" in SHELL_JS
    assert 'dialog.dataset.closing = "true"' in SHELL_JS


def test_assets_are_same_origin_because_the_product_ships_a_strict_csp() -> None:
    # A data: URI is blocked outright by `default-src 'self'`.
    assert "data:image" not in ALL_CSS
    for asset in re.findall(r'url\("(/[^"]+)"\)', ALL_CSS):
        assert (DEMO_DIR / asset.lstrip("/")).exists(), f"{asset} is referenced but not packaged"


def test_the_icon_set_is_one_family_authored_one_way() -> None:
    icons = sorted((DEMO_DIR / "icons").glob("*.svg"))
    assert icons, "the packaged icon set is missing"
    for icon in icons:
        markup = icon.read_text(encoding="utf-8")
        if icon.name in {"favicon.svg", "select-caret.svg", "select-caret-inverse.svg"}:
            continue  # brand mark and form chrome, deliberately not glyphs
        assert 'viewBox="0 0 256 256"' in markup, f"{icon.name} is not on the 256 grid"
        assert 'fill="currentColor"' in markup.split(">", 1)[0] + ">", (
            f"{icon.name} sets fill on the path instead of the svg"
        )


def test_the_render_layer_patches_the_tree_instead_of_replacing_it() -> None:
    # Replacing #view-root wholesale is what lost scroll position, focus,
    # open disclosures and any error node appended to a form.
    assert "root.innerHTML" not in SHELL_JS
    assert "function morphChildren(current, next)" in SHELL_JS
    assert "function captureScroll()" in SHELL_JS
    assert "function restoreScroll(snapshot, routeChanged)" in SHELL_JS
    # Exactly one innerHTML write remains: filling the offscreen template
    # that the patcher diffs against.
    assert SHELL_JS.count("innerHTML") == 1
    assert "template.innerHTML = html;" in SHELL_JS


def test_failures_have_a_visible_home_that_outlives_the_view() -> None:
    alert_index = PAGE.index('id="global-alert"')
    view_index = PAGE.index('id="view-root"')
    assert alert_index < view_index, "the alert region must not live inside the swapped view"
    assert "function showAlert(" in SHELL_JS
    assert 'role="status"' in PAGE and 'aria-live="polite"' in PAGE
    # The status element must be a real live region, not a 1x1 clipped box
    # that also carried the only copy of every error message.
    assert "clip-path: inset(50%)" in SHELL_CSS


def test_the_composer_measures_itself_from_its_own_stylesheet() -> None:
    # Hardcoding the minimum in JS is how 41px of typed text ended up hidden.
    assert "parseFloat(styles.minHeight)" in SHELL_JS
    assert "parseFloat(styles.maxHeight)" in SHELL_JS
    assert "function syncComposers()" in SHELL_JS
