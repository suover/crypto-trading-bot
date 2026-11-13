import {
  InvalidAnalysisData,
  parseAnalysisRecommendations,
  parseAnalysisDetail,
  parseAnalysisCandidates,
} from '@/lib/analysis-api-parser';
import { sameScope } from '@/lib/overview-api-parser';
import { timestamp, validId } from '@/lib/research-api-parser';
import type { Search, Detail, DetailReference } from '@/types/analysis-api';
export class AnalysisApiError extends Error {
  constructor(
    public readonly kind: 'http' | 'network' | 'data' | 'input' | 'consistency',
    public readonly status?: number,
  ) {
    super('AI Analysis API ' + kind);
  }
}
export const analysisAuthenticationPath = '/api/v1/ai-trade-analysis/recommendations';
async function get<T>(path: string, read: (raw: string) => T, signal?: AbortSignal): Promise<T> {
  try {
    const response = await fetch(analysisAuthenticationPath + path, {
      method: 'GET',
      signal,
      credentials: 'same-origin',
      redirect: 'error',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new AnalysisApiError('http', response.status);
    if (!response.headers.get('content-type')?.toLowerCase().includes('application/json'))
      throw new AnalysisApiError('data');
    return read(await response.text());
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    if (error instanceof AnalysisApiError) throw error;
    throw new AnalysisApiError(error instanceof InvalidAnalysisData ? 'data' : 'network');
  }
}
function pagination(page: number, size: number) {
  if (
    !Number.isSafeInteger(page) ||
    page < 0 ||
    !Number.isInteger(size) ||
    size < 1 ||
    size > 100 ||
    page * size > 1_000_000
  )
    throw new AnalysisApiError('input');
}
export function analysisSearchQuery(f: Search) {
  pagination(f.page, f.size);
  if (
    !['', 'BUY', 'SELL', 'HOLD'].includes(f.action) ||
    (f.market && (f.market.length > 30 || !/^[A-Z0-9]+-[A-Z0-9]+$/.test(f.market)))
  )
    throw new AnalysisApiError('input');
  try {
    for (const v of [f.from, f.to]) if (v) timestamp(v);
  } catch {
    throw new AnalysisApiError('input');
  }
  if (f.from && f.to && instantOrder(f.from, f.to) > 0) throw new AnalysisApiError('input');
  const p = new URLSearchParams({ page: String(f.page), size: String(f.size) });
  for (const key of ['from', 'to', 'market', 'action'] as const) if (f[key]) p.set(key, f[key]);
  return p.toString();
}
// Compare UTC instants without losing sub-millisecond precision.
export function instantOrder(a: string, b: string): number {
  const normalized = (s: string) =>
    s.slice(0, 19) + '.' + s.slice(19, -1).replace(/^\./, '').padEnd(9, '0');
  return normalized(a) < normalized(b) ? -1 : normalized(a) > normalized(b) ? 1 : 0;
}
export const analysisApi = {
  recommendations(f: Search, signal?: AbortSignal) {
    return get(
      '?' + analysisSearchQuery(f),
      (raw) => {
        const p = parseAnalysisRecommendations(raw);
        if (
          p.page !== f.page ||
          p.size !== f.size ||
          p.items.some(
            (r) =>
              (f.market && r.market !== f.market) ||
              (f.action && r.action !== f.action) ||
              (f.from && instantOrder(r.createdAt, f.from) < 0) ||
              (f.to && instantOrder(r.createdAt, f.to) > 0),
          )
        )
          throw new InvalidAnalysisData();
        return p;
      },
      signal,
    );
  },
  detail(ref: DetailReference, signal?: AbortSignal) {
    if (!validId(ref.id)) throw new AnalysisApiError('input');
    return get(
      '/' + ref.id,
      (raw) => {
        const d = parseAnalysisDetail(raw);
        if (d.recommendation.id !== ref.id || (ref.scope && !sameScope(ref.scope, d.scope)))
          throw new AnalysisApiError('consistency');
        return d;
      },
      signal,
    );
  },
  candidates(d: Detail, page: number, size: number, signal?: AbortSignal) {
    pagination(page, size);
    if (!validId(d.recommendation.id) || d.universe.status !== 'VERIFIED')
      throw new AnalysisApiError('input');
    return get(
      `/${d.recommendation.id}/candidates?page=${page}&size=${size}`,
      (raw) => {
        const p = parseAnalysisCandidates(raw);
        if (
          !sameScope(p.scope, d.scope) ||
          p.universe.status !== 'VERIFIED' ||
          p.universe.analysisRunId !== d.universe.analysisRunId ||
          p.universe.selectedCandidateId !== d.universe.selectedCandidateId
        )
          throw new AnalysisApiError('consistency');
        if (p.page !== page || p.size !== size) throw new InvalidAnalysisData();
        return p;
      },
      signal,
    );
  },
};
