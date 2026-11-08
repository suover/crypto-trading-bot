import type {
  AnalysisRun,
  BotPnlMatch,
  DashboardData,
  MarketCandidate,
  OrderExecution,
  PortfolioPosition,
  TimeframeFeatures,
  TradeAction,
  WorkerStatus,
} from '@/types/dashboard';
import { research } from './research';

/** Frozen demo clock, deliberately unrelated to production account/server state. */
export const DEMO_AS_OF = '2026-11-07T03:05:00Z';
const DAY = 86_400_000;
const atDay = (daysAgo: number) =>
  new Date(Date.parse('2026-11-07T03:00:00Z') - daysAgo * DAY).toISOString();
const minutesAfter = (at: string, minutes: number) =>
  new Date(Date.parse(at) + minutes * 60_000).toISOString();

const positionInputs = [
  {
    asset: 'BTC',
    name: '비트코인',
    quantity: '0.08',
    average: 107_500_000,
    price: 115_000_000,
    cost: 8_600_000,
    value: 9_200_000,
  },
  {
    asset: 'ETH',
    name: '이더리움',
    quantity: '1.5',
    average: 4_000_000,
    price: 4_180_000,
    cost: 6_000_000,
    value: 6_270_000,
  },
  {
    asset: 'SOL',
    name: '솔라나',
    quantity: '12',
    average: 190_000,
    price: 180_000,
    cost: 2_280_000,
    value: 2_160_000,
  },
  {
    asset: 'XRP',
    name: '엑스알피',
    quantity: '400',
    average: 2_000,
    price: 2_300,
    cost: 800_000,
    value: 920_000,
  },
];
const cash = 6_301_600;
const totalValue = positionInputs.reduce((sum, position) => sum + position.value, cash);
const costBasis = positionInputs.reduce((sum, position) => sum + position.cost, 0);
const unrealized = totalValue - cash - costBasis;
const initialNav = 23_000_000;
const positions: PortfolioPosition[] = positionInputs.map((position) => ({
  id: `position-${position.asset}`,
  market: `KRW-${position.asset}`,
  asset: position.asset,
  name: position.name,
  quantity: position.quantity,
  averageBuyPriceKrw: String(position.average),
  currentPriceKrw: String(position.price),
  valueKrw: String(position.value),
  costBasisKrw: String(position.cost),
  unrealizedPnlKrw: String(position.value - position.cost),
  returnPercent: (position.value / position.cost - 1) * 100,
  weightPercent: (position.value / totalValue) * 100,
  valuationStatus: 'PRICED',
  priceSource: 'MARKET_SNAPSHOT',
}));
positions.push({
  id: 'position-KRW',
  market: null,
  asset: 'KRW',
  name: '원화 잔고',
  quantity: String(cash),
  averageBuyPriceKrw: null,
  currentPriceKrw: '1',
  valueKrw: String(cash),
  costBasisKrw: null,
  unrealizedPnlKrw: null,
  returnPercent: null,
  weightPercent: (cash / totalValue) * 100,
  valuationStatus: 'PRICED',
  priceSource: 'CASH',
});

