const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const extract = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const helpers = extract('  function text(', '  function count(') + extract('  function esc(', '  function statusLabel(');
const review = extract('  function reviewScope(', '  function renderPlan(');
const payload = extract('  function commandPayload(', '  /* ------------------------------------------------------------------ */');
const tick = () => new Promise(setImmediate);
function fixture() {
 const items = [1,2,3].map(n=>({revision_id:`r${n}`,front:`Question ${n}`,back:`Answer ${n}`}));
 const state = {bootstrap:{course:{id:'course'},session:{id:'session'}},route:'ripasso',viewData:null,highWaterSequence:10,revealedReviews:{r1:true}, review:{snapshot:null,pending:[],saving:false,error:null,scope:''}};
 const calls=[];let html='',renders=0,identities=0,refreshes=0;
 const context={SCHEMA_VERSION:1,state,root:{},Set,Object,Number,Boolean,JSON,encodeURIComponent,
  ROUTES:{ripasso:{endpoint:'/api/v1/recall/due'}},requestId:()=>`key-${++identities}`,
  $:()=>({focus:()=>{}}),setView:(_route,value)=>{html=value;renders++;},
  emptyState:(title,detail)=>`<h2>${title}</h2><p>${detail}</p>`,
  setStatus:()=>{},showAlert:()=>{},refreshBootstrapCounts:()=>{refreshes++;return Promise.resolve();},
  updateSequence:(n)=>{state.highWaterSequence=n;},
  fetchJson:(path,options={})=>new Promise((resolve,reject)=>calls.push({path,command:options.body&&JSON.parse(options.body),resolve,reject})),
 };
 vm.createContext(context);vm.runInContext(helpers+payload+review,context);
 // Drive the actual rating dispatch as well as the rendering and save path.
 const dispatcher=source.slice(source.indexOf('  function commandFromControl('),source.indexOf('\n  function ',source.indexOf('  function commandFromControl(')+10));
 vm.runInContext(dispatcher,context);
 const createCourse=source.slice(source.indexOf('  async function createChatCourse('),source.indexOf('\n  function ',source.indexOf('  async function createChatCourse(')+10));
 vm.runInContext(createCourse,context);
 const data={status:'ready',high_water_sequence:10,items};state.viewData=data;context.renderRipasso(data);
 const rate=(id,rating='good')=>{state.revealedReviews[id]=true;context.commandFromControl({dataset:{command:'review',revisionId:id,rating}});};
 const receipt=(seq,remaining)=>({status:'committed',high_water_sequence:seq,result:{status:remaining.length?'ready':'empty',high_water_sequence:seq,items:remaining}});
 return {context,state,calls,items,rate,receipt,get html(){return html;},get renders(){return renders;},get refreshes(){return refreshes;}};
}
(async()=>{
 const f=fixture();f.rate('r1');
 assert.match(f.html,/Question 2/);assert.match(f.html,/data-reveal-review="r2"/);
 assert.equal(f.calls.length,1);assert.equal(f.state.review.pending.length,1);
 f.rate('r1','easy');assert.equal(f.calls.length,1);
 f.rate('r2','hard');f.rate('r3','easy');assert.equal(f.calls.length,1);
 assert.match(f.html,/Salvataggio in corso/);assert.doesNotMatch(f.html,/Nessun ripasso dovuto/);
 assert.equal(f.state.review.pending.length,3);
 f.calls[0].resolve(f.receipt(12,f.items.slice(1)));await tick();
 assert.equal(f.calls[1].command.expected_sequence,12);assert.equal(f.calls[1].command.payload.rating,'hard');
 f.calls[1].resolve(f.receipt(14,f.items.slice(2)));await tick();
 assert.equal(f.calls[2].command.expected_sequence,14);assert.equal(f.calls[2].command.payload.rating,'easy');
 f.calls[2].resolve(f.receipt(16,[]));await tick();
 assert.match(f.html,/Nessun ripasso dovuto/);assert.equal(f.state.review.pending.length,0);assert.equal(f.refreshes,1);

 for(const status of [409,503]){
  const f=fixture();f.rate('r1','again');f.rate('r2');
  const first=f.calls[0].command;
  f.calls[0].reject(Object.assign(new Error('write not confirmed'),{status}));await tick();
  assert.equal(f.calls.length,1);assert.equal(f.state.review.pending.length,2);
  assert.match(f.html,/Question 1/);assert.match(f.html,/data-review-error/);
  assert.match(f.html,/data-rating="again" disabled/);
  f.rate('r1','easy');f.rate('r3');assert.equal(f.state.review.pending.length,2);
  f.context.recoverReviews();f.context.recoverReviews();assert.equal(f.calls.length,2);
  // Lost response can follow an actual commit: the due read excludes r1.
  f.calls[1].resolve({status:'ready',high_water_sequence:20,items:status===503?f.items.slice(1):f.items});await tick();
  assert.equal(f.calls.length,3);const retry=f.calls[2].command;
  assert.equal(retry.request_id,first.request_id);assert.equal(retry.payload.rating,first.payload.rating);assert.equal(retry.expected_sequence,20);
  f.calls[2].resolve(f.receipt(22,f.items.slice(1)));await tick();
  assert.equal(f.calls[3].command.expected_sequence,22);
  f.calls[3].resolve(f.receipt(24,f.items.slice(2)));await tick();
  assert.equal(f.state.review.pending.length,0);assert.match(f.html,/Question 3/);
  f.context.renderRipasso({status:'ready',high_water_sequence:10,items:f.items});
  assert.match(f.html,/Question 3/);assert.doesNotMatch(f.html,/Question 1/);
 }
 for (const status of ['unavailable', 'not_configured']) {
  const f=fixture(); f.rate('r1'); const original=f.calls[0].command;
  f.calls[0].reject(Object.assign(new Error('save failed'), {status:503})); await tick();
  f.context.recoverReviews();
  f.calls[1].resolve({status, high_water_sequence:20, items:[]}); await tick();
  f.calls[2].reject(Object.assign(new Error('still unavailable'), {status:503})); await tick();
  assert.match(f.html,/Ripasso non disponibile/);
  assert.match(f.html,/data-review-pending/); assert.match(f.html,/data-review-retry/);
  assert.equal(f.state.review.pending.length,1);
  f.context.recoverReviews();
  f.calls[3].resolve({status:'ready',high_water_sequence:22,items:f.items}); await tick();
  assert.equal(f.calls[4].command.request_id, original.request_id);
  f.calls[4].resolve(f.receipt(24,f.items.slice(1))); await tick();
  assert.equal(f.state.review.pending.length,0);
 }
 const rejected=fixture();rejected.rate('r1');rejected.calls[0].reject(Object.assign(new Error(),{status:409}));await tick();
 rejected.context.recoverReviews(true);rejected.calls[1].resolve({status:'ready',high_water_sequence:20,items:rejected.items.slice(1)});await tick();
 assert.equal(rejected.calls.length,2);assert.equal(rejected.state.review.pending.length,0);assert.match(rejected.html,/Question 2/);
 const ambiguous=fixture();ambiguous.rate('r1');ambiguous.calls[0].reject(Object.assign(new Error(),{status:503}));await tick();
 ambiguous.context.recoverReviews(true);assert.equal(ambiguous.calls.length,1);
 const committed=fixture();committed.rate('r1');committed.calls[0].reject(Object.assign(new Error(),{status:409,payload:{commandCommitted:true}}));await tick();
 assert.doesNotMatch(committed.html,/data-review-discard/);committed.context.recoverReviews(true);assert.equal(committed.calls.length,1);
 const malformed=fixture();malformed.rate('r1');malformed.calls[0].resolve({status:'committed',high_water_sequence:12});await tick();
 assert.equal(malformed.state.review.pending.length,1);assert.match(malformed.html,/data-review-error/);
 const n=fixture();n.rate('r1');n.state.route='fonti';const renders=n.renders;
 n.calls[0].resolve(n.receipt(12,n.items.slice(1)));await tick();assert.equal(n.renders,renders);assert.equal(n.state.route,'fonti');
 n.state.route='ripasso';n.context.renderRipasso(n.state.viewData);assert.match(n.html,/Question 2/);
 const scope=fixture();scope.rate('r1');assert.equal(scope.context.canLeaveReviewScope(),false);
 scope.state.bootstrap.session.id='different';scope.rate('r2');assert.equal(scope.state.review.pending.length,1);
 await scope.context.createChatCourse({elements:{namedItem:()=>{throw Error('must guard before reading the form');}}});
 assert.equal(scope.calls.length,1);
 assert.equal(scope.context.canLeaveReviewScope(scope.state.review.scope),true);
 assert.equal(scope.context.canLeaveReviewScope(JSON.stringify(['course','unrelated'])),false);
 console.log('Immediate transition, serialized ratings, duplicate suppression, conflict/lost-response retries, explicit rejection discard, stale reads, navigation and scope guards: passed');
})();
