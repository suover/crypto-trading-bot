import { reportUnauthorized } from './auth-api';
import {
  InvalidApiData,
  parseSummary,
  parseCandidates,
  parseDetail,
  parseSnapshots,
  parseAutomation,
  validId,
} from '@/lib/research-api-parser';
import type { CandidateFilters } from '@/types/research-api';

export class ResearchApiError extends Error {
  constructor(
    public readonly kind: 'http' | 'network' | 'data' | 'input',
    public readonly status?: number,
  ) {
    super(kind === 'http' ? `Research API HTTP ${status}` : `Research API ${kind}`);
  }
}
const base = '/api/v1/research';
export const authenticationPath = base + '/summary';
async function get<T>(path: string, read: (raw: string) => T, signal?: AbortSignal): Promise<T> {
  try {
    const response = await fetch(base + path, {
      method: 'GET',
      signal,
      credentials: 'same-origin',
      redirect: 'error',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    reportUnauthorized(response.status, signal);
    if (!response.ok) throw new ResearchApiError('http', response.status);
    if (!response.headers.get('content-type')?.toLowerCase().includes('application/json'))
      throw new ResearchApiError('data');
    return read(await response.text());
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
    if (error instanceof ResearchApiError) throw error;
    if (error instanceof InvalidApiData) throw new ResearchApiError('data');
    throw new ResearchApiError('network');
  }
}
function pagination(page: number, size: number) {
  if (
    !Number.isInteger(page) ||
    page < 0 ||
    page > 2147483647 ||
    !Number.isInteger(size) ||
    size < 1 ||
    size > 100
  )
    throw new ResearchApiError('input');
}
export function candidateQuery(f: CandidateFilters): string {
  pagination(f.page, f.size);
  if (
    f.keyword.length > 80 ||
    !/^(registeredAt|policyName|id),(asc|desc)$/.test(f.sort) ||
    !['', 'true', 'false'].includes(f.shadowEnrolled)
  )
    throw new ResearchApiError('input');
  for (const date of [f.registeredFrom, f.registeredTo]) {
    if (date && !Number.isFinite(Date.parse(date))) throw new ResearchApiError('input');
  }
  if (
    f.registeredFrom &&
    f.registeredTo &&
    Date.parse(f.registeredFrom) > Date.parse(f.registeredTo)
  )
    throw new ResearchApiError('input');
  const params = new URLSearchParams({ page: String(f.page), size: String(f.size), sort: f.sort });
  for (const key of ['keyword', 'registeredFrom', 'registeredTo', 'shadowEnrolled'] as const)
    if (f[key]) params.set(key, f[key]);
  return params.toString();
}
export const researchApi = {
  summary: (signal?: AbortSignal) => get('/summary', parseSummary, signal),
  candidates: (f: CandidateFilters, signal?: AbortSignal) =>
    get(
      '/candidates?' + candidateQuery(f),
      (raw) => {
        const result = parseCandidates(raw);
        if (result.page !== f.page || result.size !== f.size) throw new InvalidApiData();
        return result;
      },
      signal,
    ),
  detail: (id: string, signal?: AbortSignal) => {
    if (!validId(id)) return Promise.reject(new ResearchApiError('input'));
    return get(
      '/candidates/' + encodeURIComponent(id),
      (raw) => {
        const detail = parseDetail(raw);
        if (detail.id !== id) throw new InvalidApiData();
        return detail;
      },
      signal,
    );
  },
  snapshots: (page: number, size: number, signal?: AbortSignal) => {
    pagination(page, size);
    return get(
      '/snapshots?' + new URLSearchParams({ page: String(page), size: String(size) }),
      (raw) => {
        const result = parseSnapshots(raw);
        if (result.page !== page || result.size !== size) throw new InvalidApiData();
        return result;
      },
      signal,
    );
  },
  automation: (signal?: AbortSignal) => get('/automation-status', parseAutomation, signal),
};
