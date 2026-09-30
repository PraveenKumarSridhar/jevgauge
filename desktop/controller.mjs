// Pure controller shared by the installed single-file plugin and Node regressions.
const routeLabel = value => typeof value === 'string' && value.length <= 120 && /^[a-zA-Z0-9_.:/-]+$/.test(value) ? value : null;
const effortValues = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'];

export function createRouteController(host, publish, {timeoutMs=35000,storage=null}={}) {
  let disposed=false, generation=0, currentKey=null, pending=null, queued=false;
  const subscriptions=[];
  const timers=new Set();
  let confirmedKey=null, confirmedInfo=null;
  let initializing=true;
  const show=(label, detail) => { if (!disposed) publish({label,detail}); };
  const unavailable=() => show('Jev: unavailable','Routing integration could not be verified in this backend.');
  function attribution(captured) {
    if (!storage || !captured?.owner || !captured.stored) return null;
    const records=storage.get('routedSessions',[]);
    if (!Array.isArray(records)) return null;
    return records.find(item => item?.connectionId===captured.owner.connectionId && item?.profile===captured.owner.profile && item?.storedSessionId===captured.stored) || null;
  }
  function renderRoutedStart(captured) {
    if (!storage) {unavailable(); return;}
    const record=attribution(captured);
    if (!record) {show('Jev: unrouted','This conversation has no Jev route attribution. Use Start routed chat for a new routed conversation.'); return;}
    const info=confirmedKey===captured.key ? confirmedInfo : null;
    const requestedModel=routeLabel(record.model), requestedEffort=effortValues.includes(record.reasoningEffort) ? record.reasoningEffort : null;
    if (info) {
      const model=routeLabel(info.model), effort=effortValues.includes(info.reasoning_effort) ? info.reasoning_effort : null;
      const matches=record.status==='default' || (model===requestedModel && effort===requestedEffort && info.provider===record.provider);
      if (!matches) {show('Jev: route mismatch','Hermes reported a different live binding than Jev requested.'); return;}
      show(`Jev: ${model || 'default'}${effort ? ' · '+effort : ''}${record.status==='default' ? ' (default)' : ''}`,
        `Live session binding from a Jev routed start. Provider: ${routeLabel(info.provider) || 'unknown'}. Provider execution is not verified by this display.`);
      return;
    }
    if (record.submission==='unknown') {
      show('Jev: submission unknown','Hermes created this routed session, but the prompt acknowledgement was lost. Inspect this session before sending the prompt again.');
      return;
    }
    if (record.status==='routed' && requestedModel) {
      show(`Jev: requested ${requestedModel}${requestedEffort ? ' · '+requestedEffort : ''}`,
        'Jev supplied these values before Hermes created the session. Awaiting a matching live session.info event.');
    } else show('Jev: defaults requested','Jev abstained, so Hermes created this session with its profile defaults. Awaiting live binding confirmation.');
  }
  function identity() {
    const state=host.state;
    const owner=state.focusedSessionOwner.get();
    const runtime=state.focusedSessionId.get();
    const stored=state.focusedStoredSessionId.get();
    if (!owner?.connectionId || !owner?.profile) return {key:'unknown',unknown:true};
    return {key:JSON.stringify([owner.connectionId,owner.profile,runtime,stored]),
      owner:{...owner},runtime,stored};
  }

  function renderLegacy(data, captured) {
    if (!data || data.schema_version!==1 || data.session_id!==captured.runtime || data.stored_session_id!==captured.stored || data.selection_api!==1 || data.evidence!=='session_binding') {
      unavailable(); return;
    }
    if (data.scope_supported!==true) {show('Jev: unsupported profile','Automatic routing currently supports the gateway launch profile.'); return;}
    if (data.status==='selecting') {show('Jev: choosing','Selecting this conversation’s model and effort.'); return;}
    if (data.status!=='routed' && data.status!=='unrouted/default') {
      show('Jev: unrecorded','No routing decision recorded for this conversation.'); return;
    }
    const model=routeLabel(data.model), effort=effortValues.includes(data.reasoning_effort) ? data.reasoning_effort : null;
    if (!model) {show('Jev: unavailable','No valid effective model binding available.'); return;}
    const manual=data.owner?.model==='user' || data.owner?.reasoning==='user';
    const suffix=manual ? ' (manual)' : data.status==='unrouted/default' ? ' (default)' : '';
    show(`Jev: ${model}${effort ? ' · '+effort : ''}${suffix}`,
      `Live session binding${manual ? ' with manual override' : ''}. Provider: ${routeLabel(data.provider) || 'unknown'}. Provider execution is not verified by this display.`);
  }
  function renderNative(data, captured) {
    if (!data || data.schema_version!=='hermes.turn_route.binding.v1' || data.session_id!==captured.runtime ||
        data.stored_session_id!==captured.stored || data.evidence!=='session_binding') {
      unavailable(); return;
    }
    if (data.status==='pending') {show('Jev: choosing','Selecting this conversation’s model.'); return;}
    if (data.status==='unrecorded') {show('Jev: unrecorded','This conversation predates durable route bindings.'); return;}
    if (!['default','routed','user'].includes(data.status)) {unavailable(); return;}
    if (data.status!=='user' && (!Array.isArray(data.middleware_plugins) || !data.middleware_plugins.includes('jev-router'))) {
      unavailable(); return;
    }
    const model=routeLabel(data.model), effort=effortValues.includes(data.reasoning_effort) ? data.reasoning_effort : null;
    if (!model) {unavailable(); return;}
    const manual=data.status==='user' || data.owner==='user' || data.reasoning_owner==='user';
    const suffix=manual ? ' (manual)' : data.status==='default' ? ' (default)' : '';
    show(`Jev: ${model}${effort ? ' · '+effort : ''}${suffix}`,
      `Live session binding${manual ? ' with manual override' : ''}. Provider: ${routeLabel(data.requested_provider) || routeLabel(data.provider) || 'unknown'}. Provider execution is not verified by this display.`);
  }
  function render(data, captured) {
    if (data?.schema_version==='hermes.turn_route.binding.v1') renderNative(data,captured);
    else renderLegacy(data,captured);
  }
  function refresh() {
    if (disposed || initializing) return;
    let captured;
    try {captured=identity();} catch {captured={key:'unknown',unknown:true};}
    if (captured.key!==currentKey) {
      currentKey=captured.key; generation++; pending=null; queued=false; confirmedKey=null; confirmedInfo=null;
      if (captured.unknown) unavailable();
      else if (!captured.runtime || !captured.stored) show('Jev: routed start ready','Enter the first prompt in the Jev popover to create a routed session.');
      else show('Jev: checking','Reading this conversation’s routing state.');
    }
    if (captured.unknown || !captured.runtime || !captured.stored) return;
    if (pending!==null) {queued=true; return;}
    const requestGeneration=++generation;
    pending=requestGeneration;
    let expired=false, timeout;
    const deadline=new Promise((_,reject) => {
      timeout=setTimeout(() => {expired=true; reject(new Error('read timeout'));},timeoutMs);
      timers.add(timeout);
    });
    const read=Promise.resolve().then(async () => {
      const routes=await host.profileRoutes();
      if (disposed || expired || requestGeneration!==generation || identity().key!==captured.key) return null;
      const matches=routes.filter(route => route.connectionId===captured.owner.connectionId && route.profile===captured.owner.profile);
      if (matches.length!==1 || !matches[0].targetProfile) throw new Error('unresolved route');
      const params={session_id:captured.runtime,stored_session_id:captured.stored};
      try {
        return await host.requestProfile(matches[0],'session.turn_route.read',params,5000);
      } catch {
        if (disposed || expired || requestGeneration!==generation || identity().key!==captured.key) return null;
        return host.requestProfile(matches[0],'session.runtime_selection',params,5000);
      }
    });
    Promise.race([read,deadline])
      .then(data => {
        if (!disposed && requestGeneration===generation && identity().key===captured.key) render(data,captured);
      }).catch(() => {
        if (!disposed && requestGeneration===generation) renderRoutedStart(captured);
      }).finally(() => {clearTimeout(timeout); timers.delete(timeout); if (pending===requestGeneration) {
          pending=null;
          if (queued && !expired && !disposed) {queued=false; refresh();}
        }});
  }
  try {
    for (const name of ['focusedSessionOwner','focusedSessionId','focusedStoredSessionId','connectionId','gateway']) {
      const atom=host.state[name];
      if (name==='gateway' || name==='connectionId') {
        // Invalidate even if the IDs survive a reconnect to a replacement process.
        subscriptions.push(atom.subscribe(() => {currentKey=null; generation++; pending=null; confirmedKey=null; confirmedInfo=null; refresh();}));
      } else subscriptions.push(atom.subscribe(refresh));
    }
    initializing=false;
    refresh();
  } catch {
    initializing=false;
    for (const stop of subscriptions.splice(0)) stop();
    unavailable();
  }
  function observe(event) {
    if (event?.type==='session.info' && typeof event.session_id==='string' && event.payload && typeof event.payload==='object') {
      let captured;
      try {captured=identity();} catch {captured=null;}
      if (captured && event.connectionId===captured.owner.connectionId && event.profile===captured.owner.profile &&
          captured.runtime===event.session_id && event.payload.stored_session_id===captured.stored &&
          attribution(captured)?.runtimeId===event.session_id) {
        confirmedKey=captured.key;
        confirmedInfo={model:event.payload.model,provider:event.payload.provider,
          reasoning_effort:event.payload.reasoning_effort_wire || event.payload.reasoning_effort};
        renderRoutedStart(captured); return;
      }
    }
    refresh();
  }
  return {refresh,observe, dispose() {disposed=true; generation++; for (const timer of timers) clearTimeout(timer); timers.clear(); for (const stop of subscriptions.splice(0)) stop();}};
}
