const {defineConfig} = require('@playwright/test');
module.exports = defineConfig({testDir: './tests/browser', fullyParallel: true, timeout: 15000,
  use: {channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome', baseURL: process.env.JEVGAUGE_TEST_URL || 'http://127.0.0.1:8876', viewport: {width: 1024, height: 1000}},
  webServer: process.env.JEVGAUGE_TEST_URL ? undefined : {command: 'python3 -m http.server 8876 --bind 127.0.0.1 --directory src/jevgauge/static', url: 'http://127.0.0.1:8876', reuseExistingServer: false},
  reporter: 'list'});
