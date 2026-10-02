"""Offline behavior contracts for the compact activity and answer lifecycle."""
# Embedded JavaScript is formatted as JavaScript.
# ruff: noqa: E501
from __future__ import annotations

import json
import subprocess
from pathlib import Path

DEMO = Path(__file__).parents[3] / "src" / "cardine" / "demo"
FIXTURE = Path(__file__).parent / "fixtures" / "thinking-stream-dom.cjs"


def test_stream_actions_disclosure_and_stale_poll_lifecycle() -> None:
    result = subprocess.run(
        ["node", str(FIXTURE), str(DEMO / "ai-primitives.js"), str(DEMO / "browser.js")],
        check=True, capture_output=True, text=True,
    )
    observed = json.loads(result.stdout)
    assert observed == {
        "immediate": True,
        "copies": ["Una risposta verificata <img> con fonti."], "retry": 1,
        "feedback": ["like", "dislike", ""], "exclusive": True,
        "collapsed": True, "preservedChoice": True, "stalePollIgnored": True,
        "liveDraftSafe": True,
        "retryCalls": [[
            "/api/v1/session/turns",
            {"content": "Original prompt", "lesson_pin": {"lesson_id": "original"}},
            None, "sessione", "new-key",
        ]],
        "offRouteAnswer": {"rendered": 0, "settled": True, "retryRemembered": True},
        "onRouteAnswer": {"rendered": 1, "settled": True, "retryRemembered": True},
    }


def test_activity_dispatch_uses_safe_references_and_stable_sequence_keys() -> None:
    script = """
require(process.argv[1]);
const rows = [
 {sequence: 1, ref: 'retrieval.lesson', label: 'Leggo', target: 'Lezione 1.pdf', status: 'done'},
 {sequence: 2, ref: 'retrieval.search', label: 'Cerco nelle fonti', target: 'Lezione 2', count: 8, status: 'running'},
 {sequence: 3, ref: 'capability.propose_flashcards', label: 'Preparo le flashcard', target: 'Lezione 1', status: 'running'},
];
console.log(JSON.stringify({
 initial: CardineAI.toolChips({state: 'running', records: []}),
 live: CardineAI.toolChips({state: 'running', records: rows}),
 settled: CardineAI.toolChips({state: 'settled', records: rows.map(row => ({...row, status:'done'}))}),
 answer: CardineAI.answer({chat:true, canRetry:true, answer:'**Verificato** <script>x</script>', followUps:['Continua']}),
}));
"""
    result = subprocess.run(
        ["node", "-e", script, str(DEMO / "ai-primitives.js")],
        check=True, capture_output=True, text=True,
    )
    rendered = json.loads(result.stdout)
    assert ' open>' in rendered["initial"]
    assert ' open>' in rendered["live"]
    assert ' open>' not in rendered["settled"]
    for number, renderer in enumerate(("code", "search", "steps"), start=1):
        assert f'data-key="activity-{number}" data-renderer="{renderer}"' in rendered["live"]
        assert f'data-key="activity-{number}"' in rendered["settled"]
    assert 'ai-activity-search__result' in rendered["live"]
    assert '8 risultati' in rendered["live"]
    assert '<strong>Verificato</strong>' in rendered["answer"]
    assert '<script>x</script>' not in rendered["answer"]
    for label in ("Copy", "Retry", "Like", "Dislike"):
        assert f'aria-label="{label}"' in rendered["answer"]
    assert "Follow-ups" in rendered["answer"]
