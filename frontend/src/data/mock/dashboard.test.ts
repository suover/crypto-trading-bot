import { describe, expect, it } from 'vitest';
import { dashboardData, DEMO_AS_OF } from './dashboard';
import { partialDashboard } from '@/services/mock-dashboard-repository';

describe('coherent read-only demo dataset', () => {
  it('derives NAV, PnL and allocation from the same positions including KRW', () => {
    const { positions, summary } = dashboardData.portfolio;
    expect(positions.reduce((sum, row) => sum + Number(row.valueKrw), 0)).toBe(
      Number(summary.totalValueKrw),
    );
    expect(positions.reduce((sum, row) => sum + Number(row.unrealizedPnlKrw ?? 0), 0)).toBe(
      Number(summary.unrealizedPnlKrw),
    );
    expect(positions.reduce((sum, row) => sum + (row.weightPercent ?? 0), 0)).toBeCloseTo(100, 10);
    expect(dashboardData.summary.totalValueKrw).toBe(summary.totalValueKrw);
    expect(dashboardData.summary.heldAssetCount).toBe(
      positions.filter((row) => row.asset !== 'KRW').length,
    );
    for (const row of positions.filter((position) => position.asset !== 'KRW')) {
      expect(Number(row.quantity) * Number(row.currentPriceKrw)).toBeCloseTo(
        Number(row.valueKrw),
        6,
      );
      expect(Number(row.valueKrw) - Number(row.costBasisKrw)).toBe(Number(row.unrealizedPnlKrw));
      expect(row.returnPercent).toBeCloseTo(
        (Number(row.unrealizedPnlKrw) / Number(row.costBasisKrw)) * 100,
        8,
      );
    }
  });

  it('keeps final NAV, display index, account return and daily change consistent', () => {
    const { performance, summary } = dashboardData.portfolio;
    const first = performance[0]!;
    const last = performance.at(-1)!;
    const previous = performance.at(-2)!;
    expect(last.valueKrw).toBe(summary.totalValueKrw);
    expect(last.performanceIndex! - 100).toBeCloseTo(summary.accountReturnPercent!, 8);
    expect((Number(last.valueKrw) / Number(first.valueKrw)) * 100).toBeCloseTo(
      last.performanceIndex!,
      8,
    );
    expect(Number(last.valueKrw) - Number(previous.valueKrw)).toBe(Number(summary.dailyChangeKrw));
    expect(
      performance.every(
        (point, index) => index === 0 || point.capturedAt > performance[index - 1]!.capturedAt,
      ),
    ).toBe(true);
  });

  it('does not turn unavailable valuation into zero or known-subtotal into complete NAV', () => {
    const data = partialDashboard();
    const unpriced = data.portfolio.positions.find(
      (position) => position.valuationStatus === 'UNPRICED',
    )!;
    expect(unpriced.valueKrw).toBeNull();
    expect(data.portfolio.summary.valuationStatus).toBe('PARTIAL');
    expect(data.portfolio.summary.totalValueKrw).toBeNull();
    expect(data.portfolio.summary.positionsCostBasisKrw).toBeNull();
    expect(data.portfolio.summary.knownValueKrw).toBe(
      dashboardData.portfolio.summary.totalValueKrw,
    );
    expect(data.portfolio.positions.every((position) => position.weightPercent === null)).toBe(
      true,
    );
    expect(data.portfolio.performance.at(-1)?.performanceIndex).toBeNull();
    expect(data.summary.totalValueKrw).toBeNull();
    expect(dashboardData.summary.totalValueKrw).not.toBeNull();
  });

  it('connects orders and fills only to approved BUY/SELL recommendations', () => {
    for (const order of dashboardData.portfolio.orders) {
      const recommendation = dashboardData.analyses.find(
        (analysis) => analysis.recommendation.id === order.recommendationId,
      )!.recommendation;
      expect(recommendation.orderId).toBe(order.id);
      expect(recommendation.approvalStatus).toBe('APPROVED');
      expect(recommendation.action).toBe(order.side);
      expect(recommendation.market).toBe(order.market);
      expect(recommendation.executionStatus).toBe(order.status);
      if (order.fills.length) {
        expect(order.fills.reduce((sum, fill) => sum + Number(fill.fundsKrw), 0)).toBe(
          Number(order.executedFundsKrw),
        );
        expect(order.fills.reduce((sum, fill) => sum + Number(fill.quantity), 0)).toBeCloseTo(
          Number(order.executedQuantity),
          8,
        );
        for (const fill of order.fills) {
          expect(fill.orderId).toBe(order.id);
          expect(Number(fill.quantity) * Number(fill.priceKrw)).toBeCloseTo(
            Number(fill.fundsKrw),
            6,
          );
        }
      } else {
        expect(order.status).toBe('LIVE_UNKNOWN');
        expect(order.executedFundsKrw).toBeNull();
      }
    }
  });

  it('represents HOLD as no approval required and no execution', () => {
    const holds = dashboardData.analyses.filter(
      (analysis) => analysis.recommendation.action === 'HOLD',
    );
    expect(holds.length).toBeGreaterThan(0);
    for (const { recommendation } of holds) {
      expect(recommendation.tradeRatio).toBe(0);
      expect(recommendation.approvalStatus).toBe('NOT_REQUIRED');
      expect(recommendation.executionStatus).toBe('NOT_EXECUTED');
      expect(recommendation.orderId).toBeNull();
    }
  });

  it('does not include unmatched sell proceeds in recognized FIFO PnL', () => {
    const { botPnl, orders } = dashboardData.portfolio;
    expect(botPnl.accountingStatus).toBe('PARTIAL');
    expect(botPnl.matches.find((match) => match.status === 'UNMATCHED')?.realizedPnlKrw).toBeNull();
    expect(botPnl.matches.reduce((sum, match) => sum + Number(match.realizedPnlKrw ?? 0), 0)).toBe(
      Number(botPnl.realizedPnlKrw),
    );
    expect(orders.flatMap((order) => order.fills)).toHaveLength(botPnl.fillCount);
    expect(orders.reduce((sum, order) => sum + Number(order.feeKrw ?? 0), 0)).toBeCloseTo(
      Number(botPnl.feesKrw),
      8,
    );
    for (const match of botPnl.matches.filter((entry) => entry.realizedPnlKrw !== null)) {
      expect(Number(match.recognizedProceedsKrw) - Number(match.costBasisKrw)).toBe(
        Number(match.realizedPnlKrw),
      );
      expect(orders.some((order) => order.id === match.orderId && order.side === 'SELL')).toBe(
        true,
      );
    }
  });

  it('includes every Gate status without treating any candidate as an active runtime policy', () => {
    const { candidates, runtimePolicy } = dashboardData.research;
    expect(new Set(candidates.map((candidate) => candidate.gate.status))).toEqual(
      new Set([
        'NOT_ELIGIBLE',
        'ELIGIBLE_FOR_REVIEW',
        'INSUFFICIENT_DATA',
        'INVALID_PROMOTION_DATA',
      ]),
    );
    expect(runtimePolicy.mode).toBe('BASELINE');
    expect(runtimePolicy.candidateId).toBeNull();
    for (const candidate of candidates) {
      expect(Object.values(candidate.weights).reduce((sum, weight) => sum + weight, 0)).toBeCloseTo(
        1,
        10,
      );
      expect(candidate.activationStatus).toBe('INACTIVE');
      expect(candidate.humanApproval).toBe('NOT_APPROVED');
      expect(candidate.lifecycle).toHaveLength(11);
      expect(candidate.forward.from >= candidate.registeredAt).toBe(true);
      if (candidate.shadowEnrollment)
        expect(candidate.shadowEnrollment.candidateId).toBe(candidate.id);
    }
    expect(dashboardData.summary.shadowEnrollmentCount).toBe(
      candidates.filter((candidate) => candidate.shadowEnrollment).length,
    );
  });

  it('keeps records in or before the fixed demo as-of time', () => {
    const timestamps = [
      ...dashboardData.analyses.flatMap((run) => [run.startedAt, run.finishedAt]),
      ...dashboardData.portfolio.orders.flatMap((order) => [
        order.createdAt,
        ...order.fills.map((fill) => fill.filledAt),
      ]),
      ...dashboardData.operations.events.map((event) => event.occurredAt),
      ...dashboardData.research.snapshots.map((snapshot) => snapshot.capturedAt),
    ];
    expect(timestamps.every((time) => Date.parse(time) <= Date.parse(DEMO_AS_OF))).toBe(true);
    expect(Date.parse(dashboardData.operations.schedule.nextRunAt)).toBeGreaterThan(
      Date.parse(DEMO_AS_OF),
    );
  });

  it('keeps research sample counts within source chronology and references before registration', () => {
    const { candidates, snapshots } = dashboardData.research;
    for (const candidate of candidates) {
      const reference = snapshots.find(
        (snapshot) => snapshot.id === candidate.referenceSnapshotId,
      )!;
      expect(reference).toBeDefined();
      expect(Date.parse(reference.capturedAt)).toBeLessThan(Date.parse(candidate.registeredAt));
      expect(Date.parse(candidate.historical.to)).toBeLessThan(Date.parse(candidate.registeredAt));
      for (const evidence of [candidate.historical, candidate.forward]) {
        const available = snapshots.filter(
          (snapshot) => snapshot.capturedAt >= evidence.from && snapshot.capturedAt <= evidence.to,
        ).length;
        expect(evidence.sampleCount).toBeLessThanOrEqual(available);
      }
      if (candidate.shadowEnrollment && candidate.shadowEvaluation) {
        const enrollment = candidate.shadowEnrollment;
        const watermark = snapshots.find(
          (snapshot) => snapshot.id === `RS-${enrollment.snapshotWatermark}`,
        )!;
        expect(Date.parse(watermark.capturedAt)).toBeLessThan(Date.parse(enrollment.enrolledAt));
        expect(Date.parse(enrollment.enrolledAt)).toBeGreaterThan(
          Date.parse(candidate.registeredAt),
        );
        const available = snapshots.filter(
          (snapshot) =>
            snapshot.capturedAt > enrollment.enrolledAt &&
            snapshot.capturedAt <= candidate.shadowEvaluation!.evaluatedAt,
        ).length;
        expect(candidate.shadowEvaluation.sampleCount).toBeLessThanOrEqual(available);
      }
    }
  });

  it('shows evaluated Gate stages even when insufficient or invalid and leaves activation unstarted', () => {
    const expected = {
      NOT_ELIGIBLE: 'NOT_ELIGIBLE',
      ELIGIBLE_FOR_REVIEW: 'ELIGIBLE_FOR_REVIEW',
      INSUFFICIENT_DATA: 'INSUFFICIENT_DATA',
      INVALID_PROMOTION_DATA: 'INVALID',
    };
    for (const candidate of dashboardData.research.candidates) {
      expect(candidate.lifecycle.find((stage) => stage.id === 'gate')?.status).toBe(
        expected[candidate.gate.status],
      );
      expect(candidate.lifecycle.find((stage) => stage.id === 'activation')?.status).toBe(
        'NOT_STARTED',
      );
      for (const check of candidate.gate.checks.filter(
        (entry) => entry.status === 'PASS' || entry.status === 'FAIL',
      )) {
        expect(check.observed).not.toBeNull();
        const passed =
          check.operator === '>='
            ? check.observed! >= check.threshold
            : check.operator === '<='
              ? check.observed! <= check.threshold
              : check.observed! > check.threshold;
        expect(passed).toBe(check.status === 'PASS');
      }
      for (const horizon of candidate.forwardHorizons) {
        if (horizon.meanDeltaPercent !== null)
          expect(
            horizon.candidateMeanReturnPercent! - horizon.baselineMeanReturnPercent!,
          ).toBeCloseTo(horizon.meanDeltaPercent, 10);
      }
    }
  });

  it('aligns normalized timeframe trend labels with the displayed SMA features', () => {
    for (const analysis of dashboardData.analyses) {
      for (const candidate of analysis.candidates) {
        expect(candidate.timeframes.map((timeframe) => timeframe.timeframe)).toEqual([
          '15m',
          '60m',
          '240m',
          '1d',
        ]);
        for (const timeframe of candidate.timeframes) {
          if (timeframe.quality !== 'SUFFICIENT') {
            expect(timeframe.rsi14).toBeNull();
            expect(timeframe.sma20).toBeNull();
          } else if (timeframe.trend === 'UPTREND') {
            expect(Number(candidate.currentPriceKrw)).toBeGreaterThan(Number(timeframe.sma5));
            expect(Number(timeframe.sma5)).toBeGreaterThan(Number(timeframe.sma20));
          } else if (timeframe.trend === 'NEUTRAL') {
            expect(Number(timeframe.sma5)).toBeGreaterThan(Number(candidate.currentPriceKrw));
            expect(Number(candidate.currentPriceKrw)).toBeGreaterThan(Number(timeframe.sma20));
          }
        }
      }
    }
  });

  it('derives worker observations from successful records and preserves missing telemetry', () => {
    const { workers, events, schedule } = dashboardData.operations;
    const worker = (id: string) => workers.find((item) => item.id === id)!;
    expect(worker('scheduler').lastSuccessAt).toBe(schedule.finishedAt);
    expect(worker('scheduler').lastSuccessAt).toBe(dashboardData.analyses[0]!.finishedAt);
    const lastShadow = dashboardData.research.candidates.find(
      (candidate) => candidate.shadowEvaluation,
    )?.shadowEvaluation;
    expect(worker('shadow').lastSuccessAt).toBe(lastShadow?.evaluatedAt);
    expect(worker('lifecycle').lastSuccessAt).toBeNull();
    expect(worker('lifecycle').lastErrorAt).toBe(dashboardData.research.lifecycle.lastRunAt);
    expect(worker('reconciliation').lastErrorAt).toBe(
      events.find(
        (event) => event.service === 'Live Order Reconciliation' && event.level === 'ERROR',
      )?.occurredAt,
    );
    expect(worker('portfolio').lastSuccessAt).toBe(
      events.find((event) => event.service === 'Portfolio Performance')?.occurredAt,
    );
    expect(worker('pnl').lastSuccessAt).toBe(dashboardData.portfolio.botPnl.asOf);
    expect(worker('outcome').lastSuccessAt).toBeNull();
    expect(worker('account').lastSuccessAt).toBeNull();
    for (const item of workers) {
      for (const timestamp of [item.lastSuccessAt, item.lastErrorAt]) {
        if (timestamp !== null)
          expect(Date.parse(timestamp)).toBeLessThanOrEqual(Date.parse(DEMO_AS_OF));
      }
    }
  });
});
