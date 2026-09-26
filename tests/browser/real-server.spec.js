const {test, expect} = require('@playwright/test');
const {startFixture}=require('./server-helper');
test('real HTTP service serves demo evidence, interactions and screenshots',async({page,request})=>{
  const server=process.env.JEVGAUGE_TEST_URL?{url:process.env.JEVGAUGE_TEST_URL,close:()=>{}}:await startFixture('tests/browser/demo_fixture.py');
  try {
  const errors=[]; page.on('pageerror',e=>errors.push(e.message)); page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.goto(server.url); await expect(page.locator('#j-mode')).toHaveText('DEMO'); await expect(page.locator('#j-status')).toBeEmpty();
  const actual=await (await request.get(server.url+'/api/dashboard?start=2026-09-01&end=2026-09-25&timezone=America%2FVancouver')).json();
  await expect(page.locator('#j-metrics')).toContainText(new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(actual.summary.provider_difference_usd));
  for(const theme of ['Light','Dark']){await page.getByRole('button',{name:theme+' appearance'}).click();await page.screenshot({path:'outputs/browser/v5-'+theme.toLowerCase()+'-desktop.png',fullPage:true});}
  for(const view of ['Decisions','Reliability','Jev cost']){await page.getByRole('tab',{name:view,exact:true}).click();await expect(page.locator('#j-status')).toBeEmpty();}
  await page.getByRole('button',{name:'Configuration',exact:true}).click(); await expect(page.locator('#j-config-status')).toHaveText('Supported settings loaded.');
  await page.locator('#j-timeout').fill('4.5'); await page.locator('#j-config-apply').click();await expect(page.locator('#j-config-status')).toContainText('Saved to demo only');
  await page.getByRole('tab',{name:'Savings',exact:true}).click();await page.setViewportSize({width:352,height:900});await page.screenshot({path:'outputs/browser/v5-dark-mobile.png',fullPage:true}); expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.getByRole('button',{name:'Light appearance'}).click();await page.screenshot({path:'outputs/browser/v5-light-mobile.png',fullPage:true});expect(errors).toEqual([]);
  } finally {server.close();}
});
