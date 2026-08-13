"""Static browser seams for live Tool Chips polling and legacy removal."""

from __future__ import annotations

from pathlib import Path

DEMO_DIR = Path(__file__).parents[3] / "src" / "cardine" / "demo"


def test_browser_polls_activity_by_request_id_and_stops_on_settle() -> None:
    source = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")

    assert "function pollTurnActivity" in source
    assert "/api/v1/turns/${encodeURIComponent(requestId)}/activity" in source
    assert "data-turn-activity" in source
    assert "activity_records" in source
    assert "600" in source
    assert "2000" in source
    assert "pendingTurn" in source
    assert "pollTurnActivity" in source
    assert '["settled", "failed"].includes(text(payload.state))' in source
    assert '["settled", "failed", "unknown"]' not in source
    assert "turnActivities" in source
    assert "state.turnActivities[presentationId]" in source
    assert source.count("innerHTML") == 1


def test_legacy_session_activity_block_is_removed_without_removing_tutor_trace() -> None:
    browser = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")
    primitives = (DEMO_DIR / "ai-primitives.js").read_text(encoding="utf-8")
    css = (DEMO_DIR / "ai-primitives.css").read_text(encoding="utf-8")

    assert "ai-session-activity" not in browser
    assert "ai-session-activity" not in primitives
    assert "ai-session-activity" not in css
    assert "non ha dichiarato strumenti" not in browser
    assert "data-open-turn-trace" in browser
    assert "toolChips" in primitives
    assert 'title="' not in primitives
    assert 'data-tooltip="' in browser


def test_settled_tutor_receipt_renders_before_advisory_count_refresh() -> None:
    source = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")
    command = source[source.index("async function executeCommand") :]

    receipt = command.index("const receipt = await fetchJson")
    render = command.index("state.viewData = object(receipt.result)", receipt)
    refresh = command.index("refreshBootstrapCounts()", receipt)

    assert render < refresh
