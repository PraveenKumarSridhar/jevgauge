import test from 'node:test';
import assert from 'node:assert/strict';
import { createRouteController } from '../../desktop/controller.mjs';
import { createRoutedStart } from '../../desktop/routed-start.mjs';

function atom(value) {
  const listeners = new Set();
  return {get: () => value, subscribe(fn) {listeners.add(fn); fn(value); return () => listeners.delete(fn);},
    set(next) {value = next; for (const fn of listeners) fn(next);}, size: () => listeners.size};
}
function setup() {
  const state = {focusedSessionOwner: atom({connectionId: 'local', profile: 'a'}),
    focusedSessionId: atom('runtime'), focusedStoredSessionId: atom('durable'), connectionId: atom('local'), gateway: atom('connected')};
  const calls = [], updates = [];
  const routes = ['a', 'b'].map(profile => ({connectionId:'local', profile, targetProfile:profile, mode:'local'}));
  routes.push({connectionId:'remote', profile:'a', targetProfile:'a', mode:'remote'});
  const host = {state, profileRoutes: async () => routes, requestProfile(route, method, params) {
    let resolve, reject; const promise = new Promise((yes,no) => {resolve=yes; reject=no;});
    calls.push({route, method, params, resolve, reject}); return promise;
  }};
  const controller = createRouteController(host, value => updates.push(value));
  return {state,calls,updates,controller,routes,host};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const response = (overrides={}) => ({schema_version:'hermes.turn_route.binding.v1', session_id:'runtime', stored_session_id:'durable',
  evidence:'session_binding', status:'routed', model:'gpt-6-luna', provider:'openai-codex',
  requested_provider:'openai-codex', owner:'middleware', middleware_plugins:['jev-router'],
  reasoning_effort:'low', reasoning_owner:'middleware', middleware_reason:'economical/low', ...overrides});
const legacyResponse = (overrides={}) => ({schema_version:1, session_id:'runtime', stored_session_id:'durable',
  selection_api:1, telemetry_api:1, evidence:'session_binding', scope_supported:true, status:'routed', model:'gpt-6-luna',
  provider:'openai-codex', reasoning_effort:'low', owner:{model:'router',reasoning:'router'}, ...overrides});

test('uses exact connection-qualified route and displays selected pair', async () => {
  const t=setup(); await flush(); assert.equal(t.calls.length,1);
  assert.equal(t.calls[0].method,'session.turn_route.read');
  assert.deepEqual(t.calls[0].params,{session_id:'runtime',stored_session_id:'durable'});
  assert.equal(t.calls[0].route,t.routes[0]); t.calls[0].resolve(response()); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: gpt-6-luna · low'); t.controller.dispose();
});

test('routed start selects before create, submits before open, and persists no prompt', async () => {
  const calls=[];
  const route={connectionId:'local',profile:'default',targetProfile:'default',mode:'local'};
  const storage={value:[],get(_key,fallback){return this.value ?? fallback;},set(_key,value){this.value=value;}};
  const host={
    state:{focusedSessionOwner:{get:()=>({connectionId:'local',profile:'default'})}},
    profileRoutes:async()=>[route],
    retainProfile:async()=>{calls.push(['retain']);return()=>calls.push(['release']);},
    requestProfile:async(_route,method,params)=>{
      calls.push([method,params]);
      if(method==='model.options') return {model:'gpt-6-sol',provider:'openai-codex',providers:[{slug:'openai-codex',models:['gpt-6-luna','gpt-6-sol']}]};
      if(method==='config.get') return {value:'medium'};
      if(method==='session.create') return {session_id:'runtime-1',stored_session_id:'stored-1',info:{model:'gpt-6-sol'}};
      if(method==='prompt.submit') return {status:'streaming',user_row_id:1};
      throw new Error('unexpected');
    },
    openSession:async(id,options)=>calls.push(['open',id,options]),
  };
  const ctx={storage,rest:async(path,options)=>{
    calls.push(['route',path,options]);
    return {schema_version:'jevgauge.routed_start.v1',status:'routed',model:'gpt-6-sol',provider:'openai-codex',reasoning_effort:'high'};
  }};
  const result=await createRoutedStart(host,ctx,'private prompt');
  assert.equal(result.storedSessionId,'stored-1');
  assert.deepEqual(calls.map(call=>call[0]),['retain','model.options','config.get','route','session.create','prompt.submit','open','release']);
  assert.deepEqual(calls[4][1],{source:'desktop',model:'gpt-6-sol',provider:'openai-codex',reasoning_effort:'high'});
  assert.equal(calls[6][1],'stored-1');
  assert.deepEqual(calls[6][2],{route,awaitHydration:true,expectHistory:true});
  assert.equal(JSON.stringify(storage.value).includes('private prompt'),false);
});

test('routed start releases the retained route and does not persist on submit failure', async () => {
  const calls=[];
  const route={connectionId:'local',profile:'default',targetProfile:'default',mode:'local'};
  const storage={value:[],get(){return this.value;},set(_key,value){this.value=value;}};
  const host={state:{focusedSessionOwner:{get:()=>({connectionId:'local',profile:'default'})}},
    profileRoutes:async()=>[route],retainProfile:async()=>()=>calls.push('release'),
    requestProfile:async(_route,method)=>method==='model.options'
      ? {model:'gpt-6-sol',provider:'openai-codex',providers:[{slug:'openai-codex',models:['gpt-6-sol']}]}
      : method==='config.get' ? {value:'medium'}
      : method==='session.create' ? {session_id:'r',stored_session_id:'s',info:{}}
      : Promise.reject(new Error('submit failed')),
    openSession:async()=>calls.push('open')};
  const ctx={storage,rest:async()=>({schema_version:'jevgauge.routed_start.v1',status:'default'})};
  await assert.rejects(()=>createRoutedStart(host,ctx,'hello'),/submit failed/);
  assert.deepEqual(calls,['release']);
  assert.deepEqual(storage.value,[]);
});

test('routed start fails closed on ambiguous owner routes', async () => {
  const route={connectionId:'local',profile:'default',targetProfile:'default',mode:'local'};
  const host={state:{focusedSessionOwner:{get:()=>({connectionId:'local',profile:'default'})}},profileRoutes:async()=>[route,route]};
  const ctx={storage:{get:()=>[],set:()=>{}},rest:async()=>{throw new Error('must not select');}};
  await assert.rejects(()=>createRoutedStart(host,ctx,'hello'),/unique profile route/);
});

test('routed start refuses a focused profile that does not own plugin REST scope', async () => {
  const route={connectionId:'local',profile:'worker',targetProfile:'worker',mode:'local'};
  const host={state:{focusedSessionOwner:{get:()=>({connectionId:'local',profile:'worker'})},
    profile:{get:()=> 'default'},connectionId:{get:()=> 'local'}},profileRoutes:async()=>[route]};
  const ctx={storage:{get:()=>[],set:()=>{}},rest:async()=>{throw new Error('must not select');}};
  await assert.rejects(()=>createRoutedStart(host,ctx,'hello'),/Focus the target profile/);
});

test('stock host shows requested route, then only claims a matching live binding', async () => {
  const t=setup();t.controller.dispose();t.calls.length=0;t.updates.length=0;
  const storage={get:()=>[{connectionId:'local',profile:'a',runtimeId:'runtime',storedSessionId:'durable',
    status:'routed',model:'gpt-6-sol',provider:'openai-codex',reasoningEffort:'high'}]};
  const c=createRouteController(t.host,value=>t.updates.push(value),{timeoutMs:50,storage});
  await flush();t.calls[0].reject(new Error('missing'));await flush();t.calls[1].reject(new Error('missing'));await flush();
  assert.match(t.updates.at(-1).label,/requested gpt-6-sol/);
  c.observe({type:'session.info',session_id:'runtime',payload:{model:'gpt-6-sol',provider:'openai-codex',reasoning_effort_wire:'high'}});
  await flush();
  assert.equal(t.updates.at(-1).label,'Jev: gpt-6-sol · high');
  c.dispose();
});
test('focus change clears immediately and rejects late response even with identical IDs', async () => {
  const t=setup(); await flush(); t.calls[0].resolve(response()); await flush();
  t.controller.refresh(); await flush(); const old=t.calls.at(-1);
  t.state.focusedSessionOwner.set({connectionId:'remote',profile:'a'});
  assert.equal(t.updates.at(-1).label,'Jev: checking'); await flush();
  assert.equal(t.calls.at(-1).route,t.routes[2]); old.resolve(response({model:'foreign'})); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: checking');
  t.calls.at(-1).resolve(response({model:'remote-model'})); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: remote-model · low'); t.controller.dispose();
});
test('unknown ownership performs no request and removes previous route', async () => {
  const t=setup(); await flush(); t.calls[0].resolve(response()); await flush();
  t.state.focusedSessionOwner.set(null); assert.equal(t.updates.at(-1).label,'Jev: unavailable');
  const count=t.calls.length; t.controller.refresh(); await flush(); assert.equal(t.calls.length,count); t.controller.dispose();
});
test('rejects wrong durable identity and unsupported schema', async () => {
  for (const invalid of [{stored_session_id:'wrong'},{schema_version:'unknown'}]) {
    const t=setup(); await flush(); t.calls[0].resolve(response(invalid)); await flush();
    assert.equal(t.updates.at(-1).label,'Jev: unavailable'); t.controller.dispose();
  }
});
test('falls back to the legacy read RPC and never echoes errors', async () => {
  const t=setup(); await flush(); t.calls[0].reject(new Error('PRIVATE path token')); await flush();
  assert.equal(t.calls[1].method,'session.runtime_selection');
  t.calls[1].resolve(legacyResponse()); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: gpt-6-luna · low');
  assert.ok(!JSON.stringify(t.updates).includes('PRIVATE')); t.controller.dispose();
});
test('both missing RPCs are visible without leaking errors', async () => {
  const t=setup(); await flush(); t.calls[0].reject(new Error('PRIVATE first')); await flush();
  t.calls[1].reject(new Error('PRIVATE second')); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: unavailable');
  assert.ok(!JSON.stringify(t.updates).includes('PRIVATE')); t.controller.dispose();
});
test('dispose removes subscriptions and ignores pending results', async () => {
  const t=setup(); await flush(); t.controller.dispose(); const count=t.updates.length;
  t.calls[0].resolve(response()); await flush(); assert.equal(t.updates.length,count);
  for (const value of Object.values(t.state)) assert.equal(value.size(),0);
});
test('polls never overlap for the same identity, drafts cannot inherit a result', async () => {
  const t=setup(); await flush(); t.controller.refresh(); t.controller.refresh(); await flush();
  assert.equal(t.calls.length,1); t.state.focusedSessionId.set(null);
  assert.equal(t.updates.at(-1).label,'Jev: routed start ready'); t.calls[0].resolve(response()); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: routed start ready'); t.controller.dispose();
});


test('delayed descriptor lookup never dispatches for a previous focus', async () => {
  const t=setup(); await flush(); t.calls[0].resolve(response()); await flush();
  let resolveRoutes;
  t.host.profileRoutes=() => new Promise(resolve => {resolveRoutes=resolve;});
  t.controller.refresh(); await flush(); const old=resolveRoutes;
  t.state.focusedSessionOwner.set({connectionId:'remote',profile:'a'}); await flush();
  old(t.routes); await flush(); assert.equal(t.calls.length,1);
  resolveRoutes(t.routes); await flush(); assert.equal(t.calls.length,2);
  assert.equal(t.calls[1].route.connectionId,'remote'); t.controller.dispose();
});


test('descriptor timeout is visible and late lookup cannot issue an RPC', async () => {
  const state = {focusedSessionOwner:atom({connectionId:'local',profile:'a'}),
    focusedSessionId:atom('r'),focusedStoredSessionId:atom('s'),connectionId:atom('local'),gateway:atom('connected')};
  let resolveRoutes, calls=0; const updates=[];
  const controller=createRouteController({state,profileRoutes:() => new Promise(resolve => {resolveRoutes=resolve;}),
    requestProfile:() => {calls++;}}, value => updates.push(value), {timeoutMs:5});
  await new Promise(resolve => setTimeout(resolve,15));
  assert.equal(updates.at(-1).label,'Jev: unavailable');
  resolveRoutes([{connectionId:'local',profile:'a',targetProfile:'a'}]); await flush();
  assert.equal(calls,0); controller.dispose();
});


test('an event during an older read queues exactly one follow-up', async () => {
  const t=setup(); await flush(); t.controller.refresh(); t.controller.refresh();
  t.calls[0].resolve(response({status:'pending'})); await flush();
  assert.equal(t.calls.length,2); assert.equal(t.updates.at(-1).label,'Jev: choosing');
  t.calls[1].resolve(response()); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: gpt-6-luna · low');
  assert.equal(t.calls.length,2); t.controller.dispose();
});

test('native user ownership is shown as manual and unrelated middleware is not claimed as Jev', async () => {
  const t=setup(); await flush(); t.calls[0].resolve(response({status:'user',owner:'user'})); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: gpt-6-luna · low (manual)');
  t.controller.refresh(); await flush(); t.calls.at(-1).resolve(response({middleware_plugins:['other-router']})); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: unavailable'); t.controller.dispose();
});

test('native reasoning ownership is shown as manual independently of model ownership', async () => {
  const t=setup(); await flush();
  t.calls[0].resolve(response({reasoning_owner:'user'})); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: gpt-6-luna · low (manual)');
  t.controller.dispose();
});

test('a corrupt routed model cannot be presented as a healthy default', async () => {
  const t=setup(); await flush(); t.calls[0].resolve(response({model:null})); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: unavailable'); t.controller.dispose();
});
