import type { PortfolioPerformancePoint } from '@/types/dashboard';

export type ChartRange = '7' | '30' | '90' | 'all';

/** Time-window filtering preserves null gaps and never manufactures zero NAV. */
export function filterPerformancePoints(
  points: PortfolioPerformancePoint[],
  range: ChartRange,
  asOf: string,
) {
  const end = Date.parse(asOf);
  const start = range === 'all' ? -Infinity : end - Number(range) * 86_400_000;
  return points
    .filter((point) => {
      const time = Date.parse(point.capturedAt);
      return time > start && time <= end;
    })
    .sort((a, b) => a.capturedAt.localeCompare(b.capturedAt));
}
