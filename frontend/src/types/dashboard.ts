/** Read models for a future REST adapter. Decimal values stay strings on the wire. */
export type DecimalString = string;
export type DemoScenario = 'normal' | 'empty' | 'error' | 'loading' | 'partial';
export type TradeAction = 'BUY' | 'SELL' | 'HOLD';
export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'EXPIRED' | 'NOT_REQUIRED';
export type ExecutionStatus =
  | 'LIVE_DONE'
  | 'LIVE_EXECUTED_CANCELLED'
  | 'LIVE_WAIT'
  | 'LIVE_UNKNOWN'
  | 'LIVE_CANCELLED'
  | 'NOT_EXECUTED';
export type DataQuality = 'SUFFICIENT' | 'INSUFFICIENT' | 'UNAVAILABLE';
export type Timeframe = '15m' | '60m' | '240m' | '1d';

export interface TimeframeFeatures {
  timeframe: Timeframe;
  trend: 'UPTREND' | 'DOWNTREND' | 'NEUTRAL' | 'UNAVAILABLE';
  quality: DataQuality;
  sma5: DecimalString | null;
  sma20: DecimalString | null;
  ema5: DecimalString | null;
  ema20: DecimalString | null;
  rsi14: number | null;
  macd: DecimalString | null;
  atr14: DecimalString | null;
  volatilityPercent: number | null;
  /** 5/20 volume multiple; engine percentage is divided by 100 in this read DTO. */
  volumeRatio: number | null;
}

export interface MarketCandidate {
  id: string;
  market: string;
  baseAsset: string;
  currentPriceKrw: DecimalString | null;
  quoteTradeValue24hKrw: DecimalString | null;
  rank: number | null;
  score: number | null;
  selectionSource: 'RANKED' | 'HELD';
  buyEligible: boolean;
  sellEligible: boolean;
  timeframes: TimeframeFeatures[];
}

export interface TradeRecommendation {
  id: string;
  exchange: 'UPBIT';
  market: string;
  action: TradeAction;
  confidence: number | null;
  tradeRatio: number | null;
  reason: string;
  model: string;
  approvalStatus: ApprovalStatus;
  status:
    | 'CREATED'
    | 'APPROVAL_PENDING'
    | 'APPROVED'
    | 'REJECTED'
    | 'APPROVAL_EXPIRED'
    | 'LIVE_EXECUTED'
    | 'LIVE_EXECUTION_UNKNOWN';
  executionStatus: ExecutionStatus;
  orderId: string | null;
}

export interface AnalysisRun {
  id: string;
  pipelineRunId: string;
  startedAt: string;
  finishedAt: string;
  status: 'SUCCESS' | 'FAILED';
  universeMode: 'STATIC' | 'DYNAMIC';
  recommendation: TradeRecommendation;
  candidates: MarketCandidate[];
}

export interface PortfolioPosition {
  id: string;
  market: string | null;
  asset: string;
  name: string;
  quantity: DecimalString;
  averageBuyPriceKrw: DecimalString | null;
  currentPriceKrw: DecimalString | null;
  valueKrw: DecimalString | null;
  costBasisKrw: DecimalString | null;
  unrealizedPnlKrw: DecimalString | null;
  returnPercent: number | null;
  weightPercent: number | null;
  valuationStatus: 'PRICED' | 'UNPRICED' | 'EXCLUDED';
  priceSource: 'MARKET_SNAPSHOT' | 'CASH' | 'UNAVAILABLE';
}

export interface PortfolioSummary {
  totalValueKrw: DecimalString | null;
  knownValueKrw: DecimalString | null;
  unrealizedPnlKrw: DecimalString | null;
  positionsCostBasisKrw: DecimalString | null;
  cashKrw: DecimalString | null;
  accountReturnPercent: number | null;
  dailyChangeKrw: DecimalString | null;
  dailyChangePercent: number | null;
  heldAssetCount: number;
  valuationStatus: 'COMPLETE' | 'PARTIAL';
  asOf: string;
  performanceMethod: 'MODIFIED_DIETZ';
}

