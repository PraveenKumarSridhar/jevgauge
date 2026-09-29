// Pure routed-session action shared by the installed plugin and Node tests.
const MODEL = /^[a-zA-Z0-9_.:/-]{1,120}$/;
const EFFORTS = new Set(['none','minimal','low','medium','high','xhigh','max','ultra']);
const ATTRIBUTION_KEY = 'routedSessions';
const MAX_ATTRIBUTIONS = 64;
const MAX_PROMPT_CHARS = 12000;

function activeOwner(host) {
  const owner=host.state.focusedSessionOwner.get();
  const activeProfile=host.state.profile?.get?.();
  const activeConnection=host.state.connectionId?.get?.();
  if (!owner?.connectionId || !owner?.profile || activeProfile!==owner.profile || activeConnection!==owner.connectionId) {
    throw new Error('Focus the target profile before starting a routed chat.');
  }
  return {connectionId:owner.connectionId,profile:owner.profile};
}

function assertActiveRoute(host, route, intended) {
  const current=activeOwner(host);
  if (current.profile!==intended.profile || current.connectionId!==intended.connectionId ||
      route.profile!==intended.profile || route.connectionId!==intended.connectionId) {
    throw new Error('Focus the target profile before starting a routed chat.');
  }
}

function ownerRoute(host, routes, intended) {
  const matches=routes.filter(route => route?.connectionId===intended.connectionId && route?.profile===intended.profile && route?.targetProfile);
  if (matches.length!==1) throw new Error('Jev could not resolve one unique profile route.');
  assertActiveRoute(host,matches[0],intended);
  return matches[0];
}

function routePlan(value) {
  if (!value || value.schema_version!=='jevgauge.routed_start.v1' || !['routed','default'].includes(value.status)) {
    throw new Error('Jev returned an invalid routing plan.');
  }
  if (value.status==='default') return {schema_version:value.schema_version,status:'default',reason:String(value.reason || 'selection kept profile defaults').slice(0,240)};
  if (!MODEL.test(value.model || '') || value.provider!=='openai-codex' || !EFFORTS.has(value.reasoning_effort)) {
    throw new Error('Jev returned an unsupported model, provider, or reasoning effort.');
  }
  return {schema_version:value.schema_version,status:'routed',model:value.model,provider:value.provider,
    reasoning_effort:value.reasoning_effort,reason:String(value.reason || '').slice(0,240)};
}

function definiteRpcRejection(error) {
  return Number.isInteger(error?.code) || Number.isInteger(error?.cause?.code);
}

function persistAttribution(storage, record) {
  const current=storage.get(ATTRIBUTION_KEY,[]);
  const safe=Array.isArray(current) ? current.filter(item => item && typeof item==='object') : [];
  const deduped=safe.filter(item => !(item.connectionId===record.connectionId && item.profile===record.profile && item.storedSessionId===record.storedSessionId));
  storage.set(ATTRIBUTION_KEY,[record,...deduped].slice(0,MAX_ATTRIBUTIONS));
}

export function routedSessionAttribution(storage, owner, storedSessionId) {
  if (!owner?.connectionId || !owner?.profile || !storedSessionId) return null;
  const records=storage.get(ATTRIBUTION_KEY,[]);
  if (!Array.isArray(records)) return null;
  return records.find(item => item?.connectionId===owner.connectionId && item?.profile===owner.profile && item?.storedSessionId===storedSessionId) || null;
}

export async function createRoutedStart(host, ctx, rawPrompt) {
  const prompt=typeof rawPrompt==='string' ? rawPrompt : '';
  if (!prompt.trim()) throw new Error('Enter a first prompt.');
  if (prompt.length>MAX_PROMPT_CHARS) throw new Error(`First prompt must be ${MAX_PROMPT_CHARS.toLocaleString()} characters or fewer.`);
  // Capture the user's target synchronously at click time. Every later step is
  // addressed to this identity or fails if the ambient REST scope moved.
  const intended=activeOwner(host);
  const route=ownerRoute(host,await host.profileRoutes(),intended);
  const release=await host.retainProfile(route,{spawnPriority:'foreground'});
  try {
    const options=await host.requestProfile(route,'model.options',{explicit_only:false},5000,{spawnPriority:'foreground'});
    const reasoning=await host.requestProfile(route,'config.get',{key:'reasoning'},5000,{spawnPriority:'foreground'});
    const model=typeof options?.model==='string' ? options.model : '';
    const provider=typeof options?.provider==='string' ? options.provider : '';
    const providerRow=Array.isArray(options?.providers) ? options.providers.find(row => row?.slug===provider) : null;
    const unavailable=new Set(Array.isArray(providerRow?.unavailable_models) ? providerRow.unavailable_models : []);
    const eligibleModels=providerRow?.authenticated!==false && Array.isArray(providerRow?.models)
      ? providerRow.models.filter(item => typeof item==='string' && MODEL.test(item) && !unavailable.has(item)).slice(0,256) : [];
    // ctx.rest() captures the active UI profile synchronously. Revalidate after
    // the addressed RPCs so a profile switch cannot cross the two scopes.
    assertActiveRoute(host,route,intended);
    const plan=routePlan(await ctx.rest('/route',{method:'POST',body:{message:prompt,model,provider,
      reasoning_effort:reasoning?.value,eligible_models:eligibleModels},timeoutMs:12000}));
    const createParams={source:'desktop'};
    if (plan.status==='routed') Object.assign(createParams,{model:plan.model,provider:plan.provider,reasoning_effort:plan.reasoning_effort});
    const created=await host.requestProfile(route,'session.create',createParams,15000,{spawnPriority:'foreground'});
    const runtimeId=typeof created?.session_id==='string' ? created.session_id : '';
    const storedSessionId=typeof created?.stored_session_id==='string' ? created.stored_session_id : '';
    if (!runtimeId || !storedSessionId) throw new Error('Hermes did not return a durable session identity.');
    let submission='accepted';
    try {
      const submitted=await host.requestProfile(route,'prompt.submit',{session_id:runtimeId,text:prompt},15000,{spawnPriority:'foreground'});
      if (!submitted || !['streaming','queued','steered','redirected'].includes(submitted.status)) {
        throw Object.assign(new Error('Hermes did not accept the first prompt.'),{code:-32000});
      }
    } catch (error) {
      if (definiteRpcRejection(error)) throw error;
      // A transport failure after dispatch cannot prove non-acceptance. Keep
      // the known session and force inspection instead of enabling blind retry.
      submission='unknown';
    }
    try {
      persistAttribution(ctx.storage,{connectionId:route.connectionId,profile:route.profile,runtimeId,storedSessionId,
        status:plan.status,model:plan.model || null,provider:plan.provider || null,reasoningEffort:plan.reasoning_effort || null,
        reason:plan.reason || '',submission,createdAt:new Date().toISOString()});
    } catch {
      // Attribution is a display aid. A storage failure cannot cancel an accepted prompt.
    }
    let opened=true;
    try {
      await host.openSession(storedSessionId,{route,awaitHydration:true,expectHistory:true});
    } catch {
      // The first prompt is already accepted. A navigation failure must not
      // turn success into a retry that submits the prompt twice.
      opened=false;
    }
    return {runtimeId,storedSessionId,route,plan,submission,opened};
  } finally {
    release();
  }
}
