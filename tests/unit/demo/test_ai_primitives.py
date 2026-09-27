"""Behavior contracts for the dependency-free Cardine AI primitives.

These tests deliberately execute the browser asset with Node rather than
reimplementing renderer behavior in Python.  The tiny DOM below is sufficient
for the local presentation bindings and keeps the test suite dependency-free.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# The embedded Node fixtures intentionally mirror compact JavaScript snippets;
# wrapping every line would obscure the browser contract under test.
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


def test_all_17_renderers_escape_untrusted_text_and_keep_bounded_defaults() -> None:
    source = json.dumps(str(PRIMITIVES))
    script = f"""
const path = {source};
require(path);
const hostile = '<script>alert(1)</script> & "quoted"';
const longText = 'x'.repeat(12001);
const cases = {{
  loading: CardineAI.loading({{label: hostile, detail: hostile}}),
  thinking: CardineAI.thinking({{summary: hostile, steps: [{{label: hostile, detail: hostile}}]}}),
  answer: CardineAI.answer({{answer: hostile, citations: [hostile], followUps: [{{label: hostile, prompt: hostile}}]}}),
  approval: CardineAI.approval({{title: hostile, detail: hostile, choices: [{{label: hostile, action: hostile}}]}}),
  toolStack: CardineAI.toolStack({{title: hostile, tools: [{{label: hostile, detail: hostile}}]}}),
  taskList: CardineAI.taskList({{title: hostile, tasks: [{{label: hostile, detail: hostile, id: hostile}}]}}),
  chatPanel: CardineAI.chatPanel({{title: hostile, messages: [{{role: 'learner', content: hostile}}]}}),
  recommendation: CardineAI.recommendation({{title: hostile, detail: hostile, prompt: hostile}}),
  contextGrid: CardineAI.contextGrid({{title: hostile, cards: [{{title: hostile, detail: hostile, source: hostile}}]}}),
  diffTable: CardineAI.diffTable({{title: hostile, rows: [{{label: hostile, before: hostile, after: hostile}}]}}),
  recordsTable: CardineAI.recordsTable({{title: hostile, columns: ['label'], records: [{{label: hostile}}]}}),
  filterTable: CardineAI.filterTable({{title: hostile, columns: ['label'], records: [{{label: hostile}}]}}),
  sidebarSearch: CardineAI.sidebarSearch({{placeholder: hostile, shortcut: hostile}}),
  commandSearch: CardineAI.commandSearchMarkup({{title: hostile, placeholder: hostile}}),
  insightDeck: CardineAI.insightDeck({{title: hostile, insights: [{{title: hostile, detail: hostile, source: hostile}}]}}),
  codeBlock: CardineAI.codeBlock({{title: hostile, code: hostile, caption: hostile}}),
  fineTune: CardineAI.fineTune({{title: hostile, detail: hostile, styles: [{{label: hostile, prompt: hostile}}]}}),
}};
const roots = {{
  loading: 'ai-loading', thinking: 'ai-thinking', answer: 'ai-answer', approval: 'ai-approval',
  toolStack: 'ai-tool-stack', taskList: 'ai-task-list', chatPanel: 'ai-chat-panel',
  recommendation: 'ai-recommendation', contextGrid: 'ai-context-grid', diffTable: 'ai-diff-table',
  recordsTable: 'ai-records-table', filterTable: 'ai-filter-table', sidebarSearch: 'ai-sidebar-search',
  commandSearch: 'ai-command-search', insightDeck: 'ai-insight-deck', codeBlock: 'ai-code-block',
  fineTune: 'ai-fine-tune',
}};
const output = {{}};
for (const [name, html] of Object.entries(cases)) {{
  output[name] = {{
    root: html.includes(roots[name]),
    escaped: html.includes('&lt;script&gt;alert(1)&lt;/script&gt;') && !html.includes('<script>alert(1)</script>'),
    hasAmpEscaped: html.includes('&amp;'),
    hasQuoteEscaped: html.includes('&quot;'),
  }};
}}
output.long = CardineAI.answer({{answer: longText}});
output.longBounded = output.long.length < 13000 && output.long.includes('…') && !output.long.includes(longText);
console.log(JSON.stringify(output));
"""
    result = _run_node(script)
    assert isinstance(result, dict)
    assert set(result) == {
        "loading",
        "thinking",
        "answer",
        "approval",
        "toolStack",
        "taskList",
        "chatPanel",
        "recommendation",
        "contextGrid",
        "diffTable",
        "recordsTable",
        "filterTable",
        "sidebarSearch",
        "commandSearch",
        "insightDeck",
        "codeBlock",
        "fineTune",
        "long",
        "longBounded",
    }
    for name, contract in result.items():
        if name in {"long", "longBounded"}:
            continue
        assert contract == {
            "root": True,
            "escaped": True,
            "hasAmpEscaped": True,
            "hasQuoteEscaped": True,
        }, name
    assert result["longBounded"] is True


def test_enhance_binds_local_hooks_without_submitting_or_networking() -> None:
    source = json.dumps(str(PRIMITIVES))
    script = f"""
