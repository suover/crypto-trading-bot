import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  // Any accidental API or CDN access is blocked, recorded and fails this test.
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url());
    if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') {
      throw new Error(`Unexpected external request: ${url.origin}`);
    }
    return route.continue();
  });
});

const routes = [
  ['/overview', '시장을 읽고, 흐름을 한눈에.'],
  ['/ai-analysis', '판단의 근거를, 한눈에'],
  ['/portfolio', '자산의 흐름을 읽다'],
  ['/research', '증거를 쌓고, 정책을 검증합니다.'],
  ['/operations', '운영 모니터링'],
] as const;

for (const width of [375, 768, 1024, 1440]) {
  for (const [route, title] of routes) {
    test(`${width}px ${route}: readable, no page overflow, no runtime errors`, async ({
      page,
    }, testInfo) => {
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.setViewportSize({ width, height: 900 });
      await page.goto(route);
      await expect(page.getByRole('heading', { level: 1, name: title, exact: true })).toBeVisible();
      await expect(page.getByText('DEMO', { exact: true })).toBeVisible();
      await expect
        .poll(() =>
          page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
        )
        .toBe(true);
      await page.screenshot({ path: testInfo.outputPath('screen.png'), fullPage: false });
      expect(errors).toEqual([]);
    });
  }
}

test('redirect, sidebar, theme, mobile navigation and 404', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveURL(/\/overview$/);
  await page.getByRole('button', { name: '사이드바 접기' }).click();
  await expect(page.getByRole('button', { name: '사이드바 펼치기' })).toBeVisible();
  await page.getByRole('button', { name: '라이트 모드로 전환' }).click();
  await expect(page.locator('html')).toHaveClass('light');
  await page.reload();
  await expect(page.locator('html')).toHaveClass('light');
  await page.setViewportSize({ width: 375, height: 812 });
  await page.getByRole('button', { name: '메뉴 열기' }).click();
  const dialog = page.getByRole('dialog', { name: '주요 메뉴' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('link', { name: 'AI 매매 분석', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByRole('heading', { name: '판단의 근거를, 한눈에', level: 1 }),
  ).toBeVisible();
  await page.getByRole('button', { name: '메뉴 열기' }).click();
  await dialog.getByRole('link', { name: 'Crypto Trading Bot 대시보드', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page).toHaveURL(/\/overview$/);
  await page.goto('/does-not-exist');
  await expect(page.getByRole('heading', { name: '페이지를 찾을 수 없습니다' })).toBeVisible();
  await page.getByRole('link', { name: '대시보드로 돌아가기' }).click();
  await expect(page).toHaveURL(/\/overview$/);
});

test('chart range changes actual point count and Demo scenario switching', async ({ page }) => {
  await page.goto('/overview');
  await expect(page.locator('.worker-list')).toContainText('Recommendation Outcome');
  await expect(page.getByRole('img', { name: /계좌 가치 추이, 30개 시점/ })).toBeVisible();
  await page.getByRole('button', { name: '7일', exact: true }).click();
  await expect(page.getByRole('img', { name: /계좌 가치 추이, 7개 시점/ })).toBeVisible();
  await page.getByRole('button', { name: '90일', exact: true }).click();
  await expect(page.getByRole('img', { name: /계좌 가치 추이, 90개 시점/ })).toBeVisible();
  await page.getByLabel('데모 데이터 상태').selectOption('loading');
  await expect(page.getByRole('status', { name: '데이터 불러오는 중' })).toBeVisible();
  await page.getByLabel('데모 데이터 상태').selectOption('error');
  await expect(page.getByRole('alert')).toContainText('데모 데이터를 불러오지 못했습니다');
  await page.getByRole('button', { name: '다시 시도' }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.getByLabel('데모 데이터 상태').selectOption('empty');
  await expect(page.getByText('선택한 기간의 자산 데이터가 없습니다')).toBeVisible();
  await page.getByLabel('데모 데이터 상태').selectOption('partial');
  await expect(page.locator('.metric-highlight .metric-value')).toHaveText('—');
  await page.getByLabel('데모 데이터 상태').selectOption('normal');
  await expect(page.locator('.metric-highlight .metric-value')).toHaveText('₩24,851,600');
});

test('analysis filters, pages, detail drawer keyboard and mobile bounds', async ({
  page,
}, testInfo) => {
  await page.goto('/ai-analysis');
  await page.getByLabel('마켓 검색').fill('BTC');
  await page.getByLabel('판단 필터').selectOption('HOLD');
  const table = page.locator('tbody');
  await expect(table).toContainText('KRW-BTC');
  await expect(table).not.toContainText('SELL');
  await page.getByLabel('마켓 검색').fill('');
  await page.getByLabel('판단 필터').selectOption('ALL');
  await page.getByRole('button', { name: '다음 분석 페이지' }).click();
  await page.getByRole('button', { name: '이전 분석 페이지' }).click();
  await page.getByLabel('분석 정렬').selectOption('oldest');
  await page.getByLabel('분석 정렬').selectOption('newest');
  await page.setViewportSize({ width: 375, height: 812 });
  await page.getByRole('button', { name: /RUN-1280 분석 상세/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  const box = await dialog.boundingBox();
  expect(box?.x).toBeGreaterThanOrEqual(0);
  expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(376);
  await expect(dialog).toContainText('15m');
  await expect(dialog).toContainText('HOLD');
  await page.screenshot({ path: testInfo.outputPath('analysis-mobile-detail.png') });
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole('button', { name: 'RUN-1280 분석 상세', exact: true })).toBeFocused();
});

test('portfolio tabs, holdings pagination, execution provenance and partial NAV', async ({
  page,
}) => {
  await page.goto('/portfolio');
  await page.getByRole('button', { name: '다음 페이지', exact: true }).click();
  await page.getByLabel('보유 자산 검색').fill('BTC');
  await expect(page.locator('#portfolio-holdings tbody tr')).toHaveCount(1);
  await page.getByLabel('보유 자산 정렬').selectOption('value-asc');
  await page.getByRole('button', { name: /주문 · 체결/ }).click();
  await page
    .getByRole('button', { name: /ORDER-.* 체결 상세/ })
    .first()
    .click();
  await expect(page.getByRole('dialog')).toContainText('추천 ID');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: /Bot Trading PnL/ }).click();
  await page.getByLabel('FIFO 매칭 상태').selectOption('UNMATCHED');
  await expect(page.locator('#portfolio-pnl tbody')).toContainText('매칭 불가');
  await page.getByLabel('데모 데이터 상태').selectOption('partial');
  await expect(page.getByText(/일부 자산의 평가 정보가 부족합니다/)).toBeVisible();
  await expect(page.locator('.donut-center strong')).toHaveText('4 / 5 coins');
});

test('light theme remains readable across all routes', async ({ page }, testInfo) => {
  await page.goto('/overview');
  await page.getByRole('button', { name: '라이트 모드로 전환' }).click();
  for (const [route, title] of routes) {
    await page.goto(route);
    await expect(page.locator('html')).toHaveClass('light');
    await expect(page.getByRole('heading', { level: 1, name: title, exact: true })).toBeVisible();
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
      )
      .toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`${route.slice(1)}-light.png`) });
  }
});

