import { defineConfig } from '@playwright/test';

const baseURL = process.env.WEB_TEST_URL ?? '';
if (!/^https:\/\/127\.0\.0\.1:\d+$/.test(baseURL)) {
  throw new Error('WEB_TEST_URL must be a loopback HTTPS fixture URL');
}

export default defineConfig({
  testDir: './e2e-web',
  outputDir: '../build/web-playwright',
  testMatch: process.env.WEB_TEST_OUTAGE === 'true' ? 'outage.spec.ts' : 'https.spec.ts',
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: { baseURL, ignoreHTTPSErrors: true },
});
