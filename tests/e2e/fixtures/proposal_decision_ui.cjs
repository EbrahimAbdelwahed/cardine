const fs = require('fs');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../../src/cardine/demo/browser.js'), 'utf8');
const helpers = source.slice(source.indexOf('  function text('), source.indexOf('  function count('));
const renderer = source.slice(source.indexOf('  function renderProposte('), source.indexOf('  function renderProposal('));
const execute = source.slice(source.indexOf('  async function executeCommand('), source.indexOf('  function renderOptimisticTurn('));
let html;
const setView = (_, value) => { html = value; };
const renderProposal = item => `<article data-fixture-id="${item.revision_id}">${item.status}</article>`;
const emptyState = () => '<p>empty</p>';
const page = ({ body }) => body;
const state = {navigationVersion: 0, lastCommand: null, highWaterSequence: 10, pendingTurn: null, activityPollToken: 0};
let statusMessage = '';
function setStatus(_status, message) { statusMessage = message; }
function setBusy(_busy) {}
function commandPayload(payload, requestId) { return {...payload, request_id: requestId}; }
function requestId() { return 'fixture-request'; }
function updateSequence(value) { state.highWaterSequence = value; }
function statusLabel(status) { return status; }
function $(_selector) { return null; }
function $$(_selector) { return []; }
function loadRoute(route) { assert.equal(route, 'proposte'); state.navigationVersion += 1; statusMessage = 'Proposte · pronte'; return true; }
function refreshBootstrapCounts() { return Promise.resolve(); }
function fetchJson(endpoint) { assert.match(endpoint, /\/decisions$/); return {status:'committed', high_water_sequence:11, receipt:{status:'committed'}}; }
function dismissAlert() {}
function showCommandError() {}
function removeOptimisticTurn() {}
function restoreFailedTurnDraft() {}
const INCOMPLETE_TURN_STATUSES = new Set(); const MODEL_ERROR_MESSAGES = {};
(async () => {
  eval(helpers + renderer + execute + '\nglobalThis.runFixture = async () => { renderProposte({items:[{revision_id:"still-pending",status:"proposed",reviewable:true},{revision_id:"just-accepted",status:"accepted",reviewable:true},{revision_id:"just-rejected",status:"rejected",reviewable:true}]}); await executeCommand("/api/v1/artifacts/just-accepted/decisions", {decision:"accepted"}, {}, "proposte"); };');
  await globalThis.runFixture();
  const pendingList = html.match(/<div class="card-list">([\s\S]*?)<\/div>/)[1];
  assert.match(pendingList, /still-pending/);
  assert.doesNotMatch(pendingList, /just-accepted|just-rejected/, 'Decided cards must leave the proposals queue after refresh');
  assert.match(html, /just-accepted/,'Accepted cards remain available in history for recall enrollment');
  assert.match(statusMessage, /accettat/i, 'User must see acceptance confirmation after refresh');
  await eval(helpers + execute + '\nexecuteCommand("/api/v1/artifacts/decisions", {decisions:[{revision_id:"one",decision:"accepted"},{revision_id:"two",decision:"rejected"}]}, {}, "proposte")');
  assert.match(statusMessage, /1 flashcard accettata.*1 flashcard rifiutata/i, 'Bulk decisions need a visible result');
  console.log('PASS: accepted card leaves queue and user sees confirmation');
})().catch(error => { console.error(error); process.exitCode=1; });
