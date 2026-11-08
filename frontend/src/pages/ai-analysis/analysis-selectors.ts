import type { AnalysisRun, ApprovalStatus, TradeAction } from '@/types/dashboard';

export interface AnalysisFilters {
  search: string;
  action: TradeAction | 'ALL';
  approval: ApprovalStatus | 'ALL';
  days: number | null;
  sort: 'newest' | 'oldest';
}

export function selectAnalyses(
  analyses: AnalysisRun[],
  filters: AnalysisFilters,
  asOf: string,
): AnalysisRun[] {
  const search = filters.search.trim().toUpperCase();
  const end = Date.parse(asOf);
  const start = filters.days === null ? -Infinity : end - filters.days * 86_400_000;
  return analyses
    .filter((analysis) => {
      const timestamp = Date.parse(analysis.startedAt);
      return (
        analysis.recommendation.market.toUpperCase().includes(search) &&
        (filters.action === 'ALL' || analysis.recommendation.action === filters.action) &&
        (filters.approval === 'ALL' ||
          analysis.recommendation.approvalStatus === filters.approval) &&
        timestamp >= start &&
        timestamp <= end
      );
    })
    .sort((left, right) => {
      const difference = Date.parse(right.startedAt) - Date.parse(left.startedAt);
      return filters.sort === 'newest' ? difference : -difference;
    });
}