const orderSpecs = [
  {
    index: 20,
    market: 'KRW-BTC',
    side: 'BUY' as const,
    quantity: '0.02',
    price: '110000000',
    funds: '2200000',
    fee: '1100',
    status: 'LIVE_DONE' as const,
  },
  {
    index: 18,
    market: 'KRW-ETH',
    side: 'BUY' as const,
    quantity: '0.3',
    price: '4000000',
    funds: '1200000',
    fee: '600',
    status: 'LIVE_DONE' as const,
  },
  {
    index: 10,
    market: 'KRW-BTC',
    side: 'SELL' as const,
    quantity: '0.01',
    price: '113000000',
    funds: '1130000',
    fee: '565',
    status: 'LIVE_DONE' as const,
  },
  {
    index: 8,
    market: 'KRW-ETH',
    side: 'SELL' as const,
    quantity: '0.4',
    price: '4100000',
    funds: '1640000',
    fee: '820',
    status: 'LIVE_DONE' as const,
  },
  {
    index: 5,
    market: 'KRW-XRP',
    side: 'SELL' as const,
    quantity: '100',
    price: '2250',
    funds: '225000',
    fee: '112.5',
    status: 'LIVE_DONE' as const,
  },
  {
    index: 3,
    market: 'KRW-SOL',
    side: 'BUY' as const,
    quantity: '0.8',
    price: '179500',
    funds: '143600',
    fee: '71.8',
    status: 'LIVE_EXECUTED_CANCELLED' as const,
  },
  {
    index: 1,
    market: 'KRW-ETH',
    side: 'BUY' as const,
    quantity: null,
    price: null,
    funds: null,
    fee: null,
    status: 'LIVE_UNKNOWN' as const,
  },
];

const orders: OrderExecution[] = orderSpecs
  .map((spec) => {
    const id = `ORDER-${280 - spec.index}`;
    return {
      id,
      recommendationId: `REC-${1280 - spec.index}`,
      createdAt: minutesAfter(atDay(spec.index), 3),
      market: spec.market,
      side: spec.side,
      status: spec.status,
      executedQuantity: spec.quantity,
      averagePriceKrw: spec.price,
      executedFundsKrw: spec.funds,
      feeKrw: spec.fee,
      fills:
        spec.quantity !== null && spec.price !== null && spec.funds !== null && spec.fee !== null
          ? [
              {
                id: `FILL-${280 - spec.index}`,
                orderId: id,
                filledAt: minutesAfter(atDay(spec.index), 4),
                quantity: spec.quantity,
                priceKrw: spec.price,
                fundsKrw: spec.funds,
                feeKrw: spec.fee,
              },
            ]
          : [],
    };
  })
  .sort((a, b) => b.createdAt.localeCompare(a.createdAt));

function timeframes(price: number, partial: boolean): TimeframeFeatures[] {
  return (['15m', '60m', '240m', '1d'] as const).map((timeframe, index) => {
    const missing = partial && timeframe === '1d';
    return {
      timeframe,
      trend: missing ? 'UNAVAILABLE' : index === 0 ? 'NEUTRAL' : 'UPTREND',
      quality: missing ? 'INSUFFICIENT' : 'SUFFICIENT',
      sma5: missing
        ? null
        : String(Math.round(price * (index === 0 ? 1.002 : 0.998 - index * 0.002))),
      sma20: missing ? null : String(Math.round(price * (0.995 - index * 0.003))),
      ema5: missing ? null : String(Math.round(price * (0.999 - index * 0.002))),
      ema20: missing ? null : String(Math.round(price * (0.996 - index * 0.003))),
      rsi14: missing ? null : 53.2 + index * 3.1,
      macd: missing ? null : String(Math.round(price * 0.0012)),
      atr14: missing ? null : String(Math.round(price * (0.009 + index * 0.002))),
      volatilityPercent: missing ? null : 1.2 + index * 0.4,
      volumeRatio: missing ? null : 1.18 + index * 0.05,
    };
  });
}

function marketCandidates(runId: string, daysAgo: number): MarketCandidate[] {
  return positionInputs.map((position, index) => {
    const price = Math.round(position.price * (1 - daysAgo * 0.0005));
    return {
      id: `${runId}-candidate-${position.asset}`,
      market: `KRW-${position.asset}`,
      baseAsset: position.asset,
      currentPriceKrw: String(price),
      quoteTradeValue24hKrw: String(230_000_000_000 - index * 40_000_000_000),
      rank: index < 3 ? index + 1 : null,
      score: index < 3 ? 0.82 - index * 0.07 : null,
      selectionSource: index < 3 ? 'RANKED' : 'HELD',
      buyEligible: index < 3,
      sellEligible: true,
      timeframes: timeframes(price, index === 2),
    };
  });
}

