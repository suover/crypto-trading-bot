import { expect, test } from '@playwright/test';
import { fixtureResponse, bigId } from '../src/test/research-api-fixtures';

test.beforeEach(async ({ page }) => {
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:5173') {
      await route.abort();
      throw new Error('Unexpected external request: ' + url.origin);
    }
    if (url.pathname === '/api/v1/auth/me') {
      await route.fulfill({ json: { username: 'fixture-admin' } });
      return;
    }
    if (url.pathname.startsWith('/api/')) {
      expect(route.request().method()).toBe('GET');
      expect(url.pathname).toMatch(
        /^\/api\/v1\/research\/(summary|candidates(?:\/\d+)?|snapshots|automation-status)$/,
      );
      await route.fulfill({ contentType: 'application/json', body: fixtureResponse(url) });
    } else await route.continue();
  });
});

test('DEMO defaults, server filters and pages, exact detail and non-persistent mode', async ({
  page,
}) => {
  const urls: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/api/') && !request.url().includes('/auth/'))
      urls.push(request.url());
  });
  await page.goto('/research');
  await expect(
    page.getByRole('heading', { name: '증거를 쌓고, 정책을 검증합니다.' }),
  ).toBeVisible();
  expect(urls).toEqual([]);
  await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
  await expect(page.getByRole('table', { name: 'API 연구 후보 목록' })).toBeVisible();
  await expect(page.getByText('baseline-a', { exact: true })).toBeVisible();
  await expect(page.getByText('baseline-b', { exact: true })).toBeVisible();
  await expect(page.getByText(/현재 API 미제공/)).toBeVisible();
  await page.getByRole('button', { name: '다음 후보 페이지' }).click();
  await expect(page.getByText('API Policy 20', { exact: true })).toBeVisible();
  await page.getByLabel('정책명 검색').fill('test & policy');
  await page.getByLabel('등록 시작 · KST').fill('2026-01-01T09:00');
  await page.getByLabel('등록 종료 · KST').fill('2026-01-02T09:00');
  await page.getByRole('button', { name: '검색 적용' }).click();
  await expect.poll(() => urls.at(-1)).toContain('keyword=test+%26+policy');
  expect(urls.at(-1)).toContain('registeredFrom=2026-01-01T00%3A00%3A00.000Z');
  expect(urls.at(-1)).toContain('page=0');
  await page.getByLabel('Shadow 등록 여부').selectOption('true');
  await expect.poll(() => urls.at(-1)).toContain('shadowEnrolled=true');
  await page.getByLabel('후보 정렬').selectOption('id,asc');
  await expect.poll(() => urls.at(-1)).toContain('sort=id%2Casc');
  const opener = page.getByRole('button', { name: 'API Policy API 상세보기', exact: true });
  await opener.click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('0.123456789123456789', { exact: true })).toBeVisible();
  await expect(dialog.getByText('저장된 당시 Gate Status')).toBeVisible();
  expect(urls.some((url) => url.endsWith('/candidates/' + bigId))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(opener).toBeFocused();
  await page.getByRole('button', { name: '다음 Snapshot 페이지' }).click();
  await expect.poll(() => urls.at(-1)).toContain('snapshots?page=1');
  await page.reload();
  await expect(page.getByText('DEMO', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'DEMO 화면' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});

for (const [status, message] of [
  [401, '관리자 인증이 필요합니다'],
  [403, '접근이 거부되었습니다.'],
  [500, '서버 오류로 데이터를 조회하지 못했습니다'],
] as const) {
  test('HTTP ' + status + ' without Mock fallback', async ({ page }) => {
    await page.route('**/api/v1/research/**', (route) =>
      route.fulfill({ status, body: 'private exception' }),
    );
    await page.goto('/research');
    await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
    if (status === 401) {
      await expect(page.getByRole('heading', { name: '관리자 로그인' })).toBeVisible();
      await expect(page.getByRole('table')).toHaveCount(0);
      return;
    }
    await expect(page.getByRole('alert')).toHaveCount(4);
    await expect(page.getByRole('alert').first()).toContainText(message);
    await expect(page.getByText('private exception')).toHaveCount(0);
    await expect(page.getByRole('region', { name: '현재 Runtime Ranking 정책' })).toHaveCount(0);
  });
}
test('invalid schema and detail not found are explicit', async ({ page }) => {
  await page.route('**/api/v1/research/summary', (route) =>
    route.fulfill({ contentType: 'application/json', body: '{}' }),
  );
  await page.route('**/api/v1/research/candidates/' + bigId, (route) =>
    route.fulfill({ status: 404 }),
  );
  await page.goto('/research');
  await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('잘못된 API 데이터');
  await page.getByRole('button', { name: 'API Policy API 상세보기', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText(
    '후보를 찾을 수 없습니다',
  );
});

for (const width of [375, 1440]) {
  test('API ' + width + 'px themes, no overflow or runtime errors', async ({ page }, testInfo) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/research');
    await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
    await expect(page.getByRole('table', { name: 'API 연구 후보 목록' })).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
      .toBe(true);
    await page.screenshot({ path: testInfo.outputPath('api-dark.png'), fullPage: true });
    await page.getByRole('button', { name: '라이트 모드로 전환' }).click();
    await expect(page.locator('html')).toHaveClass('light');
    await page.screenshot({ path: testInfo.outputPath('api-light.png'), fullPage: true });
    await page.getByRole('button', { name: 'API Policy API 상세보기', exact: true }).click();
    await expect(
      page.getByRole('dialog').getByText('0.123456789123456789', { exact: true }),
    ).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
      .toBe(true);
    expect(errors).toEqual([]);
  });
}

test('Vite rejects write methods and non-Research endpoints before proxying', async ({
  request,
}) => {
  expect((await request.post('/api/v1/research/summary')).status()).toBe(403);
  expect((await request.get('/api/v1/orders')).status()).toBe(403);
});