export interface PortfolioPerformancePoint {
  capturedAt: string;
  valueKrw: DecimalString | null;
  performanceIndex: number | null;
  status: 'COMPLETE' | 'PARTIAL';
}

export interface OrderFill {
  id: string;
  orderId: string;
  filledAt: string;
  quantity: DecimalString;
  priceKrw: DecimalString;
  fundsKrw: DecimalString;
  feeKrw: DecimalString;
}

export interface OrderExecution {
  id: string;
  recommendationId: string;
  createdAt: string;
  market: string;
  side: 'BUY' | 'SELL';
  status: Exclude<ExecutionStatus, 'NOT_EXECUTED'>;
  executedQuantity: DecimalString | null;
  averagePriceKrw: DecimalString | null;
  executedFundsKrw: DecimalString | null;
  feeKrw: DecimalString | null;
  fills: OrderFill[];
}

export interface BotPnlMatch {
  id: string;
  orderId: string;
  market: string;
  soldAt: string;
  status: 'FULLY_MATCHED' | 'PARTIALLY_MATCHED' | 'UNMATCHED';
  soldQuantity: DecimalString;
  matchedQuantity: DecimalString;
  costBasisKrw: DecimalString | null;
  recognizedProceedsKrw: DecimalString | null;
  realizedPnlKrw: DecimalString | null;
}

export interface BotPnlSummary {
  realizedPnlKrw: DecimalString | null;
  feesKrw: DecimalString | null;
  fillCount: number;
  fullyMatchedCount: number;
  partiallyMatchedCount: number;
  unmatchedCount: number;
  accountingStatus: 'COMPLETE' | 'PARTIAL';
  matches: BotPnlMatch[];
  asOf: string;
}

export interface RankingWeights {
  liquidity: number;
  trendAlignment: number;
  momentum: number;
  volumeConfirmation: number;
  spread: number;
  volatility: number;
  drawdown: number;
}

export type GateStatus =
  'INVALID_PROMOTION_DATA' | 'INSUFFICIENT_DATA' | 'NOT_ELIGIBLE' | 'ELIGIBLE_FOR_REVIEW';

export interface PromotionGateCheck {
  id: string;
  label: string;
  window: string;
  observed: number | null;
  threshold: number;
  operator: '>=' | '<=' | '>';
  unit: '%' | 'samples';
  status: 'PASS' | 'FAIL' | 'INSUFFICIENT' | 'INVALID';
}

export interface PolicyPromotionGate {
  status: GateStatus;
  evaluatedAt: string;
  reason: string;
  checks: PromotionGateCheck[];
}

export interface ResearchEvidence {
  status: 'SUCCESS' | 'INSUFFICIENT_DATA' | 'INVALID_DATA';
  sampleCount: number;
  from: string;
  to: string;
  grossReturnPercent: number | null;
  costAdjustedReturnPercent: number | null;
  baselineReturnPercent: number | null;
  winRatePercent: number | null;
  turnoverPercent: number | null;
}

export interface ShadowEnrollment {
  id: string;
  candidateId: string;
  enrolledAt: string;
  status: 'ENROLLED';
  snapshotWatermark: number;
}

export interface ShadowEvaluation {
  status: 'SUCCESS' | 'INSUFFICIENT_DATA';
  evaluatedAt: string;
  sampleCount: number;
  grossReturnPercent: number | null;
  reviewStatus: 'INSUFFICIENT_DATA' | 'ELIGIBLE_FOR_PROMOTION_REVIEW' | 'NOT_ELIGIBLE';
}

export interface LifecycleStage {
  id: string;
  label: string;
  status:
    | 'COMPLETE'
    | 'IN_PROGRESS'
    | 'INSUFFICIENT_DATA'
    | 'NOT_ELIGIBLE'
    | 'ELIGIBLE_FOR_REVIEW'
    | 'NOT_STARTED'
    | 'INVALID';
  detail: string;
}