const actions: TradeAction[] = ['HOLD', 'BUY', 'SELL', 'HOLD', 'BUY'];
const analyses: AnalysisRun[] = Array.from({ length: 28 }, (_, index) => {
  const runId = `RUN-${1280 - index}`;
  const spec = orderSpecs.find((entry) => entry.index === index);
  const order = orders.find((entry) => entry.recommendationId === `REC-${1280 - index}`);
  const action = spec?.side ?? actions[index % actions.length]!;
  const approvalStatus =
    action === 'HOLD'
      ? 'NOT_REQUIRED'
      : spec
        ? 'APPROVED'
        : index % 3 === 0
          ? 'REJECTED'
          : index % 3 === 1
            ? 'PENDING'
            : 'EXPIRED';
  return {
    id: runId,
    pipelineRunId: `demo-20261107-0000-4000-8000-${String(1280 - index).padStart(12, '0')}`,
    startedAt: atDay(index),
    finishedAt: minutesAfter(atDay(index), 2),
    status: 'SUCCESS',
    universeMode: 'DYNAMIC',
    recommendation: {
      id: `REC-${1280 - index}`,
      exchange: 'UPBIT',
      market: spec?.market ?? (index % 2 === 0 ? 'KRW-BTC' : 'KRW-ETH'),
      action,
      confidence: index === 24 ? null : action === 'HOLD' ? 0.73 : 0.78 + (index % 4) * 0.02,
      tradeRatio: action === 'HOLD' ? 0 : action === 'BUY' ? 0.1 : 0.15,
      reason:
        action === 'HOLD'
          ? '상위 시간봉의 추세는 유지되지만 단기 모멘텀이 중립입니다. 현재 스프레드와 비용을 고려할 때 추가 거래보다 보유 비중 유지가 합리적입니다.'
          : action === 'BUY'
            ? '1시간·4시간 추세와 거래량 확인이 일치합니다. 보유 현금, 변동성 및 비용을 고려해 제한된 비율의 매수를 제안합니다. 정확한 주문 금액은 애플리케이션에서 계산합니다.'
            : '상승 이후 단기 변동성이 확대되어 보유 수량의 일부를 줄이는 판단입니다. 실제 체결은 Telegram 승인과 실행 안전장치 통과 이후에만 가능합니다.',
      model: 'gpt-5.6-sol',
      approvalStatus,
      status: order
        ? order.status === 'LIVE_UNKNOWN'
          ? 'LIVE_EXECUTION_UNKNOWN'
          : 'LIVE_EXECUTED'
        : action === 'HOLD'
          ? 'CREATED'
          : approvalStatus === 'PENDING'
            ? 'APPROVAL_PENDING'
            : approvalStatus === 'EXPIRED'
              ? 'APPROVAL_EXPIRED'
              : approvalStatus === 'REJECTED'
                ? 'REJECTED'
                : 'APPROVED',
      executionStatus: order?.status ?? 'NOT_EXECUTED',
      orderId: order?.id ?? null,
    },
    candidates: marketCandidates(runId, index),
  };
});

const matches: BotPnlMatch[] = [
  {
    id: 'PNL-270',
    orderId: 'ORDER-270',
    market: 'KRW-BTC',
    soldAt: minutesAfter(atDay(10), 4),
    status: 'FULLY_MATCHED',
    soldQuantity: '0.01',
    matchedQuantity: '0.01',
    costBasisKrw: '1100550',
    recognizedProceedsKrw: '1129435',
    realizedPnlKrw: '28885',
  },
  {
    id: 'PNL-272',
    orderId: 'ORDER-272',
    market: 'KRW-ETH',
    soldAt: minutesAfter(atDay(8), 4),
    status: 'PARTIALLY_MATCHED',
    soldQuantity: '0.4',
    matchedQuantity: '0.3',
    costBasisKrw: '1200600',
    recognizedProceedsKrw: '1229385',
    realizedPnlKrw: '28785',
  },
  {
    id: 'PNL-275',
    orderId: 'ORDER-275',
    market: 'KRW-XRP',
    soldAt: minutesAfter(atDay(5), 4),
    status: 'UNMATCHED',
    soldQuantity: '100',
    matchedQuantity: '0',
    costBasisKrw: null,
    recognizedProceedsKrw: null,
    realizedPnlKrw: null,
  },
];

