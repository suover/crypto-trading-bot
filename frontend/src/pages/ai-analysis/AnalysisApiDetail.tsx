import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { useAnalysisDetail, useAnalysisCandidates } from '@/hooks/use-analysis-api';
import { Source, Time, Pages } from '@/pages/overview/api-components';
import { decimalText, moneyText, ratioText } from '@/lib/overview-format';
import type { Detail, DetailReference, Candidate, Timeframe } from '@/types/analysis-api';
import { metrics } from '@/types/analysis-api';
import { AnalysisQuery } from './api-components';
const universeText = {
  NO_REFERENCE: '당시 Universe 참조가 기록되지 않았습니다. 후보가 0개였다는 뜻은 아닙니다.',
  INVALID_REFERENCE:
    '당시 Universe 연결 검증에 실패했습니다. 다른 분석의 후보로 대체하지 않습니다.',
  VERIFIED: '당시 Universe 연결 검증 완료',
};
const eligible = (value: boolean | null) => (value === null ? '미기록' : value ? '가능' : '불가');
const labels: Record<(typeof metrics)[number], string> = {
  latest_price: '분석 당시 저장 가격',
  sma_5: 'SMA 5',
  sma_20: 'SMA 20',
  ema_5: 'EMA 5',
  ema_20: 'EMA 20',
  rsi_14: 'RSI 14',
  recent_change_rate: '최근 변화율',
  volume_ratio: '거래량 비율',
  macd: 'MACD',
  macd_signal: 'MACD Signal',
  macd_histogram: 'MACD Histogram',
  atr_14: 'ATR 14',
  atr_percentage: 'ATR percentage',
  realized_volatility: '실현 변동성',
  max_drawdown: '최대 낙폭',
};
function Frame({ frame }: { frame: Timeframe }) {
  return (
    <section className="drawer-section">
      <h4>
        {frame.timeframe} · {frame.trendLabel ?? '추세 미기록'}
      </h4>
      <p>
        데이터 품질 {frame.dataQuality ?? '미기록'} · 봉 수 {frame.candleCount ?? '미기록'}
      </p>
      {frame.invalidFields.length > 0 && (
        <p role="status">읽기 불가 필드: {frame.invalidFields.join(', ')}</p>
      )}
      <dl className="detail-grid">
        {metrics.map((metric) => (
          <div key={metric}>
            <dt className="stat-label">{labels[metric]}</dt>
            <dd className="stat-value mono">{decimalText(frame.indicators[metric] ?? null)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
function CandidateFeatures({ candidate }: { candidate: Candidate }) {
  return (
    <div>
      <h3>{candidate.market} · 저장된 다중 시간봉</h3>
      <p>
        데이터 품질 {candidate.features.dataQuality ?? '미기록'} · 읽기 품질{' '}
        {candidate.features.projectionStatus}
      </p>
      <p className="small text-muted">
        읽기 COMPLETE는 데이터 SUFFICIENT를 뜻하지 않습니다. 가격은 {candidate.quoteAsset} 단위,
        나머지 지표는 저장된 원본 단위입니다. 현재가·지표를 재계산하지 않습니다.
      </p>
      {candidate.features.timeframes.length ? (
        candidate.features.timeframes.map((f) => <Frame key={f.timeframe} frame={f} />)
      ) : (
        <p>저장된 시간봉 지표를 제공할 수 없습니다.</p>
      )}
    </div>
  );
}
function CandidatesPanel({ detail }: { detail: Detail }) {
  const [page, setPage] = useState(0);
  const query = useAnalysisCandidates(detail, page);
  if (detail.universe.status !== 'VERIFIED') return null;
  return (
    <section className="drawer-section">
      <h3>당시 Universe 후보</h3>
      <AnalysisQuery query={query}>
        {(data) => (
          <>
            <Source data={data} />
            <p className="small text-muted">
              후보의 BUY/SELL 가능 여부는 분석 당시 값입니다. 현재 주문 가능 여부가 아닙니다.
            </p>
            <div className="table-scroll">
              <table className="data-table compact-table" aria-label="API 당시 후보">
                <thead>
                  <tr>
                    <th>마켓</th>
                    <th>출처</th>
                    <th>순위 / 점수</th>
                    <th>24h 거래대금</th>
                    <th>BUY / SELL</th>
                    <th>지표</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((c) => (
                    <tr key={c.id}>
                      <td>
                        {c.market}
                        <div className="small mono">{c.id}</div>
                      </td>
                      <td>{c.selectionSource}</td>
                      <td>
                        {c.rank ?? '미기록'} / {decimalText(c.score)}
                      </td>
                      <td>
                        {decimalText(c.quoteTradeValue24h)} {c.quoteAsset}
                      </td>
                      <td>
                        {eligible(c.buyEligible)} / {eligible(c.sellEligible)}
                      </td>
                      <td>
                        <details>
                          <summary>{c.market} 지표 보기</summary>
                          <CandidateFeatures candidate={c} />
                        </details>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pages data={data} label="후보" change={setPage} />
          </>
        )}
      </AnalysisQuery>
    </section>
  );
}
function Execution({ detail: d }: { detail: Detail }) {
  const o = d.order;
  return (
    <section className="drawer-section">
      <h3>승인 · 주문 시도 · 체결 기록</h3>
      <p>추천 상태: {d.recommendation.status}</p>
      {d.recommendation.action === 'HOLD' && (
        <p>HOLD는 정상적인 관망 판단입니다. 승인 요청이 필요하지 않습니다.</p>
      )}
      <h4>승인 기록</h4>
      {d.approvals.length ? (
        <ul>
          {d.approvals.map((a) => (
            <li key={a.id}>
              승인 요청 {a.id} · {a.status} · 생성 <Time value={a.createdAt} /> · 만료{' '}
              <Time value={a.expiresAt} /> · 승인 <Time value={a.approvedAt} /> · 거절{' '}
              <Time value={a.rejectedAt} />
            </li>
          ))}
        </ul>
      ) : (
        <p>검증된 승인 기록 없음</p>
      )}
      <h4>실행 시도</h4>
      {d.executionAttempts.length ? (
        <ul>
          {d.executionAttempts.map((a) => (
            <li key={a.id}>
              시도 {a.id} · 승인 요청 {a.approvalRequestId} · {a.tradingMode} · {a.attemptNumber}회
              · {a.status} · {a.errorCode ?? '오류 코드 미기록'} · <Time value={a.attemptedAt} /> ·
              다음 재시도 <Time value={a.nextRetryAt} />
            </li>
          ))}
        </ul>
      ) : (
        <p>검증된 실행 시도 없음</p>
      )}
      <h4>저장된 주문 · 체결 요약</h4>
      {o ? (
        <>
          <p>
            주문 {o.id} · {o.tradingMode} · {o.side} · {o.orderType} · {o.status}
          </p>
          {o.status === 'LIVE_EXECUTED_CANCELLED' && <p>실거래 체결 확인 (미체결 잔량 취소)</p>}
          {o.status === 'LIVE_UNKNOWN' && (
            <p>실거래 결과 미확정입니다. 체결 성공이나 0 체결로 해석하지 않습니다.</p>
          )}
          <dl className="detail-grid">
            {[
              ['주문 기록 금액', moneyText(o.amountKrw)],
              ['주문 기록 수량', decimalText(o.quantity)],
              ['주문 기록 가격', decimalText(o.price)],
              ['실제 체결 수량', decimalText(o.executedQuantity)],
              ['실제 체결 금액 · 수수료 별도', moneyText(o.executedFundsKrw)],
              ['평균 체결가', decimalText(o.averageExecutionPrice)],
              ['지불 수수료 · 원본 quote 단위', decimalText(o.paidFee)],
              ['미체결 잔량', decimalText(o.remainingQuantity)],
              ['체결 건수', String(o.tradesCount ?? '미기록')],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="stat-label">{label}</dt>
                <dd className="stat-value mono">{value}</dd>
              </div>
            ))}
          </dl>
          <p>
            체결 동기화 <Time value={o.executionSyncedAt} /> · 주문 갱신{' '}
            <Time value={o.updatedAt} />
          </p>
        </>
      ) : (
        <p>검증된 주문 기록 없음</p>
      )}
      <p className="small text-muted">
        빈 기록은 실제 주문이 없었다는 증명이 아닙니다. 추천·승인·체결 상태는 독립적이며, 현재
        시각으로 만료나 체결을 재판정하지 않습니다.
      </p>
    </section>
  );
}
export default function AnalysisApiDetail({ reference }: { reference: DetailReference }) {
  const query = useAnalysisDetail(reference);
  return (
    <AnalysisQuery query={query}>
      {(d) => (
        <div className="stack analysis-api">
          <Source data={d} />
          <p>
            추천 {d.recommendation.id} · 분석 실행 {d.analysis.id} · {d.recommendation.market} ·{' '}
            {d.recommendation.action}
          </p>
          <p>
            추천 시각 <Time value={d.recommendation.createdAt} /> · 모델{' '}
            {d.recommendation.aiModel ?? '미기록'}
          </p>
          <p className="break-all mono">
            Pipeline: {d.analysis.pipelineRunId ?? '미기록'} · {d.analysis.runType} ·{' '}
            {d.analysis.tradingMode} · {d.analysis.status}
          </p>
          <p>
            거래 비율 {ratioText(d.recommendation.tradeRatio)} · Confidence{' '}
            {ratioText(d.recommendation.confidence)}
          </p>
          <p className="small text-muted">
            Confidence는 모델 보고값이며 검증된 성공 확률이 아닙니다.
          </p>
          <section>
            <h3>최종 판단 사유 · 애플리케이션 안전 규칙 반영</h3>
            <p className="reason-text">{d.finalDecisionReason}</p>
            <p>
              추천 금액 {moneyText(d.recommendedAmountKrw)} · 추천 수량{' '}
              {decimalText(d.recommendedQuantity)}
            </p>
            <p className="small text-muted">추천 당시 sizing이며 실제 주문·체결값과 구분합니다.</p>
          </section>
          <p>{universeText[d.universe.status]}</p>
          {d.selectedCandidate && <CandidateFeatures candidate={d.selectedCandidate} />}
          <CandidatesPanel key={d.universe.analysisRunId ?? 'none'} detail={d} />
          <Execution detail={d} />
          <Button
            variant="outline"
            onClick={() => {
              void query.refetch();
            }}
          >
            상세 새로고침
          </Button>
        </div>
      )}
    </AnalysisQuery>
  );
}
