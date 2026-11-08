import { describe, expect, it } from 'vitest';
import { dashboardData } from '@/data/mock/dashboard';
import { selectAnalyses, type AnalysisFilters } from './analysis-selectors';

const filters: AnalysisFilters = {
  search: '',
  action: 'ALL',
  approval: 'ALL',
  days: null,
  sort: 'newest',
};

describe('analysis query controls', () => {
  it('combines case-insensitive market search, action and approval filters', () => {
    const result = selectAnalyses(
      dashboardData.analyses,
      { ...filters, search: ' eth ', action: 'BUY', approval: 'APPROVED' },
      dashboardData.asOf,
    );
    expect(result.length).toBeGreaterThan(0);
    expect(
      result.every(
        (run) =>
          run.recommendation.market === 'KRW-ETH' &&
          run.recommendation.action === 'BUY' &&
          run.recommendation.approvalStatus === 'APPROVED',
      ),
    ).toBe(true);
  });

  it('uses the frozen data timestamp for the period rather than the machine clock', () => {
    const result = selectAnalyses(
      dashboardData.analyses,
      { ...filters, days: 7 },
      dashboardData.asOf,
    );
    expect(result.length).toBeGreaterThan(0);
    expect(result.length).toBeLessThan(dashboardData.analyses.length);
    const end = Date.parse(dashboardData.asOf);
    expect(
      result.every(
        (run) =>
          Date.parse(run.startedAt) >= end - 7 * 86_400_000 && Date.parse(run.startedAt) <= end,
      ),
    ).toBe(true);
  });

  it('sorts both ways without mutating the shared data', () => {
    const original = dashboardData.analyses.map((run) => run.id);
    const newest = selectAnalyses(dashboardData.analyses, filters, dashboardData.asOf);
    const oldest = selectAnalyses(
      dashboardData.analyses,
      { ...filters, sort: 'oldest' },
      dashboardData.asOf,
    );
    expect(oldest.map((run) => run.id)).toEqual(newest.map((run) => run.id).reverse());
    expect(dashboardData.analyses.map((run) => run.id)).toEqual(original);
  });

  it('keeps HOLD as an ordinary recommendation with no approval required', () => {
    const result = selectAnalyses(
      dashboardData.analyses,
      { ...filters, action: 'HOLD' },
      dashboardData.asOf,
    );
    expect(result.length).toBeGreaterThan(0);
    expect(
      result.every(
        (run) =>
          run.recommendation.approvalStatus === 'NOT_REQUIRED' &&
          run.recommendation.orderId === null,
      ),
    ).toBe(true);
  });

  it('returns an empty result for an unknown market', () => {
    expect(
      selectAnalyses(
        dashboardData.analyses,
        { ...filters, search: 'missing-market' },
        dashboardData.asOf,
      ),
    ).toEqual([]);
  });
});
