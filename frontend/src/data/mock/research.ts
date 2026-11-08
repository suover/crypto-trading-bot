import type {
  DashboardData,
  LifecycleStage,
  RankingWeights,
  ResearchPolicyCandidate,
} from '@/types/dashboard';

export const baselineWeights: RankingWeights = {
  liquidity: 0.35,
  trendAlignment: 0.2,
  momentum: 0.15,
  volumeConfirmation: 0.1,
  spread: 0.08,
  volatility: 0.07,
  drawdown: 0.05,
};

const stageLabels = [
  ['snapshot', 'Research Snapshot'],
  ['generation', 'Candidate Generation'],
  ['screening', 'Historical Screening'],
  ['registration', 'Candidate Registration'],
  ['forward', 'Forward Validation'],
  ['gate', 'Promotion Gate'],
  ['enrollment', 'Shadow Enrollment'],
  ['evaluation', 'Shadow Evaluation'],
  ['review', 'Shadow Review'],
  ['approval', 'Human Approval'],
  ['activation', 'Full LIVE Activation'],
] as const;

function stages(kind: 'failed' | 'shadow' | 'insufficient' | 'invalid'): LifecycleStage[] {
  return stageLabels.map(([id, label], index): LifecycleStage => {
    // These are explicit fictional source records, not inferences from registration.
    if (index < 4)
      return {
        id,
        label,
        status: 'COMPLETE',
        detail:
          index === 1 ? '운영자 CLI 생성 기록 · 자동 생성은 개발 예정' : 'Demo 원본 기록 확인',
      };
    if (kind === 'invalid' && index === 4)
      return { id, label, status: 'INVALID', detail: 'Fixture provenance 불일치' };
    if (kind === 'insufficient' && index === 4)
      return { id, label, status: 'INSUFFICIENT_DATA', detail: '등록 이후 관측 표본 부족' };
    if (index === 4 && (kind === 'failed' || kind === 'shadow'))
      return { id, label, status: 'COMPLETE', detail: '1h · 4h · 1d evidence 계산' };
    if (index === 5 && kind === 'failed')
      return { id, label, status: 'NOT_ELIGIBLE', detail: '비용 차감 승률 기준 미충족' };
    if (index === 5 && kind === 'shadow')
      return { id, label, status: 'ELIGIBLE_FOR_REVIEW', detail: 'Phase 1 자동 Gate 평가 통과' };
    if (index === 5 && kind === 'insufficient')
      return {
        id,
        label,
        status: 'INSUFFICIENT_DATA',
        detail: 'Gate 평가 완료 · 최소 표본 미충족',
      };
    if (index === 5 && kind === 'invalid')
      return { id, label, status: 'INVALID', detail: 'Gate 평가 완료 · provenance 검증 실패' };
    if (index === 6 && kind === 'shadow')
      return { id, label, status: 'COMPLETE', detail: '조건부 immutable Shadow 등록' };
    if (index === 7 && kind === 'shadow')
      return { id, label, status: 'IN_PROGRESS', detail: '등록 이후 실거래 없는 선택 비교' };
    if (index === 8 && kind === 'shadow')
      return { id, label, status: 'INSUFFICIENT_DATA', detail: 'Shadow 관측 구간 수집 중' };
    return {
      id,
      label,
      status: 'NOT_STARTED',
      detail:
        index === 9
          ? '별도 사람 승인 필요 · Telegram 전략 승인 개발 예정'
          : index === 10
            ? '명시적 활성화 필요 · 주문 생성과 별개'
            : '선행 단계 조건 필요',
    };
  });
}

const historical = {
  status: 'SUCCESS' as const,
  sampleCount: 126,
  from: '2026-09-29T00:00:00Z',
  to: '2026-10-20T00:00:00Z',
  grossReturnPercent: 0.84,
  costAdjustedReturnPercent: 0.65,
  baselineReturnPercent: 0.61,
  winRatePercent: 57.14,
  turnoverPercent: 28.57,
};

const forward = {
  status: 'SUCCESS' as const,
  sampleCount: 89,
  from: '2026-10-21T03:00:00Z',
  to: '2026-11-07T00:00:00Z',
  grossReturnPercent: 0.42,
  costAdjustedReturnPercent: 0.19,
  baselineReturnPercent: 0.48,
  winRatePercent: 35.96,
  turnoverPercent: 31.24,
};

const defaultChecks: ResearchPolicyCandidate['gate']['checks'] = [
  {
    id: 'FORWARD_GROSS_SAMPLE_COUNT',
    label: 'Forward 표본 수',
    window: '1시간',
    observed: 89,
    threshold: 21,
    operator: '>=',
    unit: 'samples',
    status: 'PASS',
  },
  {
    id: 'FORWARD_COST_WIN_RATE',
    label: '비용 차감 승률',
    window: '1시간',
    observed: 35.96,
    threshold: 55,
    operator: '>=',
    unit: '%',
    status: 'FAIL',
  },
  {
    id: 'FORWARD_COST_MEAN_DELTA',
    label: '평균 비용 차감 초과수익',
    window: '1시간',
    observed: -0.08,
    threshold: 0,
    operator: '>',
    unit: '%',
    status: 'FAIL',
  },
  {
    id: 'FORWARD_GROSS_MEAN_DELTA',
    label: '평균 Gross 초과수익',
    window: '4시간',
    observed: 0.12,
    threshold: 0,
    operator: '>=',
    unit: '%',
    status: 'PASS',
  },
];

