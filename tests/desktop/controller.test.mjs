import test from 'node:test';
import assert from 'node:assert/strict';
import { createRouteController } from '../../desktop/controller.mjs';

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
  assert.equal(t.updates.at(-1).label,'Jev: new chat'); t.calls[0].resolve(response()); await flush();
  assert.equal(t.updates.at(-1).label,'Jev: new chat'); t.controller.dispose();
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