const performance: DashboardData['portfolio']['performance'] = Array.from(
  { length: 121 },
  (_, index) => {
    const progress = index / 120;
    const value = Math.round(
      initialNav +
        (totalValue - initialNav) * progress +
        Math.sin(index * 0.31) * Math.sin(Math.PI * progress) * 420_000,
    );
    return {
      capturedAt: atDay(120 - index),
      valueKrw: String(value),
      performanceIndex: (value / initialNav) * 100,
      status: 'COMPLETE',
    };
  },
);
const previousValue = Number(performance.at(-2)!.valueKrw);

const workerDefinitions: [string, string, string, WorkerStatus['status'], string][] = [
  [
    'scheduler',
    'AI Scheduler',
    '설정 시각에 계좌·시장·AI 분석 pipeline 실행',
    'RUNNING',
    'ai-trade-scheduler',
  ],
  [
    'telegram',
    'Telegram Listener',
    'Telegram 사람 승인 callback 검증',
    'RUNNING',
    'telegram-listener',
  ],
  [
    'outcome',
    'Recommendation Outcome',
    '추천 이후 시장 움직임 평가',
    'RUNNING',
    'recommendation-outcome-worker',
  ],
  [
    'candidate-outcome',
    'Research Candidate Outcome',
    '전체 연구 후보의 미래 Gross 움직임 평가',
    'RUNNING',
    'recommendation-outcome-worker',
  ],
  [
    'lifecycle',
    'Research Lifecycle',
    '기존 후보 Gate 검증 및 조건부 Shadow 등록',
    'RUNNING',
    'research-lifecycle-worker',
  ],
  [
    'shadow',
    'Shadow Selection Evaluation',
    'Shadow 선택 비교 · 실거래 없음',
    'RUNNING',
    'recommendation-outcome-worker',
  ],
  [
    'reconciliation',
    'Live Order Reconciliation',
    '기존 주문의 상태를 GET으로 확인',
    'ERROR',
    'live-order-reconciliation-worker',
  ],
  [
    'portfolio',
    'Portfolio Performance',
    '완료 외부 입출금을 분리한 계좌 성과',
    'RUNNING',
    'portfolio-performance-worker',
  ],
  [
    'alerts',
    'Operational Alert',
    '중복 방지 outbox 기반 운영 경고',
    'RUNNING',
    'operational-alert-worker',
  ],
  [
    'account',
    'Account Activity Sync',
    '종료 주문·입출금 내역 read-only 동기화',
    'DISABLED',
    'account-activity-sync-worker',
  ],
  ['pnl', 'Bot Trading PnL', 'bot LIVE fill FIFO 원가 매칭', 'RUNNING', 'bot-trading-pnl-worker'],
  [
    'mock-retry',
    'Mock Order Retry',
    'MOCK 주문 실행 및 알림 재시도',
    'UNKNOWN',
    'mock-order-retry-worker',
  ],
];