const forwardHorizons: ResearchPolicyCandidate['forwardHorizons'] = [
  {
    horizonMinutes: 60,
    baselineMeanReturnPercent: 0.48,
    candidateMeanReturnPercent: 0.42,
    meanDeltaPercent: -0.06,
    costAdjustedDeltaPercent: -0.08,
    winRatePercent: 35.96,
    sampleCount: 89,
    gateStatus: 'FAIL',
  },
  {
    horizonMinutes: 240,
    baselineMeanReturnPercent: 0.61,
    candidateMeanReturnPercent: 0.73,
    meanDeltaPercent: 0.12,
    costAdjustedDeltaPercent: 0.06,
    winRatePercent: 58.43,
    sampleCount: 89,
    gateStatus: 'PASS',
  },
  {
    horizonMinutes: 1440,
    baselineMeanReturnPercent: 0.82,
    candidateMeanReturnPercent: 0.94,
    meanDeltaPercent: 0.12,
    costAdjustedDeltaPercent: 0.03,
    winRatePercent: 52.63,
    sampleCount: 76,
    gateStatus: 'FAIL',
  },
];

export const researchCandidates: ResearchPolicyCandidate[] = [
  {
    id: 'RPC-104',
    name: 'Momentum Balanced',
    description: '추세 정합성과 모멘텀에 조금 더 비중을 둔 제한 범위 후보',
    registeredAt: '2026-10-21T03:00:00Z',
    baseline: 'Heuristic Baseline v1',
    effectiveTopN: 7,
    referenceSnapshotId: 'RS-1082',
    weights: {
      liquidity: 0.28,
      trendAlignment: 0.24,
      momentum: 0.2,
      volumeConfirmation: 0.1,
      spread: 0.06,
      volatility: 0.07,
      drawdown: 0.05,
    },
    historical,
    forward,
    forwardHorizons,
    gate: {
      status: 'NOT_ELIGIBLE',
      evaluatedAt: '2026-11-07T00:00:00Z',
      reason: '1시간 비용 차감 승률과 평균 초과수익이 기준에 미달합니다.',
      checks: defaultChecks,
    },
    shadowEnrollment: null,
    shadowEvaluation: null,
    humanApproval: 'NOT_APPROVED',
    activationStatus: 'INACTIVE',
    lifecycle: stages('failed'),
  },
  {
    id: 'RPC-103',
    name: 'Liquidity Defensive',
    description: '유동성과 하방 위험 성분을 강화한 연구 후보',
    registeredAt: '2026-10-20T03:00:00Z',
    baseline: 'Heuristic Baseline v1',
    effectiveTopN: 7,
    referenceSnapshotId: 'RS-1078',
    weights: {
      liquidity: 0.4,
      trendAlignment: 0.18,
      momentum: 0.1,
      volumeConfirmation: 0.08,
      spread: 0.08,
      volatility: 0.09,
      drawdown: 0.07,
    },
    historical: {
      ...historical,
      grossReturnPercent: 0.91,
      costAdjustedReturnPercent: 0.76,
      winRatePercent: 61.9,
    },
    forward: {
      ...forward,
      grossReturnPercent: 0.62,
      costAdjustedReturnPercent: 0.49,
      winRatePercent: 62.92,
      turnoverPercent: 19.11,
    },
    forwardHorizons: forwardHorizons.map((entry) => ({
      ...entry,
      candidateMeanReturnPercent: Number((entry.baselineMeanReturnPercent! + 0.14).toFixed(2)),
      meanDeltaPercent: 0.14,
      costAdjustedDeltaPercent: 0.1,
      winRatePercent: 62.92,
      gateStatus: 'PASS',
    })),
    gate: {
      status: 'ELIGIBLE_FOR_REVIEW',
      evaluatedAt: '2026-11-07T00:00:00Z',
      reason: '고정 Gate 기준 충족. Shadow 관측과 사람 승인은 별도입니다.',
      checks: defaultChecks.map((check) => ({
        ...check,
        observed:
          check.id === 'FORWARD_COST_WIN_RATE'
            ? 62.92
            : check.id === 'FORWARD_COST_MEAN_DELTA'
              ? 0.1
              : check.id === 'FORWARD_GROSS_MEAN_DELTA'
                ? 0.14
                : check.observed,
        status: 'PASS',
      })),
    },
    shadowEnrollment: {
      id: 'SE-12',
      candidateId: 'RPC-103',
      enrolledAt: '2026-11-04T00:01:00Z',
      status: 'ENROLLED',
      snapshotWatermark: 1181,
    },
    shadowEvaluation: {
      status: 'SUCCESS',
      evaluatedAt: '2026-11-07T00:15:00Z',
      sampleCount: 12,
      grossReturnPercent: 0.27,
      reviewStatus: 'INSUFFICIENT_DATA',
    },
    humanApproval: 'NOT_APPROVED',
    activationStatus: 'INACTIVE',
    lifecycle: stages('shadow'),
  },
  {
    id: 'RPC-106',
    name: 'Trend Confirmation',
    description: '다중 시간봉 정합성 성분을 보강한 신규 연구 후보',
    registeredAt: '2026-11-05T03:00:00Z',
    baseline: 'Heuristic Baseline v1',
    effectiveTopN: 7,
    referenceSnapshotId: 'RS-1185',
    weights: {
      liquidity: 0.3,
      trendAlignment: 0.27,
      momentum: 0.13,
      volumeConfirmation: 0.1,
      spread: 0.08,
      volatility: 0.07,
      drawdown: 0.05,
    },
    historical,
    forward: {
      ...forward,
      status: 'INSUFFICIENT_DATA',
      from: '2026-11-05T03:00:00Z',
      sampleCount: 8,
      grossReturnPercent: null,
      costAdjustedReturnPercent: null,
      baselineReturnPercent: null,
      winRatePercent: null,
      turnoverPercent: null,
    },
    forwardHorizons: forwardHorizons.map((entry) => ({
      ...entry,
      sampleCount: 8,
      baselineMeanReturnPercent: null,
      candidateMeanReturnPercent: null,
      meanDeltaPercent: null,
      costAdjustedDeltaPercent: null,
      winRatePercent: null,
      gateStatus: 'INSUFFICIENT',
    })),
    gate: {
      status: 'INSUFFICIENT_DATA',
      evaluatedAt: '2026-11-07T00:00:00Z',
      reason: '등록 이후 최소 관측 기간과 표본 수를 아직 확보하지 못했습니다.',
      checks: [{ ...defaultChecks[0]!, observed: 8, status: 'INSUFFICIENT' }],
    },
    shadowEnrollment: null,
    shadowEvaluation: null,
    humanApproval: 'NOT_APPROVED',
    activationStatus: 'INACTIVE',
    lifecycle: stages('insufficient'),
  },
  {
    id: 'RPC-105',
    name: 'Volume Selective',
    description: '거래량 확인 성분 변경 후보 · 무효 provenance 예시',
    registeredAt: '2026-10-22T03:00:00Z',
    baseline: 'Heuristic Baseline v1',
    effectiveTopN: 7,
    referenceSnapshotId: 'RS-1086',
    weights: {
      liquidity: 0.3,
      trendAlignment: 0.2,
      momentum: 0.15,
      volumeConfirmation: 0.15,
      spread: 0.08,
      volatility: 0.07,
      drawdown: 0.05,
    },
    historical,
    forward: {
      ...forward,
      status: 'INVALID_DATA',
      from: '2026-10-22T03:00:00Z',
      sampleCount: 0,
      grossReturnPercent: null,
      costAdjustedReturnPercent: null,
      baselineReturnPercent: null,
      winRatePercent: null,
      turnoverPercent: null,
    },
    forwardHorizons: forwardHorizons.map((entry) => ({
      ...entry,
      sampleCount: 0,
      baselineMeanReturnPercent: null,
      candidateMeanReturnPercent: null,
      meanDeltaPercent: null,
      costAdjustedDeltaPercent: null,
      winRatePercent: null,
      gateStatus: 'INVALID',
    })),
    gate: {
      status: 'INVALID_PROMOTION_DATA',
      evaluatedAt: '2026-11-07T00:00:00Z',
      reason: '저장된 후보 정의와 evidence signature가 일치하지 않는 Mock 시나리오입니다.',
      checks: [],
    },
    shadowEnrollment: null,
    shadowEvaluation: null,
    humanApproval: 'NOT_APPROVED',
    activationStatus: 'INACTIVE',
    lifecycle: stages('invalid'),
  },
];

export const research: DashboardData['research'] = {
  // All snapshot rows are generated from a fixed chronology, never Math.random or Date.now.
  snapshots: Array.from({ length: 1200 }, (_, index) => ({
    id: `RS-${index + 1}`,
    capturedAt: new Date(
      Date.parse('2026-11-07T03:00:00Z') - (1199 - index) * 4 * 60 * 60 * 1000,
    ).toISOString(),
    candidateCount: 20,
    finalCount: 7,
    policyName: 'Heuristic Baseline v1',
  })),
  candidates: researchCandidates,
  runtimePolicy: {
    mode: 'BASELINE',
    name: 'Heuristic Baseline v1',
    candidateId: null,
    since: '2026-07-01T00:00:00Z',
    weights: baselineWeights,
  },
  baselineWeights,
  lifecycle: {
    gateEnabled: true,
    autoEnrollmentEnabled: true,
    intervalSeconds: 21600,
    registeredCount: 4,
    processedCount: 4,
    rejectedCount: 1,
    enrolledCount: 0,
    errorCount: 1,
    lastRunAt: '2026-11-07T00:00:00Z',
  },
};
