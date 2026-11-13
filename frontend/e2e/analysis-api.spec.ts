import { expect, test } from '@playwright/test';
import {
  analysisFixtureResponse,
  analysisBigId,
  analysisDetail,
  analysisCandidate,
  analysisUniverse,
  analysisPage,
} from '../src/test/analysis-api-fixtures';
import { overviewFixtureResponse } from '../src/test/overview-api-fixtures';
test.beforeEach(async ({ page }) => {
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:5173') {
      await route.abort();
      throw new Error('Unexpected external origin');
    }
    if (url.pathname.startsWith('/api/')) {
      expect(route.request().method()).toBe('GET');
      expect(url.pathname).toMatch(
        /^\/api\/v1\/(ai-trade-analysis\/recommendations(?:\/[1-9]\d*(?:\/candidates)?)?|overview\/(summary|positions|performance|recommendations))$/,
      );
      await route.fulfill({
        contentType: 'application/json',
        body: url.pathname.startsWith('/api/v1/overview/')
          ? overviewFixtureResponse(url)
          : analysisFixtureResponse(url),
      });
    } else await route.continue();
  });
});
test('explicit opt-in, server filters/pages and historical detail with no demo mixing', async ({
  page,
}) => {
  const requests: string[] = [];
  page.on('request', (r) => {
    if (r.url().includes('/api/')) requests.push(r.url());
  });
  await page.goto('/ai-analysis');
  await expect(page.getByRole('heading', { name: '판단의 근거를, 한눈에' })).toBeVisible();
  expect(requests).toEqual([]);
  await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
  await expect(page.getByRole('table', { name: 'API AI 추천' })).toBeVisible();
  // Development StrictMode can cancel/remount the first list request; no row-level details are fetched.
  expect(requests.length).toBeGreaterThan(0);
  expect(requests.every((url) => new URL(url).pathname.endsWith('/recommendations'))).toBe(true);
  await page.getByRole('button', { name: `${analysisBigId} 추천 상세` }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('실거래 체결 확인 (미체결 잔량 취소)')).toBeVisible();
  await expect(dialog.getByRole('table', { name: 'API 당시 후보' })).toBeVisible();
  await expect(
    dialog.getByText('71.1234567890123456789012345678', { exact: true }).first(),
  ).toBeVisible();
  await expect(dialog.getByText('HELD', { exact: true })).toBeVisible();
  await expect(dialog.getByText('DETAIL VIEW · DEMO')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: `${analysisBigId} 추천 상세` })).toBeFocused();
  await page.getByRole('button', { name: '다음 추천 페이지' }).click();
  await expect.poll(() => requests.at(-1)).toContain('page=1');
  await page.getByLabel('추천 판단').selectOption('HOLD');
  await page.getByLabel('추천 마켓').fill('KRW-BTC');
  await page.getByRole('button', { name: '조건 적용' }).click();
  await expect.poll(() => requests.at(-1)).toContain('page=0&size=20&market=KRW-BTC&action=HOLD');
  await expect(page.getByRole('table', { name: 'API AI 추천' }).getByText('BUY')).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('button', { name: 'DEMO 화면' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});
for (const status of [401, 403, 404, 500]) {
  test(`HTTP ${status} is explicit and does not expose raw failure`, async ({ page }) => {
    await page.route('**/api/v1/ai-trade-analysis/**', (route) =>
      route.fulfill({ status, body: 'private SQL error' }),
    );
    await page.goto('/ai-analysis');
    await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.getByRole('table')).toHaveCount(0);
    await expect(page.getByText('private SQL error')).toHaveCount(0);
    if (status === 401)
      await expect(page.getByRole('link', { name: '새 탭에서 관리자 인증' })).toHaveAttribute(
        'href',
        '/api/v1/ai-trade-analysis/recommendations',
      );
  });
}
test('candidate scope mismatch is blocked without hiding independent execution summary', async ({
  page,
}) => {
  await page.route('**/candidates?*', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        ...analysisPage([analysisCandidate]),
        universe: analysisUniverse,
        scope: { ...analysisDetail.scope, userId: '999' },
      }),
    }),
  );
  await page.goto(`/ai-analysis?recommendation=${analysisBigId}`);
  await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText(
    '조회 데이터 연결이 일치하지 않습니다',
  );
  await expect(page.getByRole('table', { name: 'API 당시 후보' })).toHaveCount(0);
  await expect(page.getByText('실거래 체결 확인 (미체결 잔량 취소)')).toBeVisible();
});
test('Overview actual link enters API mode with expected scope', async ({ page }) => {
  await page.goto('/overview');
  await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
  await page
    .getByRole('link', { name: /실제 추천 상세/ })
    .first()
    .click();
  await expect(page).toHaveURL(/ai-analysis\?recommendation=/);
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText(
    '조회 데이터 연결이 일치하지 않습니다',
  );
  await expect(page.getByText('DETAIL VIEW · DEMO')).toHaveCount(0);
});
for (const width of [375, 1440]) {
  test(`API ${width}px dark/light, keyboard and no page overflow`, async ({ page }, info) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/ai-analysis');
    await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
    await expect(page.getByRole('table', { name: 'API AI 추천' })).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true);
    await page.screenshot({ path: info.outputPath('analysis-api-dark.png'), fullPage: true });
    await page.getByRole('button', { name: '라이트 모드로 전환' }).click();
    await expect(page.locator('html')).toHaveClass('light');
    await page.getByRole('button', { name: `${analysisBigId} 추천 상세` }).click();
    await expect(page.getByRole('table', { name: 'API 당시 후보' })).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true);
    await page.screenshot({ path: info.outputPath('analysis-detail-light.png'), fullPage: true });
    expect(errors).toEqual([]);
  });
}
test('Vite denies writes, approval/order paths, malformed IDs and extra segments', async ({
  request,
}) => {
  const root = '/api/v1/ai-trade-analysis/recommendations';
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE'])
    expect((await request.fetch(root, { method })).status()).toBe(403);
  for (const path of [
    '/1/approve',
    '/1/orders',
    '/1/reconcile',
    '/1/candidates/2',
    '/01',
    '/0',
    '/-1',
  ])
    expect((await request.get(root + path)).status()).toBe(403);
});
