const {test, expect} = require('@playwright/test');
const payload = {
  mode:'demo', currency:'USD', as_of:'2026-09-25',
  summary:{conversation_count:2,request_count:3,review_count:1,default_cost_usd:1,routed_cost_usd:0.4,provider_cost_usd:0.4,provider_difference_usd:0.6,net_savings_usd:null,median_cost_usd:0.8,strongest_cost_usd:2,overhead_usd:null,overhead_available:false,routed_count:1,abstained_count:1,failed_count:0,manual_count:0},
  coverage:{known_usage_requests:2,priced_requests:2,total_requests:3,comparison_requests:2,missing_usage_requests:1,missing_price_requests:0,manual_requests:0},
  conversations:[{id:'conv-a', timestamp:'2026-09-25T12:00:00Z',project:'Example',default_model:'model-b',selected_model:'model-a',requested_effort:'low',outcome:'routed',reason_code:'selected',model_tier:'economical',effort_tier:'low',policy_version:'v1',latency_ms:20,eligible_models:[{model:'model-a',capability:1},{model:'model-b',capability:2}],attempts:[{requested_model:'model-a',reported_model:'model-a',requested_effort:'low',status:'completed',usage:{input_tokens:1000,cached_input_tokens:100,output_tokens:100,reasoning_tokens:50,source:'provider'}}],review_reasons:[],provider_cost_usd:0.4,default_cost_usd:1},{id:'conv-b',timestamp:'2026-09-24T12:00:00Z',project:null,default_model:'model-b',selected_model:null,outcome:'confidence_rejected',review_reasons:['missing_usage'],attempts:[]}],
  series:[{date:'2026-09-24',default_cost_usd:0.5,routed_cost_usd:0.2,provider_difference_usd:0.3},{date:'2026-09-25',default_cost_usd:0.5,routed_cost_usd:0.2,provider_difference_usd:0.3}],
  models:[{model:'model-a',conversations:1,requests:2},{model:'model-b',conversations:1,requests:1}],projects:[{project:'Example',conversations:1,requests:2,provider_cost_usd:0.4,provider_difference_usd:0.6}],pricing:{version:'test-v1',sources:[{url:'https://example.com/pricing',title:'Test source'}],rates:{}},limitations:['Request coverage does not bound missing dollars.'],filters:{timezone:'UTC'}
};
const config={settings:{enabled:true,effort_mode:'auto',selection_timeout:5,tier_models:{economical:['model-a'],balanced:['model-b'],strongest:['model-b']}},revision:'v1',csrf_token:'test-token',semantics:'Restart Hermes Desktop. Applies to new conversations.',supported:['enabled','effort_mode','selection_timeout','tier_models']};
async function boot(page, data=payload) {
  await page.route('**/api/dashboard**', r=>r.fulfill({json:data}));
  await page.route('**/api/config', r=>r.fulfill({json:config}));
  await page.goto('/');
}
test('renders API evidence, four views and honest overhead', async ({page})=>{
  await boot(page);
  await expect(page.getByRole('tab', {name:'Savings',exact:true})).toBeVisible();
  await expect(page.locator('#j-metrics')).toContainText('$0.60');
  await expect(page.locator('#j-metrics')).toContainText('Provider-only difference');
  await expect(page.locator('#j-coverage')).toContainText('2/3');
  await page.getByRole('tab',{name:'Jev cost',exact:true}).click();
  await expect(page.locator('#j-jev-total')).toHaveText('Unavailable');
  await page.getByRole('tab',{name:'Reliability',exact:true}).click();
  await expect(page.locator('#j-evidence')).toContainText('Missing usage');
  await page.getByRole('tab',{name:'Decisions',exact:true}).click();
  await expect(page.locator('#j-inspector')).toContainText('Prompt text is not retained');
});
test('date picker dismisses on every required path and invalid range preserves filter', async({page})=>{
  await boot(page);
  const dates=page.getByRole('button',{name:'Dates',exact:true});
  for (const close of [()=>page.locator('#j-title').click(),()=>page.keyboard.press('Escape'),()=>page.getByRole('button',{name:'Close date picker'}).click(),()=>page.getByRole('tab',{name:'Decisions',exact:true}).click(),()=>page.getByRole('button',{name:'Week',exact:true}).click()]) {
    await dates.click(); await expect(page.locator('#j-date-range')).toBeVisible(); await close(); await expect(page.locator('#j-date-range')).toBeHidden();
  }
  const old=await page.locator('#j-date').textContent();
  await dates.click(); await page.locator('#j-range-from').fill('2026-09-25'); await page.locator('#j-range-through').fill('2026-09-01'); await page.getByRole('button',{name:'Apply range'}).click();
  await expect(page.locator('#j-date-error')).not.toBeEmpty(); await expect(page.locator('#j-date-range')).toBeVisible(); await expect(page.locator('#j-date')).toHaveText(old);
  await page.locator('#j-range-through').fill('2026-09-25'); await page.getByRole('button',{name:'Apply range'}).click(); await expect(page.locator('#j-date-range')).toBeHidden();
});
test('themes, Details, metric explanations and chart layout remain usable at narrow widths',async({page})=>{
  await boot(page); await page.getByRole('button',{name:'Dark appearance'}).click(); await expect(page.locator('#jg-v5')).toHaveCSS('color-scheme','dark');
  await page.getByRole('button',{name:'Light appearance'}).click(); await expect(page.locator('#jg-v5')).toHaveCSS('color-scheme','light');
  const before=await page.locator('#j-models').boundingBox(); await page.getByRole('button',{name:'Provider requests',exact:true}).click(); expect(await page.locator('#j-models').boundingBox()).toEqual(before);
  await page.getByRole('button',{name:'Details',exact:true}).click(); await expect(page.locator('#j-calculation')).toContainText('Median eligible'); await expect(page.locator('#j-calculation')).toContainText('test-v1');
  await page.getByRole('button',{name:'Details',exact:true}).click(); await expect(page.locator('#j-calculation')).toBeHidden();
  for(const key of ['savings','comparison','trend','routed','review','usage']) {await page.locator('[data-help="'+key+'"]').first().click(); await expect(page.locator('#j-help-panel')).toBeVisible(); await page.keyboard.press('Escape'); await expect(page.locator('#j-help-panel')).toBeHidden();}
  await page.setViewportSize({width:352,height:900}); expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.locator('[data-help="savings"]').click(); const box=await page.locator('#j-help-panel').boundingBox(); expect(box.x+box.width).toBeLessThanOrEqual(352);
});
test('shared project filter, review drilldown and stored text escaping', async({page})=>{
  const data=structuredClone(payload); data.conversations[0].id='<img src=x onerror="window.bad=1">'; data.conversations[0].project='<svg onload="window.bad=1">'; data.projects[0].project=data.conversations[0].project;
  await boot(page,data); await page.getByRole('button',{name:'Inspect review queue'}).click(); await expect(page.locator('#j-decision-list .j-decision')).toHaveCount(1); await expect(page.locator('#j-inspector')).toContainText('missing usage');
  await page.locator('#j-clear-filter').click(); await expect(page.locator('#j-decision-list .j-decision')).toHaveCount(2); await page.locator('#j-decision-list .j-decision').first().click(); await expect(page.locator('#j-inspector')).toContainText('<svg'); expect(await page.evaluate(()=>window.bad)).toBeUndefined();
  const request=page.waitForRequest(r=>r.url().includes('/api/dashboard')&&r.url().includes('project=')); await page.locator('#j-project').selectOption(data.projects[0].project); expect(new URL((await request).url()).searchParams.get('project')).toBe(data.projects[0].project);
});
test('configuration writes supported fields with revision/token and shows restart semantics',async({page})=>{
  await boot(page,{...payload,mode:'live'}); await page.getByRole('button',{name:'Configuration',exact:true}).click(); await expect(page.locator('#j-config-form')).toBeVisible();
  await page.locator('#j-timeout').fill('3'); let sent;
  await page.route('**/api/config',async r=>{sent=r.request(); await r.fulfill({json:{...config,revision:'v2',settings:{...config.settings,selection_timeout:3}}});});
  await page.locator('#j-config-apply').click(); await expect(page.locator('#j-config-status')).toContainText('Restart'); expect(sent.headers()['x-jevgauge-token']).toBe('test-token'); expect(sent.postDataJSON()).toEqual({settings:{...config.settings,selection_timeout:3},revision:'v1'});
});
test('empty and failed requests never imply zero cost or stale valid results',async({page})=>{
  const empty=structuredClone(payload); empty.summary.conversation_count=0; empty.conversations=[]; empty.series=[]; empty.models=[]; empty.projects=[]; await boot(page,empty); await expect(page.locator('#j-trend')).toContainText('No conversations');
  await page.route('**/api/dashboard**',r=>r.fulfill({status:503,json:{error:'Unavailable'}})); await page.getByRole('button',{name:'Week',exact:true}).click(); await expect(page.locator('#j-status')).toContainText('Could not load'); await expect(page.locator('#j-overview')).toBeHidden();
});
test('keyboard tabs, remaining explanations, loading and accessibility across all views',async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  let unblock;const waiting=new Promise(resolve=>{unblock=resolve;});
  await page.route('**/api/dashboard**',async r=>{await waiting;await r.fulfill({json:payload});});
  await page.route('**/api/config',r=>r.fulfill({json:config}));
  await page.goto('/');await expect(page.locator('#j-status')).toContainText('Loading');unblock();await expect(page.locator('#j-status')).toBeEmpty();
  await page.getByRole('tab',{name:'Savings',exact:true}).focus();await page.keyboard.press('ArrowRight');await expect(page.getByRole('tab',{name:'Decisions',exact:true})).toBeFocused();await expect(page.locator('#j-decisions')).toBeVisible();
  await page.keyboard.press('ArrowRight');await expect(page.locator('#j-reliability')).toBeVisible();
  for(const key of ['confidence','evidence']){await page.locator('[data-help="'+key+'"]').focus();await page.keyboard.press('Enter');await expect(page.locator('#j-help-panel')).toBeVisible();await page.keyboard.press('Escape');await expect(page.locator('[data-help="'+key+'"]')).toBeFocused();}
  await page.getByRole('tab',{name:'Jev cost',exact:true}).click();await page.locator('[data-help="jev"]').click();await expect(page.locator('#j-help-panel')).toContainText('captured measured overhead');await page.keyboard.press('Escape');
  const AxeBuilder=require('@axe-core/playwright').default;
  for(const view of ['Savings','Decisions','Reliability','Jev cost']) {await page.getByRole('tab',{name:view,exact:true}).click();const result=await new AxeBuilder({page}).analyze();expect(result.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>n.target)}))).toEqual([]);}
  await page.getByRole('button',{name:'Configuration',exact:true}).click();await expect(page.locator('#j-config-status')).toHaveText('Supported settings loaded.');expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);expect(errors).toEqual([]);
});
test('supported config boundary and captured reason drilldown stay honest',async({page})=>{
  const d=structuredClone(payload);d.conversations[1].outcome='abstained';d.conversations[1].reason_code='low_confidence';
  await boot(page,d);await page.getByRole('button',{name:'low confidence',exact:true}).click();await expect(page.locator('#j-decision-list .j-decision')).toHaveCount(1);await expect(page.locator('#j-inspector')).toContainText('low_confidence');
  await page.route('**/api/config',r=>r.fulfill({json:{...config,settings:{...config.settings,enabled:false},can_enable:false,installation_notice:'Install the owned plugin before enabling.'}}));
  await page.getByRole('button',{name:'Configuration',exact:true}).click();await expect(page.locator('#j-enabled')).toBeDisabled();await expect(page.locator('#j-config-semantics')).toContainText('Install the owned plugin');
});
test('configuration permits supported empty tiers but rejects an entirely empty candidate set',async({page})=>{
  await boot(page);await page.getByRole('button',{name:'Configuration',exact:true}).click();await expect(page.locator('#j-config-status')).toHaveText('Supported settings loaded.');
  await page.locator('#j-tier-strongest').fill('');await expect(page.locator('#j-config-apply')).toBeEnabled();
  await page.locator('#j-tier-balanced').fill('');await page.locator('#j-tier-economical').fill('');await expect(page.locator('#j-config-apply')).toBeDisabled();await expect(page.locator('#j-config-status')).toContainText('At least one candidate');
});
