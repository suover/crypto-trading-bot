import {
  InvalidOverviewData,
  parseSummary,
  parsePositions,
  parsePerformance,
  parseRecommendations,
  sameScope,
} from '@/lib/overview-api-parser';
import { timestamp } from '@/lib/research-api-parser';
import type { Summary, PerformanceFilter, RecommendationFilter } from '@/types/overview-api';

export class OverviewApiError extends Error {
  constructor(
    public readonly kind: 'http' | 'network' | 'data' | 'input' | 'consistency',
    public readonly status?: number,
  ) {
    super('Overview API ' + kind);
  }
}
export const overviewAuthenticationPath = '/api/v1/overview/summary';
async function get<T>(path: string, read: (raw: string) => T, signal?: AbortSignal): Promise<T> {
  try {
    const response = await fetch('/api/v1/overview' + path, {
      method: 'GET',
      signal,
      credentials: 'same-origin',
      redirect: 'error',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new OverviewApiError('http', response.status);
    if (!response.headers.get('content-type')?.toLowerCase().includes('application/json'))
      throw new OverviewApiError('data');
    return read(await response.text());
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    if (error instanceof OverviewApiError) throw error;
    throw new OverviewApiError(error instanceof InvalidOverviewData ? 'data' : 'network');
  }
}
function pagination(page: number, size: number, max: number) {
  if (
    !Number.isSafeInteger(page) ||
    page < 0 ||
    !Number.isInteger(size) ||
    size < 1 ||
    size > max ||
    page * size > 1_000_000
  )
    throw new OverviewApiError('input');
}
export function performanceQuery(f: PerformanceFilter): string {
  pagination(f.page, f.size, 200);
  try {
    for (const value of [f.from, f.to]) if (value) timestamp(value);
  } catch {
    throw new OverviewApiError('input');
  }
  if (f.from && f.to && Date.parse(f.from) > Date.parse(f.to)) throw new OverviewApiError('input');
  const params = new URLSearchParams({ page: String(f.page), size: String(f.size) });
  if (f.from) params.set('from', f.from);
  if (f.to) params.set('to', f.to);
  return params.toString();
}
export function recommendationQuery(f: RecommendationFilter): string {
  pagination(f.page, f.size, 50);
  if (!['', 'BUY', 'SELL', 'HOLD'].includes(f.action)) throw new OverviewApiError('input');
  const params = new URLSearchParams({ page: String(f.page), size: String(f.size) });
  if (f.action) params.set('action', f.action);
  return params.toString();
}
export const overviewApi = {
  summary: (signal?: AbortSignal) => get('/summary', parseSummary, signal),
  // Retry only an ID mismatch once; never retry authentication/network failures automatically.
  async positions(summary: Summary, signal?: AbortSignal) {
    for (let attempt = 0; attempt < 2; attempt++) {
      signal?.throwIfAborted();
      const result = await get('/positions', parsePositions, signal);
      if (!sameScope(result.scope, summary.scope)) throw new OverviewApiError('consistency');
      if (
        result.portfolio?.snapshotId === summary.portfolio?.snapshotId &&
        result.portfolio?.capturedAt === summary.portfolio?.capturedAt
      )
        return result;
    }
    throw new OverviewApiError('consistency');
  },
  performance: (f: PerformanceFilter, signal?: AbortSignal) =>
    get(
      '/performance?' + performanceQuery(f),
      (raw) => {
        const result = parsePerformance(raw);
        if (
          result.page !== f.page ||
          result.size !== f.size ||
          result.items.some(
            (p) =>
              (f.from && Date.parse(p.periodEndAt) < Date.parse(f.from)) ||
              (f.to && Date.parse(p.periodEndAt) > Date.parse(f.to)),
          )
        )
          throw new InvalidOverviewData();
        return result;
      },
      signal,
    ),
  recommendations: (f: RecommendationFilter, signal?: AbortSignal) =>
    get(
      '/recommendations?' + recommendationQuery(f),
      (raw) => {
        const result = parseRecommendations(raw);
        if (
          result.page !== f.page ||
          result.size !== f.size ||
          (f.action && result.items.some((r) => r.action !== f.action))
        )
          throw new InvalidOverviewData();
        return result;
      },
      signal,
    ),
};
