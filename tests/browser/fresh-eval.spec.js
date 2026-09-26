const {test, expect} = require('@playwright/test');
const {startFixture} = require('./server-helper');

test('published price table preserves fractional-cent rates', async ({page}) => {
  const server = await startFixture('tests/browser/demo_fixture.py');
  try {
    await page.goto(server.url);
    await page.getByRole('button', {name:'Details', exact:true}).click();
    const nano = page.locator('#j-calculation tr').filter({has:page.getByRole('cell', {name:'gpt-4.1-nano', exact:true})});
    // Price book says 0.025 USD per million cached tokens, not rounded 0.03.
    await expect(nano.getByRole('cell').nth(2)).toHaveText('$0.025');
  } finally {
    server.close();
  }
});

test('bounded-history errors expose the actionable API remedy as escaped text', async ({page}) => {
  const message = 'History exceeds the 50,000-event dashboard limit. Archive older data offline before viewing. No partial totals are shown. <img src=x onerror="window.injected=1">';
  await page.route('**/api/dashboard**', route => route.fulfill({status:503, json:{error:message}}));
  await page.goto('/');
  await expect(page.locator('#j-status')).toContainText(message);
  await expect(page.locator('#j-status img')).toHaveCount(0);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
  await expect(page.locator('#j-overview')).toBeHidden();
  await expect(page.getByRole('button', {name:'Retry', exact:true})).toBeVisible();
});

for (const response of [
  {contentType:'text/html', body:'<html>Bad gateway</html>'},
  {contentType:'application/json', body:'{"error":'},
  {json:{error:{internal:'not a user-facing message'}}}
]) {
  test('malformed server error uses safe fallback '+JSON.stringify(response), async ({page}) => {
    await page.route('**/api/dashboard**', route => route.fulfill({status:503, ...response}));
    await page.goto('/');
    await expect(page.locator('#j-status')).toContainText('Could not load captured evidence');
    await expect(page.locator('#j-overview')).toBeHidden();
    await expect(page.locator('#j-status')).not.toContainText('[object Object]');
  });
}
