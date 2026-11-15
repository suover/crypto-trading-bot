import { expect, test } from '@playwright/test';

test('real HTTPS guest → own data → reload → logout → demo', async ({ page, context }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/overview');
  await expect(page.getByText('DEMO', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: '관리자 로그인' }).click();
  await page.getByLabel('관리자 아이디').fill('fixture-admin');
  await page.getByLabel('비밀번호').fill('admin-fixture-only');
  await page.getByLabel('비밀번호').press('Enter');
  await expect(page.getByRole('table', { name: 'API 성과 이력' })).toBeVisible();
  const cookie = (await context.cookies()).find((item) => item.name === 'JSESSIONID');
  expect(cookie).toMatchObject({ secure: true, httpOnly: true, sameSite: 'Strict' });
  await page.reload();
  await expect(page.getByRole('button', { name: '로그아웃' })).toBeVisible();
  for (const path of ['/research', '/ai-analysis']) {
    await page.goto(path);
    await expect(page.getByRole('button', { name: '로그아웃' })).toBeVisible();
    await expect(page.getByText('DEMO', { exact: true })).toHaveCount(0);
  }
  await page.getByRole('button', { name: '로그아웃' }).click();
  await expect(page.getByText('DEMO', { exact: true })).toBeVisible();
  expect((await context.request.get('/api/v1/auth/me')).status()).toBe(401);
  expect(errors).toEqual([]);
});

test('HTTPS enforces CSRF, session rotation, scoped reads, logout invalidation and no writes', async ({
  request,
}) => {
  expect((await request.get('/api/v1/overview/summary')).status()).toBe(401);
  expect((await request.post('/api/v1/auth/login')).status()).toBe(403);
  let csrf = await (await request.get('/api/v1/auth/csrf')).json();
  const before = (await request.storageState()).cookies.find((c) => c.name === 'JSESSIONID');
  expect(
    (
      await request.post('/api/v1/auth/login', {
        headers: { [csrf.headerName]: csrf.token },
        form: { username: 'fixture-admin', password: 'admin-fixture-only' },
      })
    ).status(),
  ).toBe(204);
  const after = (await request.storageState()).cookies.find((c) => c.name === 'JSESSIONID');
  expect(after?.value).not.toBe(before?.value);
  expect(after).toMatchObject({ secure: true, httpOnly: true, sameSite: 'Strict' });
  for (const path of [
    '/api/v1/overview/summary',
    '/api/v1/research/summary',
    '/api/v1/ai-trade-analysis/recommendations',
  ]) {
    const response = await request.get(path);
    expect(response.status()).toBe(200);
    expect(response.headers()['cache-control']).toBe('no-store');
  }
  expect((await request.get('/api/v1/research/candidates/204')).status()).toBe(404);
  expect((await request.get('/api/v1/ai-trade-analysis/recommendations/4004')).status()).toBe(404);
  csrf = await (await request.get('/api/v1/auth/csrf')).json();
  for (const path of ['/api/v1/orders', '/api/v1/research/candidates', '/api/v1/auth/register']) {
    expect(
      (await request.post(path, { headers: { [csrf.headerName]: csrf.token } })).status(),
    ).toBe(403);
  }
  expect((await request.post('/api/v1/auth/logout')).status()).toBe(403);
  expect(
    (
      await request.post('/api/v1/auth/logout', { headers: { [csrf.headerName]: csrf.token } })
    ).status(),
  ).toBe(204);
  expect(
    (
      await request.get('/api/v1/auth/me', { headers: { Cookie: `JSESSIONID=${after?.value}` } })
    ).status(),
  ).toBe(401);
  csrf = await (await request.get('/api/v1/auth/csrf')).json();
  expect(
    (
      await request.post('/api/v1/auth/login', {
        headers: { [csrf.headerName]: csrf.token },
        form: { username: 'fixture-other', password: 'admin-fixture-only' },
      })
    ).status(),
  ).toBe(204);
  expect((await request.get('/api/v1/ai-trade-analysis/recommendations/4001')).status()).toBe(404);
});

test('API errors never become SPA HTML and missing static files stay 404', async ({ request }) => {
  const denied = await request.get('/api/v1/unlisted', { headers: { Accept: 'text/html' } });
  expect(denied.status()).toBe(401);
  expect(denied.headers()['content-type']).toContain('application/json');
  expect(denied.headers()['cache-control']).toBe('no-store');
  expect((await request.get('/assets/missing.js')).status()).toBe(404);
  const page = await request.get('/research');
  expect(page.status()).toBe(200);
  expect(page.headers()['x-frame-options']).toBe('DENY');
  expect(page.headers()['x-content-type-options']).toBe('nosniff');
  expect(page.headers()['strict-transport-security']).toBe('max-age=31536000');
  expect((await (await request.get('/release.json')).json()).revision).toBe('local-web-test');
});

test('forged forwarded IP headers cannot bypass public login throttling', async ({ request }) => {
  const csrf = await (await request.get('/api/v1/auth/csrf')).json();
  let limited = false;
  for (let i = 1; i <= 15; i++) {
    const response = await request.post('/api/v1/auth/login', {
      headers: {
        [csrf.headerName]: csrf.token,
        'X-Forwarded-For': `198.51.100.${i}`,
        Forwarded: `for=198.51.100.${i}`,
      },
      form: { username: 'missing', password: 'fixture-only' },
    });
    expect([401, 429]).toContain(response.status());
    if (response.status() === 429) {
      expect(response.headers()['retry-after']).toBe('60');
      limited = true;
      break;
    }
  }
  expect(limited).toBe(true);
});
