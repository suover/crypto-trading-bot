import { dashboardData } from '@/data/mock/dashboard';
import type { DashboardData } from '@/types/dashboard';
import type { DashboardRepository } from './dashboard-repository';

function wait(milliseconds: number | null, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Mock read aborted', 'AbortError'));
      return;
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    const cleanup = () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
    };
    const abort = () => {
      cleanup();
      reject(new DOMException('Mock read aborted', 'AbortError'));
    };
    signal?.addEventListener('abort', abort, { once: true });
    if (milliseconds !== null)
      timer = setTimeout(() => {
        cleanup();
        resolve();
      }, milliseconds);
  });
}

export function emptyDashboard(): DashboardData {
  const data = structuredClone(dashboardData);
  data.summary = {
    ...data.summary,
    totalValueKrw: null,
    accountReturnPercent: null,
    botRealizedPnlKrw: null,
    heldAssetCount: 0,
    lastAnalysisAt: null,
    registeredCandidateCount: 0,
    shadowEnrollmentCount: 0,
    researchSnapshotCount: 0,
    latestResearchAt: null,
  };
  data.portfolio.positions = [];
  data.portfolio.orders = [];
  data.portfolio.performance = [];
  data.portfolio.summary = {
    ...data.portfolio.summary,
    totalValueKrw: null,
    knownValueKrw: null,
    unrealizedPnlKrw: null,
    positionsCostBasisKrw: null,
    cashKrw: null,
    accountReturnPercent: null,
    dailyChangeKrw: null,
    dailyChangePercent: null,
    heldAssetCount: 0,
    valuationStatus: 'PARTIAL',
  };
  data.portfolio.botPnl = {
    ...data.portfolio.botPnl,
    realizedPnlKrw: null,
    feesKrw: null,
    fillCount: 0,
    fullyMatchedCount: 0,
    partiallyMatchedCount: 0,
    unmatchedCount: 0,
    matches: [],
  };
  data.analyses = [];
  data.research.candidates = [];
  data.research.snapshots = [];
  data.research.lifecycle = {
    ...data.research.lifecycle,
    registeredCount: 0,
    processedCount: 0,
    rejectedCount: 0,
    enrolledCount: 0,
    errorCount: 0,
    lastRunAt: null,
  };
  data.operations.events = [];
  data.operations.alerts = [];
  data.operations.workers = [];
  return data;
}

export function partialDashboard(): DashboardData {
  const data = structuredClone(dashboardData);
  data.portfolio.positions.push({
    id: 'position-APENFT',
    market: null,
    asset: 'APENFT',
    name: 'APENFT · 평가 정보 부족',
    quantity: '12000',
    averageBuyPriceKrw: null,
    currentPriceKrw: null,
    valueKrw: null,
    costBasisKrw: null,
    unrealizedPnlKrw: null,
    returnPercent: null,
    weightPercent: null,
    valuationStatus: 'UNPRICED',
    priceSource: 'UNAVAILABLE',
  });
  for (const position of data.portfolio.positions) position.weightPercent = null;
  data.portfolio.summary = {
    ...data.portfolio.summary,
    totalValueKrw: null,
    unrealizedPnlKrw: null,
    positionsCostBasisKrw: null,
    accountReturnPercent: null,
    dailyChangeKrw: null,
    dailyChangePercent: null,
    heldAssetCount: 5,
    valuationStatus: 'PARTIAL',
  };
  data.summary = {
    ...data.summary,
    totalValueKrw: null,
    accountReturnPercent: null,
    heldAssetCount: 5,
  };
  const last = data.portfolio.performance.at(-1);
  if (last) {
    last.valueKrw = null;
    last.performanceIndex = null;
    last.status = 'PARTIAL';
  }
  return data;
}

export function createMockRepository(latencyMs = 180): DashboardRepository {
  return {
    async read({ scenario = 'normal', signal } = {}) {
      await wait(scenario === 'loading' ? null : latencyMs, signal);
      if (scenario === 'error')
        throw new Error('데모 오류 시나리오입니다. 실제 서버와 연결되지 않았습니다.');
      if (scenario === 'empty') return emptyDashboard();
      if (scenario === 'partial') return partialDashboard();
      return structuredClone(dashboardData);
    },
  };
}
