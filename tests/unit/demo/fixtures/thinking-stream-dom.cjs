/* Offline DOM contract fixture; no provider, network or browser dependency. */
const fs = require('node:fs');
const vm = require('node:vm');
require(process.argv[2]);
const timers = new Map();
let timerId = 0;
global.setTimeout = (fn, delay) => { fn.delay = delay; const id = ++timerId; timers.set(id, fn); return id; };
const copies = [];
Object.defineProperty(global, 'navigator', { value: { clipboard: { writeText: async (text) => copies.push(text) } }, configurable: true });

class Element {
  constructor(tag, doc, text = '') {
    this.tagName = tag.toUpperCase(); this.ownerDocument = doc; this.children = [];
    this.attrs = {}; this.dataset = {}; this.listeners = new Map(); this.parentNode = null;
    this._text = text;
    this.classList = { add: (name) => this.classes.add(name), contains: (name) => this.classes.has(name) };
    this.classes = new Set();
  }
  set className(value) { this.classes = new Set(value.split(' ')); }
  get className() { return [...this.classes].join(' '); }
  setAttribute(key, value) { this.attrs[key] = String(value); if (key === 'class') this.className = value; }
  getAttribute(key) { return this.attrs[key] ?? null; }
  hasAttribute(key) { return key in this.attrs; }
  removeAttribute(key) { delete this.attrs[key]; }
  get attributes() { return Object.entries(this.attrs).map(([name, value]) => ({ name, value })); }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(''); }
  set textContent(value) { this._text = value; this.children = []; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  addEventListener(type, fn) { const listeners = this.listeners.get(type) || []; listeners.push(fn); this.listeners.set(type, listeners); }
  removeEventListener(type, fn) { this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== fn)); }
  click() { (this.listeners.get('click') || []).slice().forEach((fn) => fn()); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const all = [];
    const visit = (element) => { element.children.forEach((child) => { all.push(child); visit(child); }); };
    visit(this);
    return all.filter((child) => selector.split(',').some((part) => {
      part = part.trim();
      if (part.startsWith('.')) return child.classes.has(part.slice(1));
      const attribute = part.match(/^\[([^=\]]+)(?:="([^"]+)")?\]$/);
      return attribute ? child.hasAttribute(attribute[1]) && (!attribute[2] || child.getAttribute(attribute[1]) === attribute[2]) : false;
    }));
  }
}
class Document {
  createElement(tag) { return new Element(tag, this); }
}
function fixture() {
  const doc = new Document(); const root = doc.createElement('main');
  const article = root.appendChild(doc.createElement('article')); article.className = 'ai-answer--chat';
  const copy = article.appendChild(doc.createElement('div')); copy.className = 'ai-answer__markdown';
  const p = copy.appendChild(doc.createElement('p'));
  p.appendChild(new Element('#text', doc, 'Una risposta '));
  const strong = p.appendChild(doc.createElement('strong')); strong.appendChild(new Element('#text', doc, 'verificata <img> '));
  p.appendChild(new Element('#text', doc, 'con fonti.'));
  const status = article.appendChild(doc.createElement('span')); status.setAttribute('data-ai-action-status', '');
  const controls = {};
  ['copy', 'retry', 'like', 'dislike'].forEach((action) => {
    const button = article.appendChild(doc.createElement('button')); button.setAttribute('data-ai-answer-action', action);
    if (['like', 'dislike'].includes(action)) button.setAttribute('aria-pressed', 'false'); controls[action] = button;
  });
  return { root, article, copy, strong, controls };
}
function section(source, start, end) { return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))); }
async function run() {
  const first = fixture(); const fullText = first.copy.textContent;
  const destroy = CardineAI.enhance(first.root);
  const immediate = first.copy.textContent === fullText && !first.copy.hasAttribute('aria-hidden')
    && !first.article.hasAttribute('aria-busy') && first.strong.tagName === 'STRONG' && timers.size === 0;
  destroy();
  const actions = fixture(); let retry = 0; const feedback = [];
  const remove = CardineAI.enhance(actions.root, { onRetry: () => retry++, onFeedback: (value) => feedback.push(value) });
  const duplicateActions = CardineAI.enhance(actions.root, { onRetry: () => retry++ }); duplicateActions();
  actions.controls.copy.click(); await Promise.resolve();
  actions.controls.retry.click(); actions.controls.like.click(); actions.controls.dislike.click();
  const exclusive = actions.controls.like.getAttribute('aria-pressed') === 'false' && actions.controls.dislike.getAttribute('aria-pressed') === 'true';
  actions.controls.dislike.click(); remove(); actions.controls.like.click();

  const browser = fs.readFileSync(process.argv[3], 'utf8');
  const sync = section(browser, '  function syncAttributes', '\n  function morphElement');
  const context = { Array };
  vm.createContext(context); vm.runInContext(sync, context);
  const current = new Element('details'); current.className = 'ai-tool-chips'; current.dataset.state = 'running'; current.open = true;
  const next = new Element('details'); next.dataset.state = 'settled';
  context.syncAttributes(current, next); const collapsed = !current.open;
  current.dataset.state = 'settled'; current.open = true; context.syncAttributes(current, next);
  const preservedChoice = current.open;

  const poll = section(browser, '  async function pollTurnActivity', '\n  function restoreFailedTurnDraft');
  let resolveFetch, patches = 0;
  const state = { activityPollToken: 0, navigationVersion: 0, pendingTurn: { requestId: 'one' } };
  const pollingContext = { state, fetchJson: (path) => path.endsWith('/output') ? Promise.resolve({state:'unavailable',text:''}) : new Promise((resolve) => { resolveFetch = resolve; }), text: (value) => value || '', $: () => ({}), patch: () => patches++, aiToolChips: () => '', root: {}, captureScroll: () => ({}), restoreScroll: () => {}, window: { setTimeout: global.setTimeout } };
  vm.createContext(pollingContext); vm.runInContext(poll, pollingContext);
  const polling = pollingContext.pollTurnActivity('one'); state.navigationVersion++; resolveFetch({ state: 'running', records: [{ sequence: 1 }] }); await polling;
  const stalePollIgnored = patches === 0 && timers.size === 0;

  const liveDraft = { hidden: true }; const liveText = { textContent: '' };
  const liveState = { activityPollToken: 0, navigationVersion: 0, pendingTurn: { requestId: 'live' } };
  const liveContext = {
    state: liveState,
    fetchJson: async (path) => path.endsWith('/output')
      ? { state: 'generating', text: '<img src=x onerror=alert(1)>token' }
      : { state: 'running', records: [] },
    text: (value) => value || '',
    $: (selector) => selector === '[data-turn-draft]' ? liveDraft : selector === '[data-turn-draft-text]' ? liveText : null,
    root: {}, captureScroll: () => ({}), restoreScroll: () => {},
    window: { setTimeout: (resolve, delay) => { liveState.pendingTurn = null; resolve(); return delay; } },
  };
  vm.createContext(liveContext); vm.runInContext(poll, liveContext);
  await liveContext.pollTurnActivity('live');
  const liveDraftSafe = !liveDraft.hidden && liveText.textContent === '<img src=x onerror=alert(1)>token' && !('innerHTML' in liveText);

  const retrySource = section(browser, '  async function retryAnswer', '\n  function renderFonti');
  const retryCalls = []; const original = { endpoint: '/api/v1/session/turns', payload: { content: 'Original prompt', lesson_pin: { lesson_id: 'original' } } };
  const retryContext = { state: { turnCommands: { answer: original } }, requestId: () => 'new-key', executeCommand: async (...args) => retryCalls.push(args) };
  vm.createContext(retryContext); vm.runInContext(retrySource, retryContext);
  const control = { closest: () => ({ dataset: { messageId: 'answer' } }) };
  await retryContext.retryAnswer(control); retryContext.state.pendingTurn = {}; await retryContext.retryAnswer(control);

  const commandSource = section(browser, '  async function executeCommand', '\n  function renderOptimisticTurn');
  async function commandAfterNavigation(navigateAway) {
    let resolveFetch;
    const commandState = {
      navigationVersion: 1, route: 'sessione', lastCommand: null, pendingTurn: null,
      turnCommands: {}, turnActivities: {},
      activityPollToken: 0, highWaterSequence: 0,
    };
    let renders = 0;
    const context = {
      state: commandState, text: (value, fallback = '') => String(value || fallback),
      object: (value) => value || {}, array: (value) => value || [],
      requestId: () => 'turn-request', renderOptimisticTurn: () => {}, pollTurnActivity: async () => {},
      setBusy: () => {}, setStatus: () => {}, commandPayload: (payload) => payload,
      fetchJson: () => new Promise((resolve) => { resolveFetch = resolve; }),
      updateSequence: () => {}, first: (_value, _keys, fallback) => fallback,
      statusLabel: (status) => status, INCOMPLETE_TURN_STATUSES: new Set(),
      renderSessione: () => renders++, refreshBootstrapCounts: async () => {},
      $$: () => [], $: () => null, root: {},
    };
    vm.createContext(context); vm.runInContext(commandSource, context);
    const task = context.executeCommand('/api/v1/session/turns', { content: 'Una domanda' }, null, 'sessione');
    if (navigateAway) { commandState.route = 'fonti'; commandState.navigationVersion += 1; }
    resolveFetch({ presentation_id: 'answer-1', status: 'completed', result: {} });
    await task;
    return { rendered: renders, settled: commandState.pendingTurn === null, retryRemembered: commandState.turnCommands['answer-1'].requestId === 'turn-request' };
  }
  const offRouteAnswer = await commandAfterNavigation(true);
  const onRouteAnswer = await commandAfterNavigation(false);
  console.log(JSON.stringify({ immediate, copies, retry, feedback, exclusive, collapsed, preservedChoice, stalePollIgnored, liveDraftSafe, retryCalls, offRouteAnswer, onRouteAnswer }));
}
run().catch((error) => { console.error(error); process.exit(1); });
