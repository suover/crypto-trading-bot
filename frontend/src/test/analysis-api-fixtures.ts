export const analysisBigId = '9007199254741093';
export const analysisAt = '2026-01-01T00:00:00Z';
export const analysisScope = { userId: '20', exchange: 'UPBIT', quoteAsset: 'KRW' };
export const analysisRecommendation = {
  id: analysisBigId,
  analysisRunId: '6001',
  market: 'KRW-BTC',
  action: 'BUY',
  tradeRatio: '0.123456789',
  confidence: '0.9876',
  aiModel: 'fixture-model',
  status: 'APPROVED',
  createdAt: analysisAt,
  updatedAt: analysisAt,
};
export const analysisUniverse = {
  status: 'VERIFIED',
  analysisRunId: '7001',
  selectedCandidateId: '8001',
};
export const analysisCandidate = {
  id: '8001',
  universeAnalysisRunId: '7001',
  market: 'KRW-BTC',
  baseAsset: 'BTC',
  quoteAsset: 'KRW',
  rank: 1,
  score: '0.123456789',
  selectionSource: 'RANKED',
  buyEligible: true,
  sellEligible: true,
  quoteTradeValue24h: '1234567890123456789012345678.12',
  createdAt: analysisAt,
  features: {
    projectionStatus: 'COMPLETE',
    dataQuality: 'PARTIAL',
    timeframes: [
      {
        timeframe: '15m',
        dataQuality: 'SUFFICIENT',
        candleCount: 50,
        trendLabel: '상승 우위',
        indicators: {
          latest_price: '12345678901234567890.1234567890',
          rsi_14: '71.1234567890123456789012345678',
          volume_ratio: '0',
        },
        invalidFields: [],
      },
      {
        timeframe: '1d',
        dataQuality: 'INSUFFICIENT',
        candleCount: 3,
        trendLabel: '판단 보류',
        indicators: {},
        invalidFields: [],
      },
    ],
  },
};
export const analysisDetail = {
  scope: analysisScope,
  generatedAt: analysisAt,
  recommendation: analysisRecommendation,
  analysis: {
    id: '6001',
    pipelineRunId: 'pipeline-5001',
    runType: 'AI_TRADE_RECOMMENDATION',
    tradingMode: 'AI_APPROVAL',
    status: 'SUCCESS',
    startedAt: analysisAt,
    finishedAt: analysisAt,
  },
  finalDecisionReason: 'Application safety checked reason',
  recommendedAmountKrw: '10000.12',
  recommendedQuantity: '0.0000000001',
  universe: analysisUniverse,
  selectedCandidate: analysisCandidate,
  approvals: [
    {
      id: '9501',
      status: 'APPROVED',
      expiresAt: analysisAt,
      approvedAt: analysisAt,
      rejectedAt: null,
      createdAt: analysisAt,
      updatedAt: analysisAt,
    },
  ],
  executionAttempts: [
    {
      id: '9601',
      approvalRequestId: '9501',
      orderLogId: '9701',
      tradingMode: 'LIVE',
      attemptNumber: 1,
      status: 'SUCCESS',
      errorCode: null,
      attemptedAt: analysisAt,
      nextRetryAt: null,
    },
  ],
  order: {
    id: '9701',
    approvalRequestId: '9501',
    tradingMode: 'LIVE',
    market: 'KRW-BTC',
    side: 'BUY',
    orderType: 'MARKET',
    status: 'LIVE_EXECUTED_CANCELLED',
    amountKrw: '10000.12',
    quantity: '2.0000000000',
    price: '5000.0000000000',
    executedQuantity: '1.0000000000',
    executedFundsKrw: '1500.0000000000',
    averageExecutionPrice: '1500.0000000000',
    paidFee: '0',
    remainingQuantity: '1',
    tradesCount: 1,
    executionSyncedAt: analysisAt,
    createdAt: analysisAt,
    updatedAt: analysisAt,
  },
};
export const analysisHeld = {
  ...analysisCandidate,
  id: '8002',
  market: 'KRW-ETH',
  baseAsset: 'ETH',
  rank: null,
  selectionSource: 'HELD',
  buyEligible: false,
};
export function analysisPage<T>(items: T[], page = 0, size = 20) {
  return {
    scope: analysisScope,
    items: items.slice(page * size, (page + 1) * size),
    page,
    size,
    totalElements: items.length,
    totalPages: Math.ceil(items.length / size),
    generatedAt: analysisAt,
  };
}
export const analysisRows = [
  analysisRecommendation,
  ...Array.from({ length: 20 }, (_, index) => ({
    ...analysisRecommendation,
    id: String(21 - index),
    action: 'HOLD',
    status: 'HOLD',
    tradeRatio: null,
  })),
];
export function analysisFixtureResponse(url: URL): string {
  const p = Number(url.searchParams.get('page') ?? 0),
    size = Number(url.searchParams.get('size') ?? 20);
  if (url.pathname.endsWith('/candidates'))
    return JSON.stringify({
      ...analysisPage([analysisCandidate, analysisHeld], p, size),
      universe: analysisUniverse,
    });
  if (url.pathname.endsWith('/recommendations')) {
    const action = url.searchParams.get('action'),
      market = url.searchParams.get('market');
    return JSON.stringify(
      analysisPage(
        analysisRows.filter(
          (r) => (!action || r.action === action) && (!market || r.market === market),
        ),
        p,
        size,
      ),
    );
  }
  const id = url.pathname.split('/').at(-1)!;
  if (id === analysisBigId) return JSON.stringify(analysisDetail);
  return JSON.stringify({
    ...analysisDetail,
    recommendation: {
      ...analysisRecommendation,
      id,
      action: 'HOLD',
      status: 'HOLD',
      tradeRatio: null,
    },
    universe: { status: 'NO_REFERENCE', analysisRunId: null, selectedCandidateId: null },
    selectedCandidate: null,
    approvals: [],
    executionAttempts: [],
    order: null,
  });
}
