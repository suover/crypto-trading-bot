import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { dashboardData } from '@/data/mock/dashboard';
import AnalysisPage from './AnalysisPage';

vi.mock('@/hooks/use-dashboard-data', () => ({
  useDashboardData: () => ({ data: dashboardData }),
}));
afterEach(cleanup);

function renderPage(path = '/ai-analysis') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AnalysisPage />
    </MemoryRouter>,
  );
}

describe('analysis interactions', () => {
  it('filters market/action/approval and shows a recoverable empty state', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByRole('textbox', { name: '마켓 검색' }), 'ETH');
    await user.selectOptions(screen.getByRole('combobox', { name: '판단 필터' }), 'BUY');
    await user.selectOptions(screen.getByRole('combobox', { name: '승인 상태 필터' }), 'APPROVED');
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1);
    expect(rows.length).toBeGreaterThan(0);
    rows.forEach((row) => {
      expect(within(row).getByText('KRW-ETH')).toBeInTheDocument();
      expect(within(row).getByText('BUY')).toBeInTheDocument();
      expect(within(row).getByText('승인됨')).toBeInTheDocument();
    });
    await user.clear(screen.getByRole('textbox', { name: '마켓 검색' }));
    await user.type(screen.getByRole('textbox', { name: '마켓 검색' }), 'not-a-market');
    expect(screen.getByText('조건에 맞는 분석이 없습니다')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '필터 초기화' }));
    expect(screen.getByRole('table')).toBeInTheDocument();
  });

  it('changes pages and date/sort controls reset to the first page', async () => {
    const user = userEvent.setup();
    renderPage();
    const first = dashboardData.analyses[0]!;
    expect(screen.getByRole('button', { name: `${first.id} 분석 상세` })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '다음 분석 페이지' }));
    expect(screen.queryByRole('button', { name: `${first.id} 분석 상세` })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: '분석 기간' }), '7');
    expect(screen.getByRole('button', { name: `${first.id} 분석 상세` })).toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: '분석 기간' }), 'ALL');
    await user.selectOptions(screen.getByRole('combobox', { name: '분석 정렬' }), 'oldest');
    expect(
      screen.getByRole('button', { name: `${dashboardData.analyses.at(-1)!.id} 분석 상세` }),
    ).toBeInTheDocument();
  });

  it('opens a keyboard-dismissable deep link and distinguishes HOLD from execution', async () => {
    const user = userEvent.setup();
    const hold = dashboardData.analyses.find((run) => run.recommendation.action === 'HOLD')!;
    renderPage(`/ai-analysis?analysis=${hold.id}`);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(hold.pipelineRunId)).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        'HOLD는 정상적인 관망 판단입니다. 승인 요청이나 주문을 생성하지 않습니다.',
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('다중 시간봉 분석')).toBeInTheDocument();
    expect(
      within(dialog).queryByRole('button', { name: /승인|주문 실행/ }),
    ).not.toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows an explicit missing record for an invalid deep-link ID', () => {
    renderPage('/ai-analysis?analysis=missing');
    expect(within(screen.getByRole('dialog')).getByText('분석 기록 없음')).toBeInTheDocument();
  });
});
