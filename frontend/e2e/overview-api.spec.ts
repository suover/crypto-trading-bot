import { expect, test } from '@playwright/test';
import {
  overviewFixtureResponse,
  overviewPositions,
  overviewSummary,
} from '../src/test/overview-api-fixtures';

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
        /^\/api\/v1\/overview\/(summary|positions|performance|recommendations)$/,
      );
      await route.fulfill({ contentType: 'application/json', body: overviewFixtureResponse(url) });
    } else await route.continue();
  });
});

test('DEMO opt-in, exact values, server filters/pages, missing capabilities and reload', async ({
  page,
}) => {
  const urls: string[] = [];
  page.on('request', (r) => {
    if (r.url().includes('/api/')) urls.push(r.url());
  });
  await page.goto('/overview');
  await expect(page.getByRole('heading', { name: '시장을 읽고, 흐름을 한눈에.' })).toBeVisible();
  expect(urls).toEqual([]);
  await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
  await expect(page.getByRole('table', { name: 'API 보유 자산' })).toBeVisible();
  await expect(
    page.getByText('12,345,678,901,234,567,890.123456789원', { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole('table', { name: 'API 보유 자산' })
      .getByRole('cell')
      .filter({ hasText: '0.0000000001' }),
  ).toHaveCount(3);
  await expect(page.getByText('계산 대상 제외')).toBeVisible();
  await expect(page.getByRole('heading', { name: '현재 API 미제공' })).toBeVisible();
  await expect(page.locator('a[href*="analysis="]')).toHaveCount(0);
  await page.getByRole('button', { name: '다음 성과 페이지' }).click();
  await expect.poll(() => urls.at(-1)).toContain('performance?page=1');
  await page.getByLabel('성과 기간 시작 · KST').fill('2026-01-01T09:00');
  await page.getByLabel('성과 기간 종료 · KST').fill('2026-01-03T09:00');
  await page.getByRole('button', { name: '기간 적용' }).click();
  await expect.poll(() => urls.at(-1)).toContain('from=2026-01-01T00%3A00%3A00.000Z');
  expect(urls.at(-1)).toContain('page=0');
  await page.getByRole('button', { name: '다음 추천 페이지' }).click();
  await expect.poll(() => urls.at(-1)).toContain('recommendations?page=1');
  await page.getByLabel('추천 action').selectOption('HOLD');
  await expect.poll(() => urls.at(-1)).toContain('action=HOLD');
  await expect(page.getByRole('table', { name: 'API 최근 추천' }).getByText('BUY')).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('button', { name: 'DEMO 화면' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});
for (const status of [401, 403, 500]) {
  test(`HTTP ${status} without fake fallback`, async ({ page }) => {
    await page.route('**/api/v1/overview/**', (route) =>
      route.fulfill({ status, body: 'private exception' }),
    );
    await page.goto('/overview');
    await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
    await expect(page.getByRole('alert')).toHaveCount(3);
    await expect(page.getByText('private exception')).toHaveCount(0);
    await expect(page.getByRole('table')).toHaveCount(0);
    if (status === 401)
      await expect(
        page.getByRole('link', { name: '새 탭에서 관리자 인증' }).first(),
      ).toHaveAttribute('href', '/api/v1/overview/summary');
  });
}
test('snapshot mismatch is bounded, never shown as empty holdings, and can recover', async ({
  page,
}) => {
  let calls = 0,
    mismatch = true;
  await page.route('**/api/v1/overview/positions', (route) => {
    calls++;
    return route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(
        mismatch
          ? {
              ...overviewPositions,
              portfolio: { ...overviewPositions.portfolio, snapshotId: '2' },
              items: [],
            }
          : overviewPositions,
      ),
    });
  });
  await page.goto('/overview');
  await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('조회 기준이 변경되었습니다');
  expect(calls).toBe(2);
  await expect(page.getByRole('table', { name: 'API 보유 자산' })).toHaveCount(0);
  mismatch = false;
  await page.getByRole('button', { name: '다시 시도' }).click();
  await expect(page.getByRole('table', { name: 'API 보유 자산' })).toBeVisible();
});
test('partial endpoint failure preserves independent panels; invalid schema is explicit', async ({
  page,
}) => {
  await page.route('**/api/v1/overview/positions', (route) =>
    route.fulfill({ contentType: 'application/json', body: '{}' }),
  );
  await page.goto('/overview');
  await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('잘못된 API 데이터');
  await expect(page.getByRole('table', { name: 'API 최근 추천' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '계좌 평가와 성과' })).toBeVisible();
});
test('no portfolio is distinct from a zero valued portfolio', async ({ page }) => {
  await page.route('**/api/v1/overview/summary', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        ...overviewSummary,
        portfolio: null,
        performance: null,
        botPnl: null,
      }),
    }),
  );
  await page.route('**/api/v1/overview/positions', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ ...overviewPositions, portfolio: null, items: [] }),
    }),
  );
  await page.goto('/overview');
  await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
  await expect(page.getByRole('heading', { name: '계좌 snapshot이 없습니다' })).toBeVisible();
  await expect(page.getByText('0원', { exact: true })).toHaveCount(0);
});
for (const width of [375, 1440]) {
  test(`API ${width}px dark/light and keyboard with no page overflow`, async ({
    page,
  }, testInfo) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/overview');
    await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
    await expect(page.getByRole('table', { name: 'API 성과 이력' })).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true);
    await page.screenshot({ path: testInfo.outputPath('overview-api-dark.png'), fullPage: true });
    await page.getByRole('button', { name: '라이트 모드로 전환' }).click();
    await expect(page.locator('html')).toHaveClass('light');
    await page.screenshot({ path: testInfo.outputPath('overview-api-light.png'), fullPage: true });
    await page.getByLabel('추천 action').focus();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    expect(errors).toEqual([]);
  });
}
test('Vite permits only exact opt-in GET routes', async ({ request }) => {
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE'])
    expect((await request.fetch('/api/v1/overview/summary', { method })).status()).toBe(403);
  for (const path of ['/api/v1/overview/orders', '/api/v1/overview/summary/1', '/api/v1/orders'])
    expect((await request.get(path)).status()).toBe(403);
});
