import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { dashboardData } from '../../data/mock/dashboard';
import ResearchPage from './ResearchPage';

vi.mock('../../hooks/use-dashboard-data', () => ({
  useDashboardData: () => ({ data: dashboardData }),
}));

describe('Research workspace', () => {
  it('keeps research candidates inactive and renders source lifecycle records', () => {
    render(<ResearchPage />);
    expect(
      screen.getByRole('heading', { name: '증거를 쌓고, 정책을 검증합니다.' }),
    ).toBeInTheDocument();
    const lifecycle = screen.getByRole('list', { name: '선택한 후보의 연구 단계' });
    expect(within(lifecycle).getAllByRole('listitem')).toHaveLength(11);
    expect(within(lifecycle).getByText('기준 미충족')).toBeInTheDocument();
    const table = screen.getByRole('table', {
      name: '등록된 Ranking 정책과 Gate, Shadow, 활성화 상태',
    });
    expect(within(table).getAllByText('비활성')).toHaveLength(4);
    expect(screen.queryByRole('button', { name: /승인|활성화|등록 실행/ })).not.toBeInTheDocument();
    expect(screen.getByText('BASELINE')).toBeInTheDocument();
  });

  it('filters by real Gate status and searches candidate IDs with an empty state', async () => {
    const user = userEvent.setup();
    render(<ResearchPage />);
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Promotion Gate 상태 필터' }),
      'NOT_ELIGIBLE',
    );
    let table = screen.getByRole('table', {
      name: '등록된 Ranking 정책과 Gate, Shadow, 활성화 상태',
    });
    expect(within(table).getAllByRole('row')).toHaveLength(2);
    expect(within(table).getByText('RPC-104')).toBeInTheDocument();
    await user.type(screen.getByRole('textbox', { name: '연구 후보 검색' }), 'RPC-103');
    expect(screen.getByText('조건에 맞는 연구 후보가 없습니다')).toBeInTheDocument();
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Promotion Gate 상태 필터' }),
      'ALL',
    );
    table = screen.getByRole('table', { name: '등록된 Ranking 정책과 Gate, Shadow, 활성화 상태' });
    expect(within(table).getByText('RPC-103')).toBeInTheDocument();
    expect(within(table).queryByText('RPC-104')).not.toBeInTheDocument();
  });

  it('changes comparison data without changing the active runtime policy', async () => {
    const user = userEvent.setup();
    render(<ResearchPage />);
    await user.selectOptions(screen.getByRole('combobox', { name: '비교할 연구 후보' }), 'RPC-103');
    expect(screen.getByLabelText('후보 Liquidity')).toHaveTextContent('40%');
    expect(screen.getByText('BASELINE')).toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: '비교할 연구 후보' }), 'RPC-106');
    expect(screen.getByText('Forward 성과를 계산할 수 없습니다')).toBeInTheDocument();
    const evidence = screen.getByRole('table', { name: /기간별 Forward 연구 성과/ });
    expect(within(evidence).getAllByText('—')).toHaveLength(15);
    expect(within(evidence).queryByText('0.00%')).not.toBeInTheDocument();
  });

  it('opens provenance and Gate details, supports keyboard tabs and closes with Escape', async () => {
    const user = userEvent.setup();
    render(<ResearchPage />);
    await user.click(screen.getByRole('button', { name: 'Momentum Balanced 상세보기' }));
    const dialog = screen.getByRole('dialog', { name: 'Momentum Balanced' });
    expect(within(dialog).getByText('RS-1082')).toBeInTheDocument();
    expect(within(dialog).getByRole('tab', { name: 'Forward' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(within(dialog).getByText('FORWARD_COST_WIN_RATE')).toBeInTheDocument();
    const forwardTab = within(dialog).getByRole('tab', { name: 'Forward' });
    await user.click(forwardTab);
    await user.keyboard('{Home}');
    expect(within(dialog).getByRole('tab', { name: 'Historical' })).toHaveFocus();
    expect(within(dialog).getByText('등록 시점으로 제한한 Historical')).toBeInTheDocument();
    await user.keyboard('{End}');
    expect(within(dialog).getByRole('tab', { name: 'Shadow' })).toHaveFocus();
    expect(within(dialog).getByText('Shadow에 등록되지 않은 후보입니다')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows Shadow enrollment independently of human approval and Full LIVE activation', async () => {
    const user = userEvent.setup();
    render(<ResearchPage />);
    await user.click(screen.getByRole('button', { name: 'Liquidity Defensive 상세보기' }));
    const dialog = screen.getByRole('dialog', { name: 'Liquidity Defensive' });
    await user.click(within(dialog).getByRole('tab', { name: 'Shadow' }));
    expect(within(dialog).getByText('SE-12')).toBeInTheDocument();
    expect(within(dialog).getByText('데이터 부족')).toBeInTheDocument();
    expect(within(dialog).getByText('승인 기록 없음')).toBeInTheDocument();
    expect(within(dialog).getByText('활성화 기록 없음')).toBeInTheDocument();
  });

  it('does not label missing integrity checks as a successful Gate', async () => {
    const user = userEvent.setup();
    render(<ResearchPage />);
    await user.selectOptions(screen.getByRole('combobox', { name: '비교할 연구 후보' }), 'RPC-105');
    expect(screen.getByText(/제공된 검증 항목이 없습니다/)).toBeInTheDocument();
    expect(
      screen.queryByText(/표시된 검증 항목에서 기준 미충족이 없습니다/),
    ).not.toBeInTheDocument();
  });
});
