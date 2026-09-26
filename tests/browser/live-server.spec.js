const {test,expect}=require('@playwright/test');
const {startFixture}=require('./server-helper');
test('actual route callback persists once and reaches live browser with hand calculated costs',async({page})=>{
  const server=await startFixture('tests/browser/live_fixture.py');
  try {
    const url=server.url;
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(url);await expect(page.locator('#j-mode')).toHaveText('LIVE');await expect(page.locator('#j-status')).toBeEmpty();
    // Independent example: default = 800*2/1M + 200*.5/1M + 100*8/1M = .0025.
    // Routed mini = 800*.4/1M + 200*.1/1M + 100*1.6/1M = .0005; difference .0020.
    await expect(page.locator('#j-metrics')).toContainText('$0.0020');await expect(page.locator('#j-coverage')).toContainText('1/1 requests priced');await expect(page.locator('#j-savings-bridge')).toContainText('$0.0025');await expect(page.locator('#j-savings-bridge')).toContainText('$0.0005');
    await page.getByRole('tab',{name:'Decisions',exact:true}).click();await expect(page.locator('#j-inspector')).toContainText('Actual provider attempts (1)');await expect(page.locator('#j-inspector')).toContainText('Prompt text is not retained');
    const body=await page.locator('body').textContent();expect(body).not.toContain('PRIVATE_PROMPT');expect(body).not.toContain('FAKE_SECRET');expect(errors).toEqual([]);
  }finally{server.close();}
});
