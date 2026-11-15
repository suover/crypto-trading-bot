import { defineConfig } from '@playwright/test';

const port = Number(process.env.PLAYWRIGHT_PORT || 5173);
if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  throw new Error('PLAYWRIGHT_PORT must be an integer between 1024 and 65535');
}
const baseURL = `http://127.0.0.1:${port}`;
const preview = process.env.PLAYWRIGHT_PREVIEW === 'true';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  workers: 2,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL,
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    viewport: { width: 1440, height: 1000 },
    reducedMotion: 'reduce',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    env: preview
      ? {}
      : {
          VITE_ENABLE_RESEARCH_API: 'true',
          VITE_ENABLE_OVERVIEW_API: 'true',
          VITE_ENABLE_AI_ANALYSIS_API: 'true',
        },
    command: preview
      ? `npm run preview -- --port ${port}`
      : `node node_modules/vite/bin/vite.js --host 127.0.0.1 --port ${port}`,
    url: baseURL,
    reuseExistingServer: false,
  },
});