test('research Gate evidence and operations filters remain read only', async ({
  page,
}, testInfo) => {
  await page.goto('/research');
  await page.getByLabel('Promotion Gate 상태 필터').selectOption('NOT_ELIGIBLE');
  await page
    .getByRole('button', { name: /상세보기/ })
    .first()
    .click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('tab', { name: 'Forward' }).click();
  await expect(page.getByRole('dialog')).toContainText('FORWARD_COST_WIN_RATE');
  await page.getByRole('tab', { name: 'Shadow' }).click();
  await expect(page.getByRole('dialog')).toContainText('승인');
  await page.screenshot({ path: testInfo.outputPath('research-detail.png') });
  await page.keyboard.press('Escape');
  await page.getByRole('link', { name: '운영 모니터링', exact: true }).click();
  await page.getByLabel('서비스 상태 필터').selectOption('ERROR');
  await expect(page.locator('.worker-grid .worker-card')).toHaveCount(1);
  await page.getByLabel('활동 유형 필터').selectOption('SHADOW');
  await expect(page.locator('.event-list')).not.toContainText('분석 시작');
  await page.getByLabel('경고 심각도 필터').selectOption('ERROR');
  await expect(page.getByText('주문 조회 응답 지연', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: /승인|활성화|주문 생성|워커 시작|해결 처리/ }),
  ).toHaveCount(0);
});