const path = {source};
require(path);

class MiniEventTarget {{
  constructor() {{ this.listeners = {{}}; }}
  addEventListener(type, listener) {{ (this.listeners[type] ||= []).push(listener); }}
  removeEventListener(type, listener) {{ this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== listener); }}
  dispatchEvent(event) {{ for (const listener of (this.listeners[event.type] || []).slice()) listener.call(this, event); return true; }}
}}
function camel(name) {{ return name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()); }}
class MiniClassList {{
  constructor(owner) {{ this.owner = owner; }}
  add(...names) {{ names.forEach((name) => this.owner.classes.add(name)); }}
  remove(...names) {{ names.forEach((name) => this.owner.classes.delete(name)); }}
  toggle(name, force) {{ const next = force === undefined ? !this.owner.classes.has(name) : force; if (next) this.add(name); else this.remove(name); return next; }}
  contains(name) {{ return this.owner.classes.has(name); }}
}}
class MiniElement extends MiniEventTarget {{
  constructor(tag, ownerDocument) {{ super(); this.tagName = tag.toUpperCase(); this.ownerDocument = ownerDocument; this.attrs = {{}}; this.dataset = {{}}; this.children = []; this.parentNode = null; this.classes = new Set(); this.classList = new MiniClassList(this); this.hidden = false; this.open = false; this.value = ''; this.focused = false; }}
  setAttribute(name, value) {{ this.attrs[name] = String(value); if (name === 'class') {{ this.classes = new Set(String(value).split(/\\s+/).filter(Boolean)); }} if (name.startsWith('data-')) this.dataset[camel(name.slice(5))] = String(value); }}
  getAttribute(name) {{ return this.attrs[name] ?? null; }}
  removeAttribute(name) {{ delete this.attrs[name]; if (name.startsWith('data-')) delete this.dataset[camel(name.slice(5))]; }}
  appendChild(child) {{ child.parentNode = this; this.children.push(child); return child; }}
  removeChild(child) {{ this.children = this.children.filter((item) => item !== child); child.parentNode = null; return child; }}
  replaceChildren(...children) {{ this.children = []; children.forEach((child) => this.appendChild(child)); }}
  focus() {{ this.focused = true; }}
  closest(selector) {{ let node = this; while (node) {{ if (matches(node, selector)) return node; node = node.parentNode; }} return null; }}
  get textContent() {{ return this._text !== undefined ? this._text : this.children.map((child) => child.textContent).join(''); }}
  set textContent(value) {{ this._text = String(value); this.children = []; }}
  querySelector(selector) {{ return this.querySelectorAll(selector)[0] || null; }}
  querySelectorAll(selector) {{ return descendants(this).filter((node) => matchesSelector(node, selector)); }}
}}
class MiniDocument {{ createElement(tag) {{ return new MiniElement(tag, this); }} }}
function descendants(root) {{ const output = []; for (const child of root.children) {{ output.push(child, ...descendants(child)); }} return output; }}
function matches(node, selector) {{ return matchesSelector(node, selector); }}
function matchesSimple(node, selector) {{
  selector = selector.trim();
  if (selector === '*') return true;
  const id = selector.match(/#([A-Za-z0-9_-]+)/); if (id && node.getAttribute('id') !== id[1]) return false;
  for (const cls of [...selector.matchAll(/\\.([A-Za-z0-9_-]+)/g)]) if (!node.classes.has(cls[1])) return false;
  for (const attr of [...selector.matchAll(/\\[([^\\]=]+)(?:=\\"?([^\\]\\"]+)\\"?)?\\]/g)]) {{ if (!(attr[1] in node.attrs)) return false; if (attr[2] && node.attrs[attr[1]] !== attr[2]) return false; }}
  const tag = selector.match(/^([A-Za-z][A-Za-z0-9-]*)/); return !tag || node.tagName === tag[1].toUpperCase();
}}
function matchesSelector(node, selector) {{ return selector.split(',').some((part) => {{ const tokens = part.trim().split(/\\s+/); return tokens.length === 1 ? matchesSimple(node, tokens[0]) : matchesSimple(node, tokens[tokens.length - 1]) && (() => {{ let parent = node.parentNode; for (let i = tokens.length - 2; i >= 0; i--) {{ while (parent && !matchesSimple(parent, tokens[i])) parent = parent.parentNode; if (!parent) return false; parent = parent.parentNode; }} return true; }})(); }}); }}
const document = new MiniDocument();
const root = document.createElement('main');
const form = document.createElement('form'); form.setAttribute('data-entry-form', ''); const composer = document.createElement('textarea'); form.appendChild(composer); root.appendChild(form);
const details = document.createElement('details'); details.open = false; const trigger = document.createElement('summary'); trigger.setAttribute('data-ai-disclosure-trigger', ''); details.appendChild(trigger); root.appendChild(details);
const filter = document.createElement('section'); filter.className = 'ai-filter-table'; filter.setAttribute('class', 'ai-filter-table'); const input = document.createElement('input'); input.setAttribute('data-ai-filter', ''); const status = document.createElement('span'); status.setAttribute('data-ai-filter-status', ''); const tbody = document.createElement('tbody'); const rowA = document.createElement('tr'); rowA.textContent = 'Anatomia'; const rowB = document.createElement('tr'); rowB.textContent = 'Farmacologia'; tbody.appendChild(rowA); tbody.appendChild(rowB); filter.appendChild(input); filter.appendChild(status); filter.appendChild(tbody); root.appendChild(filter);
const deck = document.createElement('section'); deck.className = 'ai-insight-deck'; deck.setAttribute('class', 'ai-insight-deck'); const first = document.createElement('article'); first.setAttribute('data-ai-insight', '0'); const second = document.createElement('article'); second.setAttribute('data-ai-insight', '1'); const prev = document.createElement('button'); prev.setAttribute('data-ai-insight-prev', ''); const next = document.createElement('button'); next.setAttribute('data-ai-insight-next', ''); deck.appendChild(first); deck.appendChild(second); deck.appendChild(prev); deck.appendChild(next); root.appendChild(deck);
const fineTune = document.createElement('section'); fineTune.setAttribute('data-ai-fine-tune', ''); const style = document.createElement('button'); style.setAttribute('data-ai-style', 'Più esempi'); style.textContent = 'Più esempi'; fineTune.appendChild(style); root.appendChild(fineTune);
const follow = document.createElement('button'); follow.setAttribute('data-ai-follow-up', 'Spiegami meglio'); follow.textContent = 'Spiegami meglio'; root.appendChild(follow);
const copy = document.createElement('button'); copy.setAttribute('data-ai-copy', '  testo da copiare  '); root.appendChild(copy);
const events = {{ follow: [], fineTune: [], copy: [] }};
const destroy = CardineAI.enhance(root, {{ onFollowUp: (value) => events.follow.push(value), onFineTune: (value) => events.fineTune.push(value), onCopy: (value, success) => events.copy.push([value, success]) }});
trigger.dispatchEvent({{type: 'keydown', key: 'Enter', preventDefault() {{}}}});
input.value = 'anatom'; input.dispatchEvent({{type: 'input'}});
next.dispatchEvent({{type: 'click'}});
style.dispatchEvent({{type: 'click'}});
follow.dispatchEvent({{type: 'click'}});
copy.dispatchEvent({{type: 'click'}});
const beforeDestroy = {{ disclosureOpen: details.open, filterHidden: [rowA.hidden, rowB.hidden], filterStatus: status.textContent, insightHidden: [first.hidden, second.hidden], composer: composer.value, follow: events.follow.slice(), fineTune: events.fineTune.slice(), copyState: copy.getAttribute('data-copy-state') }};
destroy();
follow.dispatchEvent({{type: 'click'}});
const afterDestroy = {{ follow: events.follow.slice() }};
const destroyAgain = CardineAI.enhance(root, {{ onFineTune: (value) => events.fineTune.push(value) }});
style.dispatchEvent({{type: 'click'}});
destroyAgain();
const afterRebind = {{ fineTune: events.fineTune.slice(), composer: composer.value }};
console.log(JSON.stringify({{ beforeDestroy, afterDestroy, afterRebind }}));
"""
    result = _run_node(script)
    assert result == {
        "beforeDestroy": {
            "disclosureOpen": True,
            "filterHidden": [False, True],
            "filterStatus": "1 risultati",
            "insightHidden": [True, False],
                "composer": "Spiegami meglio",
                "follow": ["Più esempi", "Spiegami meglio"],
            "fineTune": ["Più esempi"],
            "copyState": "unavailable",
        },
        "afterDestroy": {"follow": ["Più esempi", "Spiegami meglio"]},
        "afterRebind": {
            "fineTune": ["Più esempi", "Più esempi"],
            "composer": "Più esempi",
        },
    }


def test_command_palette_filters_keywords_and_preserves_keyboard_selection() -> None:
    source = json.dumps(str(PRIMITIVES))
    script = f"""
const path = {source};
require(path);
class E {{ constructor(tag, doc) {{ this.tagName = tag; this.ownerDocument = doc; this.children = []; this.attrs = {{}}; this.dataset = {{}}; this.listeners = {{}}; this.parentNode = null; this.hidden = false; }} setAttribute(k,v) {{ this.attrs[k] = String(v); if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-([a-z])/g, (_,c)=>c.toUpperCase())] = String(v); }} removeAttribute(k) {{ delete this.attrs[k]; }} scrollIntoView() {{}} getAttribute(k) {{ return this.attrs[k] ?? null; }} appendChild(c) {{ c.parentNode = this; this.children.push(c); return c; }} replaceChildren(...c) {{ this.children = []; c.forEach((x) => this.appendChild(x)); }} addEventListener(k, fn) {{ (this.listeners[k] ||= []).push(fn); }} removeEventListener() {{}} dispatchEvent(e) {{ (this.listeners[e.type] || []).forEach((fn) => fn(e)); }} querySelector(s) {{ return this.querySelectorAll(s)[0] || null; }} querySelectorAll(s) {{ return this.children.flatMap((c) => [c, ...c.querySelectorAll(s)]).filter((n) => s.split(',').some((x) => x.trim() === '[data-ai-command-input]' && n.dataset.aiCommandInput !== undefined || x.trim() === '[data-ai-command-results]' && n.dataset.aiCommandResults !== undefined || x.trim() === '[data-ai-command-empty]' && n.dataset.aiCommandEmpty !== undefined)); }} }}
class D {{ createElement(tag) {{ return new E(tag, this); }} }}
const document = new D(); const dialog = new E('DIALOG', document); const input = new E('INPUT', document); input.setAttribute('data-ai-command-input',''); const results = new E('DIV', document); results.setAttribute('data-ai-command-results',''); const empty = new E('P', document); empty.setAttribute('data-ai-command-empty',''); dialog.appendChild(input); dialog.appendChild(results); dialog.appendChild(empty); dialog.close = () => {{ dialog.closed = true; }};
const selected = []; const destroy = CardineAI.commandSearch(dialog, [{{label:'Anatomia', description:'fonti', keywords:['clinica']}}, {{label:'Farmaco', description:'dose', keywords:['terapia']}}], (item) => selected.push(item.label));
input.value = 'clinica'; input.dispatchEvent({{type: 'input'}}); const afterFilter = {{count: results.children.length, label: results.children[0].children[1].children[0].textContent}};
input.dispatchEvent({{type: 'keydown', key:'Enter', isComposing:true, keyCode:229, preventDefault() {{}}}});
input.dispatchEvent({{type: 'keydown', key:'ArrowDown', isComposing:true, keyCode:229, preventDefault() {{ throw new Error('IME navigation was intercepted'); }}}});
input.dispatchEvent({{type: 'keydown', key:'Escape', isComposing:true, keyCode:229}});
const selectedDuringComposition = selected.slice();
input.dispatchEvent({{type: 'keydown', key:'ArrowDown', preventDefault() {{}}}}); input.dispatchEvent({{type: 'keydown', key:'Enter', preventDefault() {{}}}});
input.dispatchEvent({{type: 'keydown', key:'Escape'}});
console.log(JSON.stringify({{afterFilter, selectedDuringComposition, selected, closed: dialog.closed === true, empty: empty.hidden}}));
"""
    result = _run_node(script)
    assert result == {
        "afterFilter": {"count": 1, "label": "Anatomia"},
        "selectedDuringComposition": [],
        "selected": ["Anatomia"],
        "closed": True,
        "empty": True,
    }


def test_command_palette_mapping_and_keyboard_markers_remain_explicit() -> None:
    source = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")
    for marker in (
        'label: "Spiegami un concetto"',
        'label: "Interrogami"',
        'label: "Crea un collegamento clinico"',
        'keywords: ["quiz", "verifica", "domanda"]',
        'data-open-command-search',
        'event.key === "/"',
        'event.key.toLowerCase() === "o"',
        'event.metaKey || event.ctrlKey',
        'populateComposer: false',
        'control?.closest(".continuation")',
        'if (!mobile && rail.classList.contains("is-open"))',
    ):
        assert marker in source


def test_staged_reveal_and_sidebar_search_remain_motion_and_keyboard_safe() -> None:
    source = PRIMITIVES.read_text(encoding="utf-8")

    assert 'data-ai-reveal="true"' in source
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in source
    assert "clearTimeout(timer)" in source
    assert 'copy.classList.add("is-revealing")' in source
    assert 'announcement.setAttribute("aria-atomic", "true")' in source
    assert 'callbacks.populateComposer === false' in source
    assert 'event.isComposing || event.keyCode === 229' in source
    assert '<button class="ai-sidebar-search"' in source
    assert "delete card.dataset.aiEnhanced" in source


def test_primitives_are_dependency_free_and_have_no_provider_side_effects() -> None:
    source = PRIMITIVES.read_text(encoding="utf-8")
    assert "fetch(" not in source
    assert "/api/" not in source
    assert "XMLHttpRequest" not in source
    assert "innerHTML" not in source
    assert "CardineAI" in source
