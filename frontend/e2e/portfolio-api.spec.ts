import { expect, test } from '@playwright/test';
import { analysisDetail, analysisPage } from '../src/test/analysis-api-fixtures';
import { observedAt, overviewPositions, overviewScope } from '../src/test/overview-api-fixtures';

test('portfolio switches from demo to scoped holdings and fills and back on logout', async ({
  page,
}) => {
  let signedIn = false;
  const reads: string[] = [];
  const order = analysisDetail.order;
  const ordersUrl = '/api/v1/portfolio/orders';
  const fillsUrl = `${ordersUrl}/${order.id}/fills`;
  await page.setViewportSize({ width: 375, height: 900 });
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    expect(url.origin).toBe(test.info().project.use.baseURL);
    switch (url.pathname) {
      case '/api/v1/auth/me':
        return route.fulfill({
          status: signedIn ? 200 : 401,
          json: signedIn ? { username: 'fixture-admin' } : {},
        });
      case '/api/v1/auth/csrf':
        return route.fulfill({ json: { headerName: 'X-CSRF-TOKEN', token: 'fixture' } });
      case '/api/v1/auth/login':
      case '/api/v1/auth/logout':
        expect(request.method()).toBe('POST');
        expect(request.headers()['x-csrf-token']).toBe('fixture');
        signedIn = url.pathname.endsWith('/login');
        return route.fulfill({ status: 204 });
    }
    expect(signedIn).toBe(true);
    expect(request.method()).toBe('GET');
    reads.push(url.pathname);
    if (url.pathname === '/api/v1/overview/positions')
      return route.fulfill({ json: overviewPositions });
    const envelope = { scope: overviewScope };
    if (url.pathname === ordersUrl)
      return route.fulfill({ json: { ...analysisPage([order]), ...envelope } });
    expect(url.pathname).toBe(fillsUrl);
    const fill = {
      id: '9007199254740993',
      quantity: '1',
      price: '1500',
      fundsKrw: '1500',
      recordedAt: observedAt,
    };
    return route.fulfill({ json: { ...analysisPage([fill]), ...envelope, orderId: order.id } });
  });
  await page.goto('/portfolio');
  await expect(page.getByRole('heading', { name: '자산의 흐름을 읽다' })).toBeVisible();
  expect(reads).toEqual([]);
  await page.getByRole('link', { name: '관리자 로그인' }).click();
  await page.getByLabel('관리자 아이디').fill('fixture-admin');
  await page.getByLabel('비밀번호').fill('fixture-only-password');
  await page.getByLabel('비밀번호').press('Enter');
  await expect(page).toHaveURL(/\/portfolio$/);
  await expect(page.getByRole('table', { name: 'API 보유 자산' })).toContainText('0.0000000001');
  expect(reads).not.toContain(ordersUrl);
  await page.getByRole('button', { name: '주문·체결 이력' }).click();
  await expect(page.getByRole('table', { name: '봇 주문 이력' })).toContainText('LIVE');
  expect(reads).not.toContain(fillsUrl);
  await page.getByRole('button', { name: `주문 ${order.id} 체결 상세` }).click();
  await expect(page.getByRole('table', { name: '개별 체결 내역' })).toContainText('1,500원');
  await expect(page.getByRole('columnheader', { name: '원장 기록 시각 · KST' })).toBeVisible();
  const title = `주문 ${order.id} 체결 상세`;
  const drawer = page.getByRole('dialog', { name: title });
  const close = drawer.getByRole('button', { name: 'Close' });
  for (const width of [375, 1280]) {
    await page.setViewportSize({ width, height: 720 });
    await expect(drawer).toBeInViewport({ ratio: 1 });
    expect(await drawer.evaluate((e) => e.getBoundingClientRect().right)).toBe(width);
    await expect(drawer.getByRole('heading', { name: title })).toBeInViewport({ ratio: 1 });
    await expect(close).toBeInViewport({ ratio: 1 });
  }
  await close.click();
  await expect(drawer).toHaveCount(0);
  await page.getByRole('button', { name: `주문 ${order.id} 체결 상세` }).click();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: '로그아웃' }).click();
  await expect(page.getByRole('heading', { name: '자산의 흐름을 읽다' })).toBeVisible();
  await expect(page.getByRole('table', { name: '봇 주문 이력' })).toHaveCount(0);
});

test('portfolio gateway accepts only the two business read routes', async ({ request }) => {
  const orders = '/api/v1/portfolio/orders';
  for (const path of [orders, orders + '/1/fills']) {
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
      expect((await request.fetch(path, { method })).status()).toBe(403);
    }
  }
  for (const suffix of ['/1', '/01/fills', '/1/cancel', '/1/fills/raw', '/1/fills/']) {
    const response = await request.get(orders + suffix);
    expect(response.status()).toBe(403);
    expect(response.headers()['cache-control']).toBe('no-store');
  }
});