export interface ResearchPolicyCandidate {
  id: string;
  name: string;
  description: string;
  registeredAt: string;
  baseline: string;
  effectiveTopN: number;
  referenceSnapshotId: string;
  weights: RankingWeights;
  historical: ResearchEvidence;
  forward: ResearchEvidence;
  forwardHorizons: {
    horizonMinutes: 60 | 240 | 1440;
    baselineMeanReturnPercent: number | null;
    candidateMeanReturnPercent: number | null;
    meanDeltaPercent: number | null;
    costAdjustedDeltaPercent: number | null;
    winRatePercent: number | null;
    sampleCount: number;
    gateStatus: 'PASS' | 'FAIL' | 'INSUFFICIENT' | 'INVALID';
  }[];
  gate: PolicyPromotionGate;
  shadowEnrollment: ShadowEnrollment | null;
  shadowEvaluation: ShadowEvaluation | null;
  humanApproval: 'NOT_APPROVED' | 'APPROVED';
  activationStatus: 'INACTIVE' | 'FULL_LIVE';
  lifecycle: LifecycleStage[];
}

export interface ResearchSnapshot {
  id: string;
  capturedAt: string;
  candidateCount: number;
  finalCount: number;
  policyName: string;
}

export interface RuntimePolicy {
  mode: 'BASELINE' | 'FULL_LIVE';
  name: string;
  candidateId: string | null;
  since: string;
  weights: RankingWeights;
}

export interface ResearchLifecycleSummary {
  gateEnabled: boolean;
  autoEnrollmentEnabled: boolean;
  intervalSeconds: number;
  registeredCount: number;
  processedCount: number;
  rejectedCount: number;
  enrolledCount: number;
  errorCount: number;
  lastRunAt: string | null;
}

export interface WorkerStatus {
  id: string;
  name: string;
  description: string;
  status: 'RUNNING' | 'DISABLED' | 'ERROR' | 'UNKNOWN';
  lastSuccessAt: string | null;
  lastErrorAt: string | null;
  configuration: Record<string, string>;
  container: string;
}

export interface OperationalEvent {
  id: string;
  occurredAt: string;
  type: 'ANALYSIS' | 'RECOMMENDATION' | 'ORDER' | 'RESEARCH' | 'SHADOW' | 'SYSTEM';
  level: 'INFO' | 'WARNING' | 'ERROR';
  service: string;
  title: string;
  description: string;
}

export interface OperationalAlert {
  id: string;
  occurredAt: string;
  severity: 'INFO' | 'WARNING' | 'ERROR';
  service: string;
  title: string;
  description: string;
  status: 'OPEN' | 'RESOLVED';
}

export interface AnalysisSchedule {
  lastRunId: string;
  status: 'SUCCESS' | 'FAILED';
  startedAt: string;
  finishedAt: string;
  durationSeconds: number;
  nextRunAt: string;
  error: string | null;
}

export interface DashboardSummary {
  totalValueKrw: DecimalString | null;
  accountReturnPercent: number | null;
  botRealizedPnlKrw: DecimalString | null;
  heldAssetCount: number;
  lastAnalysisAt: string | null;
  activePolicy: string;
  registeredCandidateCount: number;
  shadowEnrollmentCount: number;
  researchSnapshotCount: number;
  latestResearchAt: string | null;
}

export interface DashboardData {
  asOf: string;
  summary: DashboardSummary;
  portfolio: {
    summary: PortfolioSummary;
    positions: PortfolioPosition[];
    performance: PortfolioPerformancePoint[];
    orders: OrderExecution[];
    botPnl: BotPnlSummary;
  };
  analyses: AnalysisRun[];
  research: {
    snapshots: ResearchSnapshot[];
    candidates: ResearchPolicyCandidate[];
    runtimePolicy: RuntimePolicy;
    baselineWeights: RankingWeights;
    lifecycle: ResearchLifecycleSummary;
  };
  operations: {
    workers: WorkerStatus[];
    events: OperationalEvent[];
    alerts: OperationalAlert[];
    schedule: AnalysisSchedule;
  };
}
