/* V5 presentation. All accounting and production evidence come from the local API. */
'use strict';
(() => {
  const root = document.querySelector('#jg-v5');
  const q = selector => root.querySelector(selector);
  const all = selector => [...root.querySelectorAll(selector)];
  const esc = value => String(value ?? 'Unavailable').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = value => Number.isFinite(value) ? new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', minimumFractionDigits:value !== 0 && Math.abs(value) < .01 ? 4 : 2, maximumFractionDigits: value !== 0 && Math.abs(value) < .01 ? 4 : 2}).format(value) : 'Unavailable';
  const rateMoney = value => Number.isFinite(value) ? new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', minimumFractionDigits:2, maximumFractionDigits:20}).format(value) : 'Unavailable';
  const nice = value => String(value ?? 'Unavailable').replaceAll('_', ' ');
  const count = value => Number.isFinite(value) ? value.toLocaleString('en-US') : 'Unavailable';
  const state = {view:'overview', period:'month', project:'all', usage:'conversations', filter:null, selected:null, page:0, timezone:Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', start:null, end:null};
  let data = null, config = null, loading = true, error = '', sequence = 0, controller = null;
  let theme = 'system';
  try { theme = localStorage.getItem('jevgauge.appearance') || 'system'; } catch { /* Storage is optional. */ }
  const titles = {overview:'How much did routing save?',decisions:'Why was this route chosen?',reliability:'What needs attention?',jev:'What does Jev cost?',configuration:'How should Jev choose?'};
  const help = {
    savings:['Estimated savings versus default','The comparison reprices the same known provider usage against captured historical defaults. Only eligible, fully priced, non-manual requests enter the shared comparison cohort. When overhead is unavailable, this is a provider-only difference, not net savings. Task quality and subscription bill reductions are unmeasured.'],
    comparison:['Default versus routed cost','Both amounts use the same comparison request cohort. Routed provider cost includes known retries and fallback requests in that cohort. Overhead is shown separately in Details; unmeasured overhead is never assumed to be zero.'],
    trend:['Reading cumulative savings','The chart accumulates the provider-only difference from the start of the selected period. Above the dashed zero line means the routed provider estimate costs less; below zero means it costs more. Provider break-even excludes Jev overhead. Missing evidence is excluded.'],
    routed:['Classified and routed','The percentage counts conversations with an accepted routing selection. It does not mean the first provider request was accepted or the task succeeded. Decisions shows captured attempts, rejections and fallbacks.'],
    review:['What needs review','The queue counts distinct conversations with technical problems or missing evidence. Each conversation counts once even if multiple flags overlap. Confidence-only abstentions and disabled routing are excluded unless another actionable issue exists.'],
    usage:['Conversations versus provider requests','Conversations count each conversation once at its final requested model. Conversations without a captured request appear as Unavailable. Provider requests count actual captured outgoing attempts, including retries, rejections and fallbacks. Missing attempt telemetry is unavailable. Requested effort is not provider-confirmed effort.'],
    confidence:['Confidence gate','Both model and effort scores must be finite and within the configured acceptance interval [0.55, 1]. Otherwise Hermes retains defaults. This is a routing safeguard, not a task-success probability. Historical confidence scores are unavailable.'],
    evidence:['Execution and evidence','These counts describe captured technical routing events and telemetry coverage. Unknown cached-input breakdown prevents reliable pricing even if total input and output are known. Request-count coverage does not bound missing dollars. A completed request is not proof of task quality or subscription savings.'],
    jev:['Jev overhead','Only captured measured overhead supports net savings. Without complete overhead evidence, net savings is unavailable. Explicit demo mode contains synthetic example overhead and does not establish any actual charge.']
  };
  const info = key => `<button type="button" class="j-info" data-help="${key}" aria-label="Explain ${esc(help[key][0].toLowerCase())}" aria-expanded="false" aria-controls="j-help-panel"><span aria-hidden="true">ⓘ</span></button>`;
  const rows = pairs => pairs.map(([label,value])=>`<div class="j-evidence-row"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
  function setTheme() {
    root.style.colorScheme = ['light','dark'].includes(theme) ? theme : 'light dark';
    const active = theme === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : theme;
    all('[data-theme]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.theme === active)));
    document.documentElement.style.colorScheme = root.style.colorScheme;
  }
  function closeDates(focus=false) { const open=!q('#j-date-range').hidden; q('#j-date-range').hidden=true; q('#j-dates-toggle').setAttribute('aria-expanded','false'); if(open&&focus)q('#j-dates-toggle').focus(); }
  function closeHelp(focus=false) { const trigger=q('[data-help][aria-expanded="true"]'); q('#j-help-panel').hidden=true; all('[data-help]').forEach(b=>b.setAttribute('aria-expanded','false')); if(focus&&trigger)trigger.focus(); }
  function showHelp(button) {
    const wasOpen=button.getAttribute('aria-expanded')==='true'; closeHelp(); if(wasOpen)return;
    const [title,copy]=help[button.dataset.help], panel=q('#j-help-panel');
    panel.innerHTML=`<button aria-label="Close explanation" type="button" id="j-help-close">×</button><strong>${esc(title)}</strong><p>${esc(copy)}</p>`;
    panel.hidden=false; button.setAttribute('aria-expanded','true');
    const r=root.getBoundingClientRect(), b=button.getBoundingClientRect(); panel.style.left=Math.max(10,Math.min(b.left-r.left,root.clientWidth-panel.offsetWidth-10))+'px'; panel.style.top=b.bottom-r.top+8+'px';
  }
  function dateOnly(date) {return new Intl.DateTimeFormat('en-CA',{timeZone:state.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(date);}
  function setPeriod(period, reference) {
    const end=reference || dateOnly(new Date()); const d=new Date(end+'T12:00:00Z');
    if(period==='week')d.setUTCDate(d.getUTCDate()-((d.getUTCDay()+6)%7));
    if(period==='month')d.setUTCDate(1);
    if(period==='year'){d.setUTCMonth(0);d.setUTCDate(1);}
    state.period=period; state.start=d.toISOString().slice(0,10); state.end=end;
  }
  function validDate(value) {return /^\d{4}-\d{2}-\d{2}$/.test(value)&&value.slice(0,4)!=='0000'&&!Number.isNaN(Date.parse(value))&&new Date(value+'T12:00:00Z').toISOString().slice(0,10)===value;}
  function renderChrome() {
    q('#j-title').textContent=titles[state.view];
    q('#j-date').textContent=state.view==='configuration'?'Profile settings':`${state.start || 'All captured dates'}${state.end ? ' through '+state.end : ''} · ${state.timezone}`;
    all('[role="tab"]').forEach(b=>{b.setAttribute('aria-selected',String(b.dataset.view===state.view));b.tabIndex=b.dataset.view===state.view?0:-1;});
    q('#j-config-entry').setAttribute('aria-pressed',String(state.view==='configuration'));
    all('[data-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.period===state.period)));
    for(const view of Object.keys(titles)) q('#j-'+view).hidden=view!==state.view || (view!=='configuration'&&(loading||!!error||!data));
    q('#j-metrics').hidden=['configuration','jev'].includes(state.view)||loading||!!error||!data;
    q('#j-global-footer').hidden=state.view==='configuration'||loading||!!error||!data;
    q('.j-controls').hidden=state.view==='configuration';
    q('#j-status').innerHTML=error ? `${esc(error)} <button type="button" class="j-textbutton" id="j-retry">Retry</button>` : loading ? 'Loading captured evidence…' : '';
    root.setAttribute('aria-busy',String(loading));
  }
  async function load(initial=false) {
    controller?.abort(); controller=new AbortController(); const current=++sequence;
    loading=true; error=''; renderChrome();
    const params=new URLSearchParams({timezone:state.timezone});
    if(state.start)params.set('start',state.start); if(state.end)params.set('end',state.end); if(state.project!=='all')params.set('project',state.project);
    try {
      const response=await fetch('/api/dashboard?'+params,{signal:controller.signal,cache:'no-store'});
      if(!response.ok){
        const failure=await response.json().catch(()=>null), issue=new Error('request');
        if(typeof failure?.error==='string'&&failure.error.trim()&&failure.error.length<=512)issue.displayMessage=failure.error;
        throw issue;
      }
      const result=await response.json(); if(!result.summary||!Array.isArray(result.conversations))throw new Error('shape'); if(current!==sequence)return;
      data=result;
      if(initial){setPeriod('month', result.mode==='demo'&&result.as_of ? dateOnly(new Date(result.as_of.length===10?result.as_of+'T12:00:00Z':result.as_of)) : undefined); return load();}
      loading=false; render();
    } catch(e) { if(e.name==='AbortError'||current!==sequence)return; loading=false; error='Could not load captured evidence. '+(e.displayMessage||'Check the local dashboard service and retry.'); renderChrome(); }
  }
  function priceHTML() {
    const p=data.pricing || {}; const sources=Array.isArray(p.sources)?p.sources:[];
    const links=sources.map(source=>{const item=typeof source==='string'?{url:source}:source; let url;try {url=new URL(item.url);}catch{return '';}return url.protocol==='https:'?`<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${esc(item.title||item.provider||url.hostname)}</a>`:'';}).filter(Boolean);
    return `<p>Price book: ${esc(p.version || 'Unavailable')}. USD only. ${esc(p.units||'Published rates per million tokens')}.</p><p>${links.join(' · ') || 'No published pricing source available.'}</p>${Object.keys(p.rates||{}).length?`<table class="j-price-table"><caption>USD per 1M tokens</caption><thead><tr><th>Model</th><th>Input</th><th>Cached</th><th>Output</th></tr></thead><tbody>${Object.entries(p.rates).map(([model,r])=>`<tr><td>${esc(model)}</td><td>${rateMoney(r.input)}</td><td>${rateMoney(r.cached_input)}</td><td>${rateMoney(r.output)}</td></tr>`).join('')}</tbody></table>`:''}${p.conditions?`<p>${Array.isArray(p.conditions)?p.conditions.map(c=>esc(c)).join('</p><p>'):esc(p.conditions)}</p>`:''}`;
  }
  function render() {
    renderChrome(); if(!data)return;
    closeHelp(); const s=data.summary, c=data.coverage, list=data.conversations;
    q('#j-mode').textContent=data.mode==='demo'?'DEMO':'LIVE';
    q('#j-provenance').textContent=data.mode==='demo'?'Synthetic data · API equivalents, not bill savings':'Captured evidence · API equivalents, not bill savings';
    const options=data.project_options || data.projects.map(p=>p.project);
    const names=[...new Set(options.map(p=>typeof p==='string'?p:p.project).filter(Boolean))]; if(state.project!=='all'&&!names.includes(state.project))names.push(state.project);
    q('#j-project').innerHTML='<option value="all">All projects</option>'+names.map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join(''); q('#j-project').value=state.project;
    const net=Number.isFinite(s.net_savings_usd), amount=net?s.net_savings_usd:s.provider_difference_usd;
    q('#j-metrics').innerHTML=`<div class="j-metric"><span class="j-label">${net?'Estimated net savings':'Provider-only difference'} vs default ${info('savings')}</span><strong class="j-number">${s.conversation_count?money(amount):'No data'} <span class="j-currency">USD</span></strong><span class="j-small">${net?'Captured overhead included':s.overhead_available?'Incomplete comparison cohort; net savings unavailable':Number.isFinite(s.overhead_usd)?'Partial Jev overhead; net savings unavailable':'Jev overhead unavailable; net savings unavailable'}</span><p class="j-savings-caveat">Same-usage price scenario. Task quality not evaluated.</p></div><div class="j-metric"><span class="j-label">Conversations</span><strong class="j-number">${count(s.conversation_count)}</strong><span class="j-small">${count(s.request_count)} captured provider requests</span></div><div class="j-metric"><span class="j-label">Classified &amp; routed ${info('routed')}</span><strong class="j-number">${s.conversation_count?(s.routed_count/s.conversation_count*100).toFixed(1)+'%':'No data'}</strong><span class="j-small">${count(s.routed_count)} of ${count(s.conversation_count)} conversations</span></div><div class="j-metric attention"><div class="j-label">Review queue ${info('review')}</div><button type="button" data-cause="attention" aria-label="Inspect review queue"><strong class="j-number">${count(s.review_count)}</strong><span class="j-small">Technical issues or missing evidence</span></button></div>`;
    q('#j-cumulative-title').innerHTML='Cumulative provider difference '+info('trend'); q('#j-cumulative-unit').textContent='USD · before Jev';
    q('#j-cumulative-legend').innerHTML='<span><i class="j-swatch"></i>Provider difference vs default</span><span><i class="j-swatch default"></i>Provider break-even</span>';
    q('#j-savings-bridge').innerHTML=`<div><span>Captured default ${info('comparison')}</span><strong>${money(s.default_cost_usd)}</strong></div><div><span>Routed provider cost</span><strong>${money(s.routed_cost_usd)}</strong></div><button type="button" class="j-textbutton" id="j-calculation-toggle" aria-expanded="${!q('#j-calculation').hidden}" aria-controls="j-calculation">Details</button>`;
    q('#j-equation').innerHTML=`${money(s.default_cost_usd)} default − ${money(s.routed_cost_usd)} routed provider = ${money(s.provider_difference_usd)} provider difference. Jev overhead: ${money(s.overhead_usd)}. Net savings: ${money(s.net_savings_usd)}.<p>${count(c.comparison_requests)} requests share the comparison cohort; ${count(c.total_requests-c.comparison_requests)} excluded. All observed priced attempts: ${money(s.provider_cost_usd)} (includes manual usage where priced).</p>${priceHTML()}`;
    q('#j-baselines').innerHTML=[['Default',s.default_cost_usd],['Median eligible',s.median_cost_usd],['Highest capability',s.strongest_cost_usd]].map(([name,value])=>`<div><span>${name}</span><strong>${money(value)}</strong></div>`).join('');
    q('#j-outcome-summary').innerHTML=[['Routed',s.routed_count],['Abstained',s.abstained_count],['Disabled',s.disabled_count ?? 0],['Failed',s.routing_failed_count ?? 0]].map(([label,n])=>`<div><strong>${count(n)}</strong>${label}</div>`).join('');
    q('#j-outcome-strip').innerHTML=[['',s.routed_count],['j-skipped',s.abstained_count],['j-skipped',s.disabled_count ?? 0],['j-failed',s.routing_failed_count ?? 0]].map(([cls,n])=>`<span class="${cls}" style="width:${s.conversation_count?100*n/s.conversation_count:0}%"></span>`).join('');
    q('#j-outcome-strip').setAttribute('aria-label',`${s.routed_count} routed, ${s.abstained_count} abstained, ${s.disabled_count ?? 0} disabled, ${s.routing_failed_count ?? 0} routing failures`);
    q('#j-project-rows').innerHTML=data.projects.length?data.projects.map(p=>`<tr><td><button type="button" data-project="${esc(p.project)}">${esc(p.project)}</button></td><td>${count(p.conversations)}</td><td>${money(p.provider_difference_usd)}</td><td>${count(p.requests)}</td></tr>`).join(''):'<tr><td colspan="4">No projects in this selection</td></tr>';
    q('#j-coverage').textContent=`${c.priced_requests}/${c.total_requests} requests priced · ${c.missing_usage_requests} missing usage · ${c.missing_usage_breakdown_requests ?? 0} missing cache breakdown · ${c.missing_price_requests} missing prices · ${c.comparison_requests} in comparison. Request coverage does not bound missing dollars.`;
    q('#j-source-mode').textContent=data.mode==='demo'?'Explicit demo mode: all events are synthetic and isolated from production.':'Live mode: captured routing and provider telemetry only. No historical evidence is inferred.';
    q('#j-price-source').innerHTML=priceHTML(); q('#j-limitations').innerHTML=(data.limitations||[]).map(l=>`<p>${esc(l)}</p>`).join('');
    renderDecisions(); renderReliability(); renderJev(); drawCharts();
    if(!s.conversation_count){q('#j-savings-bridge').innerHTML='';q('#j-calculation').hidden=true;q('#j-jev-total').textContent='No data';q('#j-jev-economics').innerHTML='<p class="j-empty">No captured activity in this selection. Cost comparisons are unavailable.</p>';}
  }
  function renderDecisions() {
    const list=data.conversations.filter(r=>!state.filter||(state.filter==='attention'?(r.review_reasons||[]).length:(r.reason_code||r.outcome)===state.filter)).sort((a,b)=>String(b.timestamp).localeCompare(String(a.timestamp)));
    const pages=Math.max(1,Math.ceil(list.length/7)); state.page=Math.min(state.page,pages-1); const visible=list.slice(state.page*7,state.page*7+7);
    if(!visible.some(r=>r.id===state.selected))state.selected=visible[0]?.id;
    q('#j-filter-label').textContent=`${list.length} decisions${state.filter?' · '+nice(state.filter==='attention'?'Review queue':state.filter):''}`; q('#j-clear-filter').hidden=!state.filter;
    q('#j-decision-list').innerHTML=visible.length?visible.map(r=>`<button type="button" class="j-decision" data-id="${esc(r.id)}" aria-pressed="${r.id===state.selected}"><span class="j-decision-top"><span>${esc(r.project||'Unattributed')}</span><span>${esc(r.timestamp?.slice(0,10))}</span></span><span class="j-preview">Conversation ${esc(r.id)}</span><span class="j-decision-bottom"><span>${esc(r.selected_model)} / ${esc(r.requested_effort)}</span><span class="j-badge ${(r.review_reasons||[]).length?'warn':''}">${esc(nice(r.outcome))}</span></span>${r.review_reasons?.length?`<span class="j-review-reason">Review: ${esc(r.review_reasons.map(nice).join('; '))}</span>`:''}</button>`).join(''):'<p class="j-empty">No decisions match these filters.</p>';
    q('#j-page-label').textContent=list.length?`${state.page*7+1}–${Math.min((state.page+1)*7,list.length)} of ${list.length}`:'0 decisions'; q('#j-prev').disabled=state.page===0; q('#j-next').disabled=state.page>=pages-1;
    const r=list.find(r=>r.id===state.selected); if(!r){q('#j-inspector').innerHTML='<p class="j-empty">No matching decisions.</p>';return;}
    const attempts=r.attempts||[], last=attempts.at(-1)||{};
    q('#j-inspector').innerHTML=`<div class="j-inspector-head"><h2>${esc(r.project||'Unattributed')}</h2><span class="j-small">${esc(r.timestamp?.slice(0,10))}</span></div>${r.review_reasons?.length?`<p class="j-inspector-review"><strong>Why this needs review</strong><br>${esc(r.review_reasons.map(nice).join('; '))}</p>`:''}<div class="j-prompt-text">Prompt text is not retained. No generated classifier reasoning or historical confidence score is available.</div><div class="j-lifecycle">${[['01 · Captured default',r.default_model],['02 · Classifier selection',`${r.selected_model||'Unavailable'} / ${r.requested_effort||'Unavailable'}`],['03 · Actual final request',last.requested_model],['04 · Provider reported',last.reported_model]].map(([label,value])=>`<div class="j-step"><span>${label}</span><strong>${esc(value)}</strong></div>`).join('')}</div><dl class="j-inspector-dl">${[['Model tier',r.model_tier],['Effort tier',r.effort_tier],['Captured reason code',r.reason_code],['Policy version',r.policy_version],['Selector latency',Number.isFinite(r.latency_ms)?r.latency_ms+' ms':'Unavailable'],['Provider reported effort',last.reported_effort],['Manual override',r.manual_override?'Yes':'No captured override'],['Known provider estimate',money(r.provider_cost_usd)]].map(([label,value])=>`<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl><details class="j-note"><summary>Eligible models and baseline provenance</summary><p>${esc((r.eligible_models||[]).map(m=>`${m.model} (configured capability ${m.capability})`).join(', ')||'Unavailable')}</p><p>Historical eligibility, not today’s catalog. Configured capability is not measured quality.</p></details><div class="j-attempts"><h3>Actual provider attempts (${attempts.length})</h3>${attempts.length?attempts.map((a,i)=>`<div class="j-attempt"><strong>${i+1}. ${esc(a.requested_model)} · ${esc(a.status)}</strong><p class="j-small">Requested effort: ${esc(a.requested_effort)}. Reported model: ${esc(a.reported_model)}. Reported effort: ${esc(a.reported_effort)}.${a.fallback?' Fallback.':''}${a.manual_override?' Manual override.':''}</p><p class="j-small">${a.usage?`Provider usage: input ${count(a.usage.input_tokens)} (cached ${count(a.usage.cached_input_tokens)}), output ${count(a.usage.output_tokens)} (reasoning ${count(a.usage.reasoning_tokens)}).`:'Usage unavailable.'}</p></div>`).join(''):'<p class="j-small">No provider attempt evidence.</p>'}</div>`;
  }
  function causes() {const groups=new Map(); data.conversations.filter(r=>r.outcome&&r.outcome!=='routed').forEach(r=>groups.set(r.reason_code||r.outcome,(groups.get(r.reason_code||r.outcome)||0)+1));return [...groups].map(([key,n])=>({key,label:nice(key),n}));}
  function renderReliability() {
    q('#j-reliability h2').innerHTML='Why routing kept your defaults '+info('confidence'); q('#j-reliability .j-reliability-grid>section:nth-child(2)>h2').innerHTML='Execution and evidence '+info('evidence');
    const s=data.summary,c=data.coverage;
    q('#j-queue-explanation').innerHTML=`<strong>${count(s.review_count)} distinct conversations need review.</strong><p>Overlapping technical and evidence flags count once.</p><button type="button" class="j-textbutton" data-cause="attention">Inspect review queue</button>`;
    const latency=data.conversations.map(r=>r.latency_ms).filter(Number.isFinite).sort((a,b)=>a-b);const mid=Math.floor(latency.length/2),median=latency.length?(latency.length%2?latency[mid]:(latency[mid-1]+latency[mid])/2):null;
    q('#j-evidence').innerHTML=rows([['Routing failures',count(s.routing_failed_count ?? 0)],['Abstentions',count(s.abstained_count)],['Provider rejections',count(data.conversations.reduce((n,r)=>n+(r.attempts||[]).filter(a=>a.status==='rejected').length,0))],['Missing usage',count(c.missing_usage_requests)+' requests'],['Missing cache breakdown',count(c.missing_usage_breakdown_requests ?? 0)+' requests'],['Missing prices',count(c.missing_price_requests)+' requests'],['Manual requests',count(c.manual_requests)],['Selector latency, median',median===null?'Unavailable':median.toFixed(0)+' ms'],['Quality impact','Not evaluated'],['Subscription / quota savings','Not measured']]);
    q('#j-botlist').innerHTML='<div class="j-botrow"><h2>Bot routing is unsupported</h2><p class="j-small">Invocation coverage and cost are unknown, not zero.</p></div>';
  }
  function renderJev() {
    const s=data.summary;
    q('#j-jev-label').innerHTML=(data.mode==='demo'?'Synthetic example overhead':s.overhead_available?'Captured Jev overhead':'Incomplete Jev overhead')+' '+info('jev');
    q('#j-jev-total').textContent=money(s.overhead_usd)+(Number.isFinite(s.overhead_usd)&&!s.overhead_available?' known subtotal':''); q('#j-jev-rate').textContent=s.overhead_available?(data.mode==='demo'?'Demo evidence only. No real charge is implied.':'Captured measured overhead. No per-call charge is inferred.'):'Overhead evidence is incomplete. Net savings is unavailable.';
    q('#j-jev-stats').innerHTML=[['Conversations',s.conversation_count],['Accepted routes',s.routed_count],['Failed routes',s.routing_failed_count ?? 0]].map(([label,n])=>`<div><strong>${count(n)}</strong><span class="j-small">${label}</span></div>`).join('');
    q('#j-jev-breakdown').innerHTML='<tr><td colspan="3">Per-call billing breakdown unavailable. No costs are inferred from outcomes.</td></tr>';
    q('#j-jev-economics').innerHTML=rows([['Provider-only difference',money(s.provider_difference_usd)],['Captured Jev overhead',money(s.overhead_usd)],['Net savings',money(s.net_savings_usd)],['Actual billed provider charges',money(s.billed_cost_usd)],['Task quality','Not evaluated']]);
  }
  function svgText(x,y,text,attrs=''){return `<text x="${x}" y="${y}" ${attrs}>${esc(text)}</text>`;}
  function barChart(id,items,height=166) {
    const node=q(id), w=Math.max(230,node.clientWidth), left=Math.min(130,w*.42), right=42, max=Math.max(1,...items.map(d=>d.n)), slot=Math.min(40,(height-28)/Math.max(1,items.length));
    let content=items.length?'':svgText(w/2,65,'No captured evidence','text-anchor="middle"');
    items.forEach((d,i)=>{const y=10+i*slot, width=(w-left-right)*d.n/max;content+=svgText(left-8,y+13,d.label.length>20?d.label.slice(0,18)+'…':d.label,'text-anchor="end"')+`<rect x="${left}" y="${y}" width="${width}" height="18" rx="2" fill="${id==='#j-models'?'var(--j-accent)':'var(--j-warning)'}"><title>${esc(d.label)}: ${d.n}</title></rect>`+svgText(left+width+5,y+13,count(d.n));});
    node.innerHTML=`<svg viewBox="0 0 ${w} ${height}" role="img" aria-label="${esc(items.map(d=>d.label+': '+d.n).join(', ')||'No captured evidence')}">${content}</svg>`;node.style.height=height+'px';
  }
  function drawCharts() {
    if(state.view==='overview') {
      const modelItems=data.models.map(m=>({label:m.model||'Unavailable',n:m[state.usage]||0})).sort((a,b)=>b.n-a.n); if(modelItems.length>5){const remaining=modelItems.splice(4);modelItems.push({label:'Other models ('+remaining.length+')',n:remaining.reduce((n,m)=>n+m.n,0)});}
      barChart('#j-models',modelItems,166); q('#j-model-title').innerHTML='Model usage '+info('usage');
      all('[data-usage]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.usage===state.usage)));
      q('#j-model-denominator').textContent=state.usage==='requests'?`${data.summary.request_count} captured provider requests, including retries and fallbacks.`:`${data.summary.conversation_count} conversations, each counted once by final requested model; absent requests appear as Unavailable.`;
      // No effort shading is fabricated where the aggregate API has no effort breakdown.
      q('#j-models').previousElementSibling.innerHTML='<span><i class="j-swatch"></i>Captured model counts · effort details in Decisions</span>';
      drawTrend(); barChart('#j-failures',causes().map(d=>({...d,label:nice(d.key)})),154);
      q('#j-failure-actions').innerHTML=causes().map(d=>`<button type="button" class="j-textbutton" data-cause="${esc(d.key)}">${esc(d.label)}</button>`).join('');
    }
    if(state.view==='reliability'){barChart('#j-reliability-chart',causes(),260);q('#j-reliability-actions').innerHTML=causes().map(d=>`<button type="button" class="j-textbutton" data-cause="${esc(d.key)}">${esc(d.label)}</button>`).join('');}
  }
  function drawTrend() {
    const node=q('#j-trend'),w=Math.max(230,node.clientWidth),h=232,left=48,right=w-18,top=20,bottom=195;
    const series=data.series.filter(p=>Number.isFinite(p.provider_difference_usd));let running=0;const points=[{date:state.start,value:0},...series.map(p=>({date:p.date,value:(running+=p.provider_difference_usd)}))];
    if(!data.summary.conversation_count||!series.length){node.innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="No cost evidence">${svgText(w/2,110,data.summary.conversation_count?'No comparable priced usage':'No conversations in this range','text-anchor="middle"')}</svg>`;return;}
    const low=Math.min(0,...points.map(p=>p.value)),high=Math.max(0,...points.map(p=>p.value)),span=high-low||1;
    const y=value=>bottom-(value-low)/span*(bottom-top), minDate=Date.parse(state.start||points[0].date),maxDate=Date.parse(state.end||points.at(-1).date), dateSpan=maxDate-minDate||86400000;
    const x=p=>left+Math.min(1,Math.max(0,(Date.parse(p.date)-minDate)/dateSpan))*(right-left);
    let content='';for(let i=0;i<4;i++){const value=low+span*i/3,Y=y(value);content+=`<line x1="${left}" x2="${right}" y1="${Y}" y2="${Y}" stroke="var(--j-line)"/>`+svgText(left-6,Y+4,money(value),'text-anchor="end"');}
    content+=`<line x1="${left}" x2="${right}" y1="${y(0)}" y2="${y(0)}" stroke="var(--j-default)" stroke-dasharray="4 4"/><polyline points="${points.map(p=>x(p)+','+y(p.value)).join(' ')}" fill="none" stroke="${running<0?'var(--j-warning)':'var(--j-accent)'}" stroke-width="2"/>`;
    content+=svgText(left,h-12,state.start||points[0].date)+svgText(right,h-12,state.end||points.at(-1).date,'text-anchor="end"');
    for(const p of points)content+=`<circle cx="${x(p)}" cy="${y(p.value)}" r="3" fill="var(--j-accent)"><title>${esc(p.date)}: ${money(p.value)} cumulative provider difference</title></circle>`;
    node.innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Cumulative provider difference ${esc(money(running))}">${content}</svg>`;
  }
  async function loadConfig() {
    q('#j-config-status').textContent='Loading settings…'; q('#j-config-apply').disabled=true;
    try {const response=await fetch('/api/config',{cache:'no-store'});if(!response.ok)throw new Error();config=await response.json(); fillConfig();q('#j-config-status').textContent='Supported settings loaded.';}
    catch {q('#j-config-status').textContent='Could not load configuration. Reopen Configuration to retry.';}
  }
  function fillConfig() {
    const s=config.settings; q('#j-enabled').checked=s.enabled; q('#j-enabled').disabled=config.can_enable===false; q('#j-effort-mode').value=s.effort_mode; q('#j-timeout').value=s.selection_timeout;
    for(const tier of ['economical','balanced','strongest'])q('#j-tier-'+tier).value=(s.tier_models[tier]||[]).join(', ');
    q('#j-config-semantics').textContent=typeof config.semantics==='string'?config.semantics:'Restart Hermes Desktop. Saved settings apply to new conversations; existing bindings are unchanged.';
    if(config.installation_notice)q('#j-config-semantics').textContent+=' '+config.installation_notice;
    q('#j-config-mode').textContent=data?.mode==='demo'?'Demo settings only':'Profile settings';q('#j-config-apply').textContent=data?.mode==='demo'?'Apply to demo':'Save settings';
    q('#j-config-revert').disabled=false; updateDraft();
  }
  function readDraft() {return {enabled:q('#j-enabled').checked,effort_mode:q('#j-effort-mode').value,selection_timeout:Number(q('#j-timeout').value),tier_models:Object.fromEntries(['economical','balanced','strongest'].map(t=>[t,q('#j-tier-'+t).value.split(',').map(s=>s.trim()).filter(Boolean)]))};}
  function draftErrors(s) {
    const errors=[];if(!Number.isFinite(s.selection_timeout)||s.selection_timeout<.01||s.selection_timeout>10)errors.push('Timeout must be between 0.01 and 10 seconds.');
    if(!Object.values(s.tier_models).some(models=>models.length))errors.push('At least one candidate model is required.');
    for(const [tier,models] of Object.entries(s.tier_models)){if(models.length>32||models.some(model=>!/[A-Za-z0-9]/.test(model[0])||!/^[-A-Za-z0-9._:/]{1,128}$/.test(model)))errors.push(tier+': use at most 32 valid model identifiers.');if(new Set(models).size!==models.length)errors.push(tier+': remove duplicate candidates.');}
    return errors;
  }
  function updateDraft() {if(!config)return;const draft=readDraft(),errors=draftErrors(draft);q('#j-config-preview').textContent=JSON.stringify(draft,null,2);q('#j-config-apply').disabled=!!errors.length||JSON.stringify(draft)===JSON.stringify(config.settings);q('#j-config-status').classList.toggle('invalid',!!errors.length);q('#j-config-status').textContent=errors.join(' ')||'Format valid. Account compatibility is checked by Hermes.';}
  async function saveConfig() {
    if(!config)return;const settings=readDraft();if(draftErrors(settings).length)return;
    q('#j-config-apply').disabled=true;
    try {const response=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-JevGauge-Token':config.csrf_token},body:JSON.stringify({settings,revision:config.revision})});if(!response.ok)throw new Error(String(response.status));const result=await response.json();config={...config,...result};fillConfig();q('#j-config-status').textContent=data?.mode==='demo'?'Saved to demo only. Hermes settings are unchanged.':'Saved. Restart Hermes Desktop for new conversations to use these settings.';}
    catch(e){q('#j-config-status').textContent=e.message==='409'?'Settings changed elsewhere. Reopen Configuration to load the current revision before saving.':'Could not save settings. Your draft is preserved; no success was confirmed.';q('#j-config-status').classList.add('invalid');q('#j-config-apply').disabled=false;}
  }
  function view(name) {state.view=name;closeDates();closeHelp();render();if(name==='configuration')loadConfig();q('#j-live').textContent=titles[name];}
  root.addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b)return;
    if(!b.closest('#j-date-range')&&b.id!=='j-dates-toggle')closeDates();
    if(b.dataset.help){showHelp(b);return;} if(b.id==='j-help-close'){closeHelp(true);return;}
    if(b.dataset.theme){theme=b.dataset.theme;setTheme();try{localStorage.setItem('jevgauge.appearance',theme);}catch{}return;}
    if(b.dataset.view){view(b.dataset.view);return;}
    if(b.dataset.usage){state.usage=b.dataset.usage;drawCharts();return;}
    if(b.dataset.period){setPeriod(b.dataset.period,data?.mode==='demo'&&data.as_of?dateOnly(new Date(data.as_of.length===10?data.as_of+'T12:00:00Z':data.as_of)):undefined);state.page=0;load();return;}
    if(b.dataset.project){state.project=b.dataset.project;state.page=0;load();return;}
    if(b.dataset.cause){state.filter=b.dataset.cause;state.page=0;view('decisions');return;}
    if(b.dataset.id){state.selected=b.dataset.id;renderDecisions();return;}
    if(b.id==='j-clear-filter'){state.filter=null;state.page=0;renderDecisions();return;}
    if(b.id==='j-prev'||b.id==='j-next'){state.page+=b.id==='j-prev'?-1:1;state.selected=null;renderDecisions();return;}
    if(b.id==='j-calculation-toggle'){const panel=q('#j-calculation');panel.hidden=!panel.hidden;b.setAttribute('aria-expanded',String(!panel.hidden));return;}
    if(b.id==='j-policy-toggle'){q('#j-policy').hidden=!q('#j-policy').hidden;b.setAttribute('aria-expanded',String(!q('#j-policy').hidden));return;}
    if(b.id==='j-dates-toggle'){const opening=q('#j-date-range').hidden;closeHelp();q('#j-date-range').hidden=!opening;b.setAttribute('aria-expanded',String(opening));if(opening){q('#j-range-from').value=state.start||'';q('#j-range-through').value=state.end||'';q('#j-date-error').textContent='';q('#j-range-from').focus();}return;}
    if(b.id==='j-range-close'){closeDates(true);return;}
    if(b.id==='j-range-apply'){
      const start=q('#j-range-from').value,end=q('#j-range-through').value;
      if(!validDate(start)||!validDate(end)||start>end||(Date.parse(end)-Date.parse(start))/86400000>=3660){q('#j-date-error').textContent='Choose valid dates, with the start on or before the end and at most 10 years in the range.';return;}
      state.period='custom';state.start=start;state.end=end;state.page=0;closeDates(true);load();return;
    }
    if(b.id==='j-retry'){load();return;}
    if(b.id==='j-config-apply'){saveConfig();return;}
    if(b.id==='j-config-revert'&&config){fillConfig();return;}
  });
  q('#j-project').addEventListener('change',event=>{state.project=event.target.value;state.page=0;closeDates();load();});
  q('#j-config-form').addEventListener('submit',e=>e.preventDefault());q('#j-config-form').addEventListener('input',updateDraft);
  document.addEventListener('pointerdown',event=>{if(!event.target.closest('#j-date-range')&&!event.target.closest('#j-dates-toggle'))closeDates();if(!event.target.closest('#j-help-panel')&&!event.target.closest('[data-help]'))closeHelp();});
  root.addEventListener('keydown',event=>{
    if(event.key==='Escape'){const datesOpen=!q('#j-date-range').hidden;closeDates(datesOpen);closeHelp(!datesOpen);}
    if(event.target.matches('[role="tab"]')&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
      event.preventDefault();const tabs=all('[role="tab"]'),index=tabs.indexOf(event.target),next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();view(tabs[next].dataset.view);
    }
  });
  let width=root.clientWidth;new ResizeObserver(()=>{if(root.clientWidth!==width){width=root.clientWidth;if(data&&!loading&&!error)drawCharts();closeHelp();}}).observe(root);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{if(theme==='system')setTheme();});
  setTheme();load(true);
})();
