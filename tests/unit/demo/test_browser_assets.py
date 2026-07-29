from pathlib import Path

DEMO_DIR = Path(__file__).parents[3] / "src" / "study_agent" / "demo"


def test_cardine_assets_are_split_and_reference_each_other() -> None:
    page = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
    css = (DEMO_DIR / "browser.css").read_text(encoding="utf-8")
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/browser.css">' in page
    assert '<script src="/browser.js" defer></script>' in page
    assert "<style" not in page
    assert "x-dc" not in page
    assert "support.js" not in page
    assert "Claude" not in page + javascript
    assert "@media (max-width: 700px)" in css
    assert "prefers-reduced-motion" in css


def test_cardine_assets_use_only_approved_v1_routes_and_semantic_markers() -> None:
    page = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    for marker in (
        '<textarea id="entry"',
        'aria-labelledby="conversation-heading"',
        'aria-labelledby="material-heading"',
        'aria-labelledby="evidence-heading"',
        'aria-labelledby="conflict-heading"',
        'aria-labelledby="review-heading"',
        'id="entry-form"',
    ):
        assert marker in page

    for route in (
        "/api/v1/bootstrap",
        "/api/v1/session",
        "/api/v1/session/turns",
        "/api/v1/session/continuations/",
        "/api/v1/materials",
        "/api/v1/artifacts",
        "/api/v1/assessments",
        "/api/v1/evidence",
        "/api/v1/recall/due",
        "/api/v1/context/conflicts",
    ):
        assert route in javascript

    assert "schema_version: SCHEMA_VERSION" in javascript
    assert "expected_sequence: state.highWaterSequence" in javascript
    assert "/api/state" not in javascript
    assert "/api/entry" not in javascript


def test_piano_is_explicitly_unavailable_and_source_conflicts_are_read_only() -> None:
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert "Piano non disponibile" in javascript
    assert "Disaccordo tra fonti: sola lettura" in javascript
    assert "Non esiste un owner canonico" in javascript
