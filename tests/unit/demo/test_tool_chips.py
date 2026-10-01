"""Pure Tool Chips renderer contracts (no DOM, network, or provider access)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# The embedded JavaScript is intentionally formatted as JavaScript rather than
# wrapped to Python's line length.
# ruff: noqa: E501

DEMO_DIR = Path(__file__).parents[3] / "src" / "cardine" / "demo"
PRIMITIVES = DEMO_DIR / "ai-primitives.js"


def _run_node(script: str) -> object:
    result = subprocess.run(
        ["node", "-e", script],
        cwd=DEMO_DIR.parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_tool_chips_escape_hostile_targets_bound_long_text_and_never_emit_title() -> None:
    source = json.dumps(str(PRIMITIVES))
    script = f"""
const path = {source};
require(path);
const hostile = '<script>alert(1)</script> & "quoted"';
const longTarget = 'x'.repeat(12001);
const rendered = CardineAI.toolChips({{
  state: 'running',
  records: [
    {{kind: 'retrieval', ref: 'retrieval.search', label: hostile, target: longTarget, count: 8, status: 'running'}},
    {{kind: 'verification', ref: 'verification.answer', label: 'Verifico la risposta', target: hostile, status: 'failed', error_code: 'provider_protocol_error'}},
  ],
}});
console.log(JSON.stringify({{rendered, empty: CardineAI.toolChips({{state: 'settled', records: []}})}}));
"""

    result = _run_node(script)
    assert isinstance(result, dict)
    rendered = result["rendered"]
    assert isinstance(rendered, str)
    assert '<details class="ai-tool-chips"' in rendered
    assert 'data-state="running"' in rendered
    assert "4 attività" not in rendered
    assert "2 attività" in rendered
    assert "1 errore" in rendered
    assert 'data-state="failed"' in rendered
    assert 'data-tone="retrieval"' in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered
    assert "&amp;" in rendered
    assert "title=" not in rendered
    assert len(rendered) < 14000
    assert result["empty"] == ""


def test_tool_chips_summary_has_true_singular_plural_and_settled_state() -> None:
    source = json.dumps(str(PRIMITIVES))
    script = f"""
const path = {source};
require(path);
const one = CardineAI.toolChips({{
  state: 'settled',
  records: [{{kind: 'capability', ref: 'capability.explain_concept', label: 'Preparo la spiegazione', target: '', status: 'done'}}],
}});
const many = CardineAI.toolChips({{
  state: 'settled',
  records: [
    {{kind: 'capability', ref: 'capability.explain_concept', label: 'Preparo la spiegazione', target: '', status: 'done'}},
    {{kind: 'tool', ref: 'repository.write', label: 'Registro nel repository', target: 'corso', status: 'done'}},
  ],
}});
console.log(JSON.stringify({{one, many}}));
"""

    result = _run_node(script)
    assert isinstance(result, dict)
    assert "1 attività" in result["one"]
    assert "2 attività" in result["many"]
    assert "· in corso" not in result["one"]
    assert 'data-state="settled"' in result["one"]
    assert 'data-state="done"' in result["one"]
    assert "Preparo la spiegazione" in result["one"]
    assert "Registro nel repository" in result["many"]
