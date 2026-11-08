import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { TooltipProvider } from '@/components/ui/tooltip';
import { dashboardData } from '@/data/mock/dashboard';
import type { DashboardData } from '@/types/dashboard';
import PortfolioPage from './PortfolioPage';

let currentData: DashboardData = dashboardData;
vi.mock('@/hooks/use-dashboard-data', () => ({ useDashboardData: () => ({ data: currentData }) }));
vi.mock('@/components/charts/PortfolioChart', () => ({
  PortfolioChart: () => <div>Portfolio chart</div>,
}));
afterEach(cleanup);
beforeEach(() => {
  currentData = dashboardData;
});

function renderPage() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <PortfolioPage />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

describe('portfolio interactions', () => {
  it('searches, sorts and paginates holdings', async () => {
    const user = userEvent.setup();
    renderPage();
    expect(screen.getByRole('button', { name: '다음 페이지' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: '다음 페이지' }));
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(2);
    await user.type(screen.getByRole('textbox', { name: '보유 자산 검색' }), '이더리움');
    expect(within(screen.getByRole('table')).getByText('이더리움')).toBeInTheDocument();
    expect(within(screen.getByRole('table')).queryByText('비트코인')).not.toBeInTheDocument();
    await user.clear(screen.getByRole('textbox', { name: '보유 자산 검색' }));
    await user.selectOptions(screen.getByRole('combobox', { name: '보유 자산 정렬' }), 'asset');
    expect(within(screen.getByRole('table')).getAllByRole('row')[1]).toHaveTextContent('비트코인');
  });

  it('shows executed records separately and can open the actual fill detail', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('button', { name: /주문 · 체결/ }));
    await user.selectOptions(screen.getByRole('combobox', { name: '체결 방향 필터' }), 'SELL');
    expect(within(screen.getByRole('table')).queryByText('BUY')).not.toBeInTheDocument();
    const order = dashboardData.portfolio.orders.find((item) => item.side === 'SELL')!;
    await user.click(screen.getByRole('button', { name: `${order.id} 체결 상세` }));
    expect(screen.getByRole('dialog')).toHaveTextContent(order.id);
    expect(screen.getByRole('dialog')).toHaveTextContent('개별 체결');
    expect(
      within(screen.getByRole('dialog')).getByRole('link', { name: /연결된 AI 분석 보기/ }),
    ).toHaveAttribute('href', expect.stringContaining('/ai-analysis?analysis='));
  });

  it('keeps unmatched FIFO PnL unknown rather than reporting profit', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('button', { name: /Bot Trading PnL/ }));
    await user.selectOptions(screen.getByRole('combobox', { name: 'FIFO 매칭 상태' }), 'UNMATCHED');
    const row = within(screen.getByRole('table')).getAllByRole('row')[1]!;
    expect(row).toHaveTextContent('원가 미입증');
    expect(within(row).getAllByText('—')).toHaveLength(3);
  });

  it('does not show a partial NAV as zero or as a complete valuation', () => {
    currentData = structuredClone(dashboardData);
    currentData.portfolio.summary.valuationStatus = 'PARTIAL';
    currentData.portfolio.summary.totalValueKrw = null;
    currentData.portfolio.summary.accountReturnPercent = null;
    renderPage();
    const metric = screen.getByText('총 계좌 평가금액').closest('article');
    expect(metric).toHaveTextContent('—');
    expect(metric).not.toHaveTextContent('₩0');
    expect(
      screen.getByText(/총 평가금액·전체 비중·수익률은 확정하지 않습니다/),
    ).toBeInTheDocument();
  });
});
