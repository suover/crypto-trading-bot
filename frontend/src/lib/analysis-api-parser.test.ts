import { describe, it, expect } from 'vitest';
import {
  parseAnalysisDetail,
  parseAnalysisRecommendations,
  parseAnalysisCandidates,
} from './analysis-api-parser';
import {
  analysisDetail,
  analysisCandidate,
  analysisHeld,
  analysisUniverse,
  analysisPage,
  analysisRecommendation,
  analysisBigId,
} from '@/test/analysis-api-fixtures';
describe('AI Analysis wire contract', () => {
  it('preserves exact IDs, indicator digits, null and zero; projects out private extras', () => {
    const d = parseAnalysisDetail(
      JSON.stringify({ ...analysisDetail, raw_response: 'private', callback_token: 'private' }),
    );
    expect(d.recommendation.id).toBe(analysisBigId);
    expect(d.recommendedQuantity).toBe('0.0000000001');
    expect(d.selectedCandidate?.features.timeframes[0]?.indicators.rsi_14).toBe(
      '71.1234567890123456789012345678',
    );
    expect(d.order?.paidFee).toBe('0');
    expect(d.approvals[0]?.rejectedAt).toBeNull();
    expect(JSON.stringify(d)).not.toContain('private');
  });
  it.each(['NO_REFERENCE', 'INVALID_REFERENCE'])(
    'preserves %s without fake candidates',
    (status) => {
      const universe = { status, analysisRunId: null, selectedCandidateId: null };
      expect(
        parseAnalysisDetail(
          JSON.stringify({ ...analysisDetail, universe, selectedCandidate: null }),
        ).universe.status,
      ).toBe(status);
      expect(
        parseAnalysisCandidates(JSON.stringify({ ...analysisPage([]), universe })).items,
      ).toEqual([]);
    },
  );
  it('preserves held and partial candidates and expanded decimal precision', () => {
    const p = parseAnalysisCandidates(
      JSON.stringify({
        ...analysisPage([analysisCandidate, analysisHeld]),
        universe: analysisUniverse,
      }),
    );
    expect(p.items[1]?.buyEligible).toBe(false);
    expect(p.items[1]?.rank).toBeNull();
    expect(p.items[0]?.features).toMatchObject({
      dataQuality: 'PARTIAL',
      projectionStatus: 'COMPLETE',
    });
  });
  it.each(['NaN', 'Infinity', '-Infinity', '1e100', '', '01'])(
    'rejects malformed decimal %s',
    (tradeRatio) => {
      expect(() =>
        parseAnalysisRecommendations(
          JSON.stringify(analysisPage([{ ...analysisRecommendation, tradeRatio }])),
        ),
      ).toThrow();
    },
  );
  it.each(['0', '-1', '01', '9223372036854775808', '1/approve'])('rejects ID %s', (id) => {
    expect(() =>
      parseAnalysisDetail(
        JSON.stringify({ ...analysisDetail, recommendation: { ...analysisRecommendation, id } }),
      ),
    ).toThrow();
  });
  it.each([
    { analysis: { ...analysisDetail.analysis, id: '6002' } },
    { selectedCandidate: { ...analysisCandidate, id: '8002' } },
    { selectedCandidate: { ...analysisCandidate, universeAnalysisRunId: '9999' } },
    { selectedCandidate: { ...analysisCandidate, market: 'KRW-ETH' } },
    { selectedCandidate: { ...analysisCandidate, quoteAsset: 'BTC' } },
    { universe: { ...analysisUniverse, status: 'NO_REFERENCE' } },
    { analysis: { ...analysisDetail.analysis, pipelineRunId: null } },
    { scope: { ...analysisDetail.scope, exchange: 'BINANCE' } },
    { order: { ...analysisDetail.order, market: 'KRW-ETH' } },
    { order: { ...analysisDetail.order, approvalRequestId: '999' } },
    { executionAttempts: [{ ...analysisDetail.executionAttempts[0], approvalRequestId: '999' }] },
    { executionAttempts: [{ ...analysisDetail.executionAttempts[0], orderLogId: '999' }] },
    { executionAttempts: [{ ...analysisDetail.executionAttempts[0], tradingMode: 'MOCK' }] },
  ])('rejects mismatched detail relationships %#', (patch) => {
    expect(() => parseAnalysisDetail(JSON.stringify({ ...analysisDetail, ...patch }))).toThrow();
  });
  it('rejects duplicate IDs and inconsistent page counts', () => {
    expect(() =>
      parseAnalysisRecommendations(
        JSON.stringify(analysisPage([analysisRecommendation, analysisRecommendation])),
      ),
    ).toThrow();
    expect(() =>
      parseAnalysisRecommendations(
        JSON.stringify({ ...analysisPage([analysisRecommendation]), totalElements: 2 }),
      ),
    ).toThrow();
  });
  it('preserves counts larger than JS safe integer', () => {
    const raw = JSON.stringify(analysisPage([], 10000, 100))
      .replace('"totalElements":0', '"totalElements":9007199254740993')
      .replace('"totalPages":0', '"totalPages":90071992547410');
    // Missing the required 100 rows must fail, rather than accepting a truncated page.
    expect(() => parseAnalysisRecommendations(raw)).toThrow();
    const rows = Array.from({ length: 100 }, (_, i) => ({
      ...analysisRecommendation,
      id: String(i + 1),
    }));
    const valid = JSON.stringify(analysisPage(rows, 0, 100))
      .replace('"totalElements":100', '"totalElements":9007199254740993')
      .replace('"totalPages":1', '"totalPages":90071992547410');
    expect(parseAnalysisRecommendations(valid).totalElements).toBe('9007199254740993');
  });
  it('keeps UNKNOWN null execution independent from recommendation approval', () => {
    const d = parseAnalysisDetail(
      JSON.stringify({
        ...analysisDetail,
        order: {
          ...analysisDetail.order,
          status: 'LIVE_UNKNOWN',
          executedQuantity: null,
          executedFundsKrw: null,
          averageExecutionPrice: null,
        },
      }),
    );
    expect(d.order?.executedQuantity).toBeNull();
    expect(d.order?.status).toBe('LIVE_UNKNOWN');
  });
});
