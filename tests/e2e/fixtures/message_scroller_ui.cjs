const fs = require('node:fs');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../../src/cardine/demo/browser.js'), 'utf8');
const controller = source.slice(source.indexOf('  function enhanceMessageScroller('), source.indexOf('  function captureScroll('));
let scheduled = new Map(); let frameId = 0;
function requestAnimationFrame(callback) { scheduled.set(++frameId, callback); return frameId; }
function cancelAnimationFrame(id) { scheduled.delete(id); }
function flush() { const callbacks = [...scheduled.values()]; scheduled.clear(); callbacks.forEach(fn => fn()); }
let observers = [];
class Observer {
  constructor(callback) { this.callback = callback; this.disconnected = false; this.observed = []; observers.push(this); }
  observe(target, options) { this.observed.push({target, options}); } disconnect() { this.disconnected = true; }
}
const ResizeObserver = Observer; const MutationObserver = Observer;
const state = {loading: false}; const NEAR_BOTTOM = 56;
let reduce = false;
const window = {matchMedia: () => ({matches: reduce})};
const text = value => value == null ? '' : String(value);
class Element {
  constructor() { this.children = []; this.dataset = {}; this.attributes = {}; this.events = {}; this.hidden = false; }
  get firstElementChild() { return this.children[0]; }
  get lastElementChild() { return this.children.at(-1); }
  appendChild(child) { child.parentElement = this; this.children.push(child); }
  remove() { this.parentElement.children.pop(); }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { this.events[name] = callback; }
  removeEventListener(name) { delete this.events[name]; }
  closest() { return this; }
  focus() { this.focused = true; }
}
const document = {createElement: () => new Element()};
function fixture(height = 2000) {
  let scrollTop = Math.max(0, height - 400);
  const shell = new Element();
  const viewport = new Element();
  viewport.scrollHeight = height; viewport.clientHeight = 400;
  Object.defineProperty(viewport, 'scrollTop', {get: () => scrollTop, set: value => {scrollTop = Math.max(0, Math.min(value, viewport.scrollHeight - viewport.clientHeight));}});
  viewport.scrollTo = options => { viewport.lastScroll = options; viewport.scrollTop = options.top; };
  viewport.getBoundingClientRect = () => ({top: 100, height: 400});
  const content = new Element(); viewport.appendChild(content);
  const rail = new Element(); shell.appendChild(rail); const latest = new Element();
  content.messages = Array.from({length: 6}, (_, index) => ({
    classList: {contains: () => index % 2 === 0},
    surface: {textContent: index === 0 ? '<script>markup stays text</script>' : `Message ${index}`},
    getBoundingClientRect: () => ({top: 124 + index * 300 - viewport.scrollTop, height: 200}),
  }));
  return {viewport, content, rail, latest, shell};
}
let f;
function $(selector, root) {
  if (root?.surface) return root.surface;
  return selector.includes('conversation-scroll') ? f.viewport : selector.includes('__rail') ? f.rail : selector.includes('__latest') ? f.latest : null;
}
function $$(selector, root) { return root.messages; }
eval(controller + '\nglobalThis.enhance = enhanceMessageScroller;');
f = fixture();
let c = globalThis.enhance({});
assert.equal(c.following, true);
assert.equal(f.latest.hidden, true);
assert.equal(f.rail.hidden, false);
assert.match(f.rail.children[0].attributes['aria-label'], /tu · 1 di 6/);
assert.match(f.rail.children[0].firstElementChild.textContent, /<script>/);
assert.equal(f.rail.children.at(-1).attributes['aria-current'], 'true');
f.viewport.events.wheel({type:'wheel',deltaY:100}); flush(); assert.equal(c.following, true, 'Downward wheel at the end still follows');
f.viewport.events.touchstart({type:'touchstart'}); flush(); assert.equal(c.following, true, 'A tap without scrolling does not leave the live edge');
f.viewport.scrollHeight += 200; observers[0].callback(); flush();
assert.equal(f.viewport.scrollTop, 1800, 'Late content growth follows the live edge');
f.viewport.events.wheel({type:'wheel',deltaY:-100}); assert.equal(c.following,false); f.viewport.scrollTop = 300; f.viewport.events.scroll(); flush();
assert.equal(c.following, false); assert.equal(f.latest.hidden, false);
f.viewport.scrollHeight += 600; observers[1].callback(); flush();
assert.equal(f.viewport.scrollTop, 300, 'Reading history survives appended output');
const firstButton = f.rail.children[0];
f.content.messages.push({...f.content.messages[5], surface: {textContent:'New answer'}});
observers[1].callback(); flush();
assert.equal(f.rail.children.length, 7);
assert.equal(f.rail.children[0], firstButton, 'Growing output preserves focused controls');
const normalScrollTo = f.viewport.scrollTo;
f.viewport.scrollTop = f.viewport.scrollHeight;
f.viewport.scrollTo = options => { f.viewport.lastScroll = options; };
f.rail.events.click({target:firstButton});
f.viewport.events.scrollend();
observers[0].callback(); flush();
assert.equal(c.following, false, 'Stale initial scrollend must not cancel a rail jump');
f.viewport.scrollTo = normalScrollTo;
f.rail.events.click({target: firstButton}); flush();
assert.equal(f.viewport.lastScroll.behavior, 'smooth'); assert.equal(f.viewport.scrollTop, 0);
f.viewport.events.scrollend(); flush(); assert.equal(c.following, false);
reduce = true; f.rail.events.click({target: f.rail.children[2]}); flush();
assert.equal(f.viewport.lastScroll.behavior, 'instant');
f.viewport.events.touchstart(); assert.equal(c.following, false);
f.latest.events.click(); flush(); assert.equal(c.following, true); assert.equal(f.latest.hidden, true); assert.equal(f.viewport.focused, true);
f.viewport.scrollTop = 100; f.viewport.events.scroll(); flush();
f.viewport.scrollHeight += 200; observers[0].callback(); flush(); assert.equal(f.viewport.scrollTop, 100);
f.viewport.scrollTop = f.viewport.scrollHeight; f.viewport.events.scroll(); flush(); assert.equal(c.following, true);
f.rail.events.focusin({target:firstButton}); assert.match(f.shell.dataset.messagePreview, /markup stays text/);
f.rail.events.keydown({key:'Escape',type:'keydown'}); assert.equal(f.shell.dataset.messagePreview, undefined);
// Visibility-only streaming changes no text node or geometry. The observer
// catches the class toggle used by the word reveal, then refreshes the rail.
f.viewport.scrollTop = 100; f.viewport.events.scroll(); flush();
const streamingButton = f.rail.children[1];
assert.deepEqual(observers[1].observed[0].options.attributeFilter, ['class']);
f.content.messages[1].surface.innerText = 'First'; observers[1].callback([{type:'attributes',attributeName:'class'}]); flush();
assert.match(streamingButton.attributes['aria-label'], /First$/);
f.content.messages[1].surface.innerText = 'First complete answer'; observers[1].callback([{type:'attributes',attributeName:'class'}]); flush();
assert.match(streamingButton.attributes['aria-label'], /First complete answer$/);
f.rail.events.focusin({target:streamingButton});
assert.match(f.shell.dataset.messagePreview, /First complete answer$/);
assert.equal(f.viewport.scrollTop, 100, 'Preview refresh preserves history reading');
assert.equal(f.rail.children[1], streamingButton, 'Streaming preserves rail controls');
state.loading = true; observers[0].callback(); flush(); assert.equal(f.viewport.attributes['aria-busy'], 'true');
c.destroy(); assert.ok(observers.every(o => o.disconnected)); assert.equal(Object.keys(f.viewport.events).length, 0); assert.equal(scheduled.size, 0);
f = fixture(300); observers = []; c = globalThis.enhance({});
assert.equal(f.rail.hidden, true); assert.equal(f.latest.hidden, true); c.destroy();

console.log('PASS: message navigation, reader control, growth, reduced motion and teardown');
