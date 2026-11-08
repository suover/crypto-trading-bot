import { describe, expect, it } from 'vitest';
import { filterPerformancePoints } from './performance-range';
import type { PortfolioPerformancePoint } from '@/types/dashboard';

const point = (
  capturedAt: string,
  valueKrw: string | null = '1000',
): PortfolioPerformancePoint => ({
  capturedAt,
  valueKrw,
  performanceIndex: valueKrw === null ? null : 100,
  status: valueKrw === null ? 'PARTIAL' : 'COMPLETE',
});

describe('portfolio chart windows', () => {
  it('uses fixed asOf, excludes future data, and preserves missing NAV as null', () => {
    const points = [
      point('2026-11-08T03:00:00Z'),
      point('2026-11-07T03:00:00Z', null),
      point('2026-10-31T03:00:00Z'),
      point('2026-11-02T03:00:00Z'),
    ];
    const result = filterPerformancePoints(points, '7', '2026-11-07T03:00:00Z');
    expect(result.map((item) => item.capturedAt)).toEqual([
      '2026-11-02T03:00:00Z',
      '2026-11-07T03:00:00Z',
    ]);
    expect(result[1].valueKrw).toBeNull();
    expect(points[0].capturedAt).toBe('2026-11-08T03:00:00Z');
  });
  it('returns an empty window instead of inventing zero-valued data', () => {
    expect(
      filterPerformancePoints([point('2026-09-01T03:00:00Z')], '30', '2026-11-07T03:00:00Z'),
    ).toEqual([]);
  });
  it('whole history remains sorted and excludes future observations', () => {
    const points = [
      point('2026-11-08T03:00:00Z'),
      point('2026-11-07T03:00:00Z'),
      point('2026-08-01T03:00:00Z'),
    ];
    expect(
      filterPerformancePoints(points, 'all', '2026-11-07T03:00:00Z').map((item) => item.capturedAt),
    ).toEqual(['2026-08-01T03:00:00Z', '2026-11-07T03:00:00Z']);
  });
});
