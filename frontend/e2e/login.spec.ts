import { expect, test } from '@playwright/test';
import { overviewFixtureResponse } from '../src/test/overview-api-fixtures';

for (const width of [375, 1440]) {
  test(`session login, reload, logout and keyboard at ${width}px`, async ({ page }, info) => {
    let loggedIn = false;
    let reads = 0;
    await page.setViewportSize({ width, height: 1000 });
    await page.route('**/api/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      expect(url.origin).toBe('http://127.0.0.1:5173');
      if (url.pathname === '/api/v1/auth/me') {
        return route.fulfill({
          status: loggedIn ? 200 : 401,
          json: loggedIn ? { username: 'fixture-admin' } : {},
        });
      }
      if (url.pathname === '/api/v1/auth/csrf')
        return route.fulfill({ json: { headerName: 'X-CSRF-TOKEN', token: 'masked-fixture' } });
      if (url.pathname === '/api/v1/auth/login') {
        expect(request.method()).toBe('POST');
        expect(request.headers()['x-csrf-token']).toBe('masked-fixture');
        expect(new URLSearchParams(request.postData()!).get('password')).toBe(
          'fixture-only-password',
        );
        loggedIn = true;
        return route.fulfill({ status: 204 });
      }
      if (url.pathname === '/api/v1/auth/logout') {
        expect(request.method()).toBe('POST');
        expect(request.headers()['x-csrf-token']).toBe('masked-fixture');
        loggedIn = false;
        return route.fulfill({ status: 204 });
      }
      expect(loggedIn).toBe(true);
      expect(request.method()).toBe('GET');
      expect(url.pathname).toMatch(/^\/api\/v1\/overview\//);
      reads++;
      return route.fulfill({ contentType: 'application/json', body: overviewFixtureResponse(url) });
    });
    await page.goto('/overview');
    await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
    await expect(page).toHaveURL(/\/login$/);
    expect(reads).toBe(0);
    await page.screenshot({ path: info.outputPath('admin-login.png'), fullPage: true });
    await page.getByLabel('관리자 아이디').fill('fixture-admin');
    await page.getByLabel('비밀번호').fill('fixture-only-password');
    await page.getByLabel('비밀번호').press('Enter');
    await expect(page).toHaveURL(/\/overview$/);
    await expect(page.getByRole('table', { name: 'API 성과 이력' })).toBeVisible();
    const storage = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]));
    expect(storage).not.toContain('fixture-only-password');
    expect(storage).not.toContain('masked-fixture');
    await page.reload();
    await page.getByRole('button', { name: 'LOCAL API · READ ONLY', exact: true }).click();
    await expect(page.getByRole('button', { name: '로그아웃' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.getByRole('button', { name: '로그아웃' }).click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole('table')).toHaveCount(0);
    await expect(page.getByLabel('비밀번호')).toHaveValue('');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.getByRole('link', { name: 'DEMO 대시보드로 이동' }).click();
    await expect(page.getByRole('button', { name: 'DEMO 화면' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
}

test('Vite continues to reject business writes and unlisted authentication routes', async ({
  request,
}) => {
  for (const path of ['/api/v1/research/candidates', '/api/v1/orders', '/api/v1/auth/register']) {
    expect((await request.post(path)).status()).toBe(403);
  }
  expect((await request.get('/api/v1/auth/logout')).status()).toBe(403);
  expect((await request.put('/api/v1/auth/login')).status()).toBe(403);
});