const reconciliationErrorAt = '2026-11-07T02:58:00Z';
const portfolioCalculationCompletedAt = '2026-11-03T00:00:00Z';
// Process running does not prove a successful cycle. Only display timestamps for
// successful results present in this fixture; otherwise leave telemetry unavailable.
const workerLastSuccess: Record<string, string | null> = {
  scheduler: analyses[0]!.finishedAt,
  shadow:
    research.candidates
      .flatMap((candidate) =>
        candidate.shadowEvaluation?.status === 'SUCCESS'
          ? [candidate.shadowEvaluation.evaluatedAt]
          : [],
      )
      .sort()
      .at(-1) ?? null,
  portfolio: portfolioCalculationCompletedAt,
  pnl: DEMO_AS_OF,
};
const workerLastError: Record<string, string | null> = {
  reconciliation: reconciliationErrorAt,
  lifecycle: research.lifecycle.errorCount > 0 ? research.lifecycle.lastRunAt : null,
};

export const dashboardData: DashboardData = {
  asOf: DEMO_AS_OF,
  summary: {
    totalValueKrw: String(totalValue),
    accountReturnPercent: (totalValue / initialNav - 1) * 100,
    botRealizedPnlKrw: '57670',
    heldAssetCount: positionInputs.length,
    lastAnalysisAt: analyses[0]!.finishedAt,
    activePolicy: research.runtimePolicy.mode,
    registeredCandidateCount: research.candidates.length,
    shadowEnrollmentCount: research.candidates.filter((candidate) => candidate.shadowEnrollment)
      .length,
    researchSnapshotCount: research.snapshots.length,
    latestResearchAt: research.snapshots.at(-1)!.capturedAt,
  },
  portfolio: {
    summary: {
      totalValueKrw: String(totalValue),
      knownValueKrw: String(totalValue),
      unrealizedPnlKrw: String(unrealized),
      positionsCostBasisKrw: String(costBasis),
      cashKrw: String(cash),
      accountReturnPercent: (totalValue / initialNav - 1) * 100,
      dailyChangeKrw: String(totalValue - previousValue),
      dailyChangePercent: (totalValue / previousValue - 1) * 100,
      heldAssetCount: positionInputs.length,
      valuationStatus: 'COMPLETE',
      asOf: DEMO_AS_OF,
      performanceMethod: 'MODIFIED_DIETZ',
    },
    positions,
    performance,
    orders,
    botPnl: {
      realizedPnlKrw: '57670',
      feesKrw: '3269.3',
      fillCount: 6,
      fullyMatchedCount: 1,
      partiallyMatchedCount: 1,
      unmatchedCount: 1,
      accountingStatus: 'PARTIAL',
      matches,
      asOf: DEMO_AS_OF,
    },
  },
  analyses,
  research,
  operations: {
    workers: workerDefinitions.map(([id, name, description, status, container]): WorkerStatus => ({
      id,
      name,
      description,
      status,
      container,
      lastSuccessAt: workerLastSuccess[id] ?? null,
      lastErrorAt: workerLastError[id] ?? null,
      configuration:
        id === 'lifecycle'
          ? {
              interval: '21,600초',
              batchSize: '10',
              gateEnabled: 'true',
              autoEnrollmentEnabled: 'true',
            }
          : {
              enabled: status === 'DISABLED' ? 'false' : 'true',
              mode: id === 'reconciliation' ? 'GET only' : 'Demo 설정',
            },
    })),
    schedule: {
      lastRunId: analyses[0]!.id,
      status: 'SUCCESS',
      startedAt: analyses[0]!.startedAt,
      finishedAt: analyses[0]!.finishedAt,
      durationSeconds: 120,
      nextRunAt: '2026-11-07T09:00:00Z',
      error: null,
    },
    events: [
      {
        id: 'EV-10',
        occurredAt: '2026-11-07T03:02:00Z',
        type: 'ANALYSIS',
        level: 'INFO',
        service: 'AI Scheduler',
        title: 'AI 분석 완료',
        description: 'RUN-1280 · KRW-BTC HOLD · 주문 생성 없음',
      },
      {
        id: 'EV-09',
        occurredAt: '2026-11-07T03:01:45Z',
        type: 'RECOMMENDATION',
        level: 'INFO',
        service: 'AI Analysis',
        title: 'HOLD 추천 생성',
        description: '단기 모멘텀 중립 · 승인 요청 없이 종료',
      },
      {
        id: 'EV-08',
        occurredAt: '2026-11-07T03:00:00Z',
        type: 'ANALYSIS',
        level: 'INFO',
        service: 'AI Scheduler',
        title: '정기 분석 시작',
        description: 'DYNAMIC Universe · 15m / 1h / 4h / 1d',
      },
      {
        id: 'EV-07',
        occurredAt: reconciliationErrorAt,
        type: 'SYSTEM',
        level: 'ERROR',
        service: 'Live Order Reconciliation',
        title: '주문 조회 지연',
        description: '모의 timeout · 새 주문 없이 다음 조회 대기',
      },
      {
        id: 'EV-06',
        occurredAt: '2026-11-07T00:15:00Z',
        type: 'SHADOW',
        level: 'INFO',
        service: 'Shadow Selection',
        title: 'Shadow 선택 평가 완료',
        description: 'RPC-103 · 누적 12개 표본 · 실거래 없음',
      },
      {
        id: 'EV-05',
        occurredAt: '2026-11-07T00:00:00Z',
        type: 'RESEARCH',
        level: 'WARNING',
        service: 'Research Lifecycle',
        title: '연구 후보 4개 검증',
        description:
          'NOT_ELIGIBLE 1 · ALREADY_ENROLLED 1 · INSUFFICIENT_DATA 1 · INVALID_PROMOTION_DATA 1',
      },
      {
        id: 'EV-04',
        occurredAt: '2026-11-06T03:03:00Z',
        type: 'ORDER',
        level: 'WARNING',
        service: 'Execution Ledger',
        title: '주문 상태 확인 대기',
        description: 'ORDER-279 · LIVE_UNKNOWN · 재주문 없음',
      },
      {
        id: 'EV-03',
        occurredAt: '2026-11-04T03:04:00Z',
        type: 'ORDER',
        level: 'INFO',
        service: 'Execution Ledger',
        title: '부분 체결 및 잔량 취소 확인',
        description: 'ORDER-277 · 실제 체결량 0.8 SOL은 확정',
      },
      {
        id: 'EV-02',
        occurredAt: '2026-11-04T00:01:00Z',
        type: 'SHADOW',
        level: 'INFO',
        service: 'Research Lifecycle',
        title: '조건부 Shadow 등록',
        description: 'RPC-103 · SE-12 · Gate 적격 상태에서 등록',
      },
      {
        id: 'EV-01',
        occurredAt: portfolioCalculationCompletedAt,
        type: 'SYSTEM',
        level: 'INFO',
        service: 'Portfolio Performance',
        title: '계좌 성과 계산 완료',
        description: '외부 현금흐름 조정 · 봇 FIFO 손익과 별도 계산',
      },
    ],
    alerts: [
      {
        id: 'AL-3',
        occurredAt: reconciliationErrorAt,
        severity: 'ERROR',
        service: 'Live Order Reconciliation',
        title: '주문 조회 응답 지연',
        description:
          'Demo timeout 시나리오입니다. 주문 상태는 UNKNOWN으로 유지되며 새 주문을 만들지 않습니다.',
        status: 'OPEN',
      },
      {
        id: 'AL-2',
        occurredAt: '2026-11-07T00:00:00Z',
        severity: 'WARNING',
        service: 'Research Lifecycle',
        title: '후보 provenance 불일치',
        description: 'RPC-105의 evidence signature가 일치하지 않아 fail-closed 처리했습니다.',
        status: 'OPEN',
      },
      {
        id: 'AL-1',
        occurredAt: '2026-11-06T23:00:00Z',
        severity: 'INFO',
        service: 'Portfolio Performance',
        title: '성과 데이터 갱신',
        description: '모의 성과 계산 완료 · 외부 API와 연결되지 않았습니다.',
        status: 'RESOLVED',
      },
    ],
  },
};
