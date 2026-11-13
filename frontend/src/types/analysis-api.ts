import type { Scope, Page, Envelope, Action } from './overview-api';
export type { Scope, Page, Action } from './overview-api';
export interface Recommendation {
  id: string;
  analysisRunId: string;
  market: string;
  action: Action;
  tradeRatio: string | null;
  confidence: string | null;
  aiModel: string | null;
  status: string;
  createdAt: string;
  updatedAt: string;
}
export interface Analysis {
  id: string;
  pipelineRunId: string | null;
  runType: string;
  tradingMode: string;
  status: string;
  startedAt: string;
  finishedAt: string | null;
}
export interface Universe {
  status: 'VERIFIED' | 'NO_REFERENCE' | 'INVALID_REFERENCE';
  analysisRunId: string | null;
  selectedCandidateId: string | null;
}
export const metrics = [
  'latest_price',
  'sma_5',
  'sma_20',
  'ema_5',
  'ema_20',
  'rsi_14',
  'recent_change_rate',
  'volume_ratio',
  'macd',
  'macd_signal',
  'macd_histogram',
  'atr_14',
  'atr_percentage',
  'realized_volatility',
  'max_drawdown',
] as const;
export type Metric = (typeof metrics)[number];
export interface Timeframe {
  timeframe: '15m' | '1h' | '4h' | '1d';
  dataQuality: string | null;
  candleCount: number | null;
  trendLabel: string | null;
  indicators: Partial<Record<Metric, string | null>>;
  invalidFields: string[];
}
export interface Candidate {
  id: string;
  universeAnalysisRunId: string;
  market: string;
  baseAsset: string;
  quoteAsset: string;
  rank: number | null;
  score: string | null;
  selectionSource: string;
  buyEligible: boolean | null;
  sellEligible: boolean | null;
  quoteTradeValue24h: string | null;
  createdAt: string;
  features: {
    projectionStatus: 'COMPLETE' | 'PARTIAL' | 'UNAVAILABLE';
    dataQuality: string | null;
    timeframes: Timeframe[];
  };
}
export interface Approval {
  id: string;
  status: string;
  expiresAt: string;
  approvedAt: string | null;
  rejectedAt: string | null;
  createdAt: string;
  updatedAt: string;
}
export interface Attempt {
  id: string;
  approvalRequestId: string;
  orderLogId: string | null;
  tradingMode: string;
  attemptNumber: number;
  status: string;
  errorCode: string | null;
  attemptedAt: string;
  nextRetryAt: string | null;
}
export interface Order {
  id: string;
  approvalRequestId: string | null;
  tradingMode: string;
  market: string;
  side: string;
  orderType: string;
  status: string;
  amountKrw: string | null;
  quantity: string | null;
  price: string | null;
  executedQuantity: string | null;
  executedFundsKrw: string | null;
  averageExecutionPrice: string | null;
  paidFee: string | null;
  remainingQuantity: string | null;
  tradesCount: number | null;
  executionSyncedAt: string | null;
  createdAt: string;
  updatedAt: string;
}
export interface Detail extends Envelope {
  recommendation: Recommendation;
  analysis: Analysis;
  finalDecisionReason: string;
  recommendedAmountKrw: string | null;
  recommendedQuantity: string | null;
  universe: Universe;
  selectedCandidate: Candidate | null;
  approvals: Approval[];
  executionAttempts: Attempt[];
  order: Order | null;
}
export interface Candidates extends Page<Candidate> {
  universe: Universe;
}
export interface Search {
  from: string;
  to: string;
  market: string;
  action: '' | Action;
  page: number;
  size: number;
}
export interface DetailReference {
  id: string;
  scope?: Scope;
}
