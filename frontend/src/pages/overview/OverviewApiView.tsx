import { useState } from 'react';
import { Link } from 'react-router';
import { useAnalysisSource } from '@/hooks/use-analysis-source';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { EmptyState, MetricCard, Panel, SectionHeading, StatusBadge } from '@/components/common';
import {
  overviewApiKey,
  useOverviewSummary,
  useOverviewPositions,
  useOverviewPerformance,
  useOverviewRecommendations,
} from '@/hooks/use-overview-api';
import {
  moneyText,
  percentText,
  decimalText,
  ratioText,
  valueTone,
  kstInstant,
} from '@/lib/overview-format';
import { performanceQuery } from '@/services/overview-api-client';
import type { Summary, Scope, PerformanceFilter, RecommendationFilter } from '@/types/overview-api';
import { QueryContent, Source, Time, Pages } from './api-components';
import { PerformanceChart } from './PerformanceChart';

function SummaryCards({ data }: { data: Summary }) {
  const { portfolio: p, performance: f, botPnl: b } = data;
  return (
    <>
      <Source data={data} />
      {!p && <p className="notice">계좌 snapshot이 없습니다. 0원 계좌를 의미하지 않습니다.</p>}
      <div className="grid-metrics overview-api-metrics">
        <MetricCard
          label="총 계좌 평가금액"
          value={moneyText(p?.totalValueKrw ?? null)}
          detail={
            p ? (
              <>
                <StatusBadge status={p.valuationStatus} /> · 저장된 총액
              </>
            ) : (
              'Snapshot 미기록'
            )
          }
        />
        <MetricCard
          label="계좌 누적 수익률"
          value={
            <span
              className={
                f?.performanceStatus === 'COMPLETE' ? valueTone(f.cumulativeReturnPercentage) : ''
              }
            >
              {percentText(f?.cumulativeReturnPercentage ?? null)}
            </span>
          }
          detail={
            f ? (
              <>
                <StatusBadge status={f.performanceStatus} /> ·{' '}
                {f.performanceStatus === 'BASELINE'
                  ? '새 기준점 · 검증된 성과 아님'
                  : f.performanceStatus === 'PARTIAL'
                    ? '성과 자료 불완전'
                    : f.returnMethod}
              </>
            ) : (
              '현재 snapshot의 성과 미기록'
            )
          }
        />
        <MetricCard
          label="봇 인식 실현손익"
          value={
            <span className={valueTone(b?.recognizedRealizedPnlKrw ?? null)}>
              {moneyText(b?.recognizedRealizedPnlKrw ?? null)}
            </span>
          }
          detail={
            b ? (
              <>
                <StatusBadge status={b.accountingStatus} /> · FIFO 매칭분
              </>
            ) : data.scope.quoteAsset !== 'KRW' ? (
              'KRW 외 quote에서는 미제공'
            ) : (
              '회계 요약 미기록'
            )
          }
        />
        <MetricCard
          label="저장된 보유 자산 수"
          value={p?.positionCount ?? '미기록'}
          detail={<>KRW 현금 · {moneyText(p?.cashTotalKrw ?? null)}</>}
        />
      </div>
      {p && (
        <dl className="overview-provenance">
          <div>
            <dt>Snapshot / 관측 시각</dt>
            <dd>
              {p.snapshotId}
              <br />
              <Time value={p.capturedAt} />
            </dd>
          </div>
          <div>
            <dt>평가 가능한 부분 금액 · 전체 총액 대체 아님</dt>
            <dd>{moneyText(p.knownTotalValueKrw)}</dd>
          </div>
          <div>
            <dt>평가 가능 position / 평가손익</dt>
            <dd>
              {moneyText(p.pricedPositionsValueKrw)} / {moneyText(p.unrealizedPnlKrw)}
            </dd>
          </div>
          <div>
            <dt>평가 불가 / 원가 미확인</dt>
            <dd>
              {p.unpricedAssetCount} / {p.missingCostBasisCount}
            </dd>
          </div>
        </dl>
      )}
      {f && (
        <p className="small text-muted">
          성과 계산 <Time value={f.calculatedAt} /> · 기간 종료 <Time value={f.periodEndAt} /> ·{' '}
          {f.safeReason ?? '추가 사유 없음'}
        </p>
      )}
      {b && (
        <dl className="overview-provenance">
          <div>
            <dt>Bot PnL 범위</dt>
            <dd>
              User {b.scope.userId} · {b.scope.exchange} · {b.scope.amountCurrency} 회계 · quote별
              집계 아님
            </dd>
          </div>
          <div>
            <dt>회계 계산 / 원본 최종 갱신</dt>
            <dd>
              <Time value={b.calculatedAt} />
              <br />
              <Time value={b.sourceLastUpdatedAt} />
            </dd>
          </div>
          <div>
            <dt>인식 실현 수익률 / 수수료</dt>
            <dd>
              {percentText(b.recognizedRealizedReturnPercentage)} / {moneyText(b.totalFeesKrw)}
            </dd>
          </div>
          <div>
            <dt>처리 주문 / 완전·부분·미매칭 SELL / 불완전 주문</dt>
            <dd>
              {b.processedOrderCount} / {b.fullyMatchedSellCount} · {b.partiallyMatchedSellCount} ·{' '}
              {b.unmatchedSellCount} / {b.incompleteOrderCount}
            </dd>
          </div>
        </dl>
      )}
      <p className="small text-muted">
        계좌 성과와 Bot PnL은 별개입니다. 각 관측·계산 시각은 다를 수 있고 서로 합산하지 않습니다.
      </p>
    </>
  );
}
function PerformanceHistory({ expectedScope }: { expectedScope?: Scope }) {
  const [filter, setFilter] = useState<PerformanceFilter>({ from: '', to: '', page: 0, size: 90 });
  const [draft, setDraft] = useState({ from: '', to: '' });
  const [error, setError] = useState('');
  const query = useOverviewPerformance(filter);
  return (
    <Panel title="저장된 계좌 성과 이력" description="Modified Dietz · 조회한 페이지의 성과 index">
      <form
        className="overview-controls"
        onSubmit={(e) => {
          e.preventDefault();
          try {
            const next = {
              ...filter,
              page: 0,
              from: kstInstant(draft.from),
              to: kstInstant(draft.to),
            };
            performanceQuery(next);
            setFilter(next);
            setError('');
          } catch {
            setError('기간을 확인하세요. 시작 시각은 종료 시각 이후일 수 없습니다.');
          }
        }}
      >
        <label>
          기간 시작 · KST
          <input
            className="field"
            aria-label="성과 기간 시작 · KST"
            type="datetime-local"
            value={draft.from}
            onChange={(e) => setDraft({ ...draft, from: e.target.value })}
          />
        </label>
        <label>
          기간 종료 · KST
          <input
            className="field"
            aria-label="성과 기간 종료 · KST"
            type="datetime-local"
            value={draft.to}
            onChange={(e) => setDraft({ ...draft, to: e.target.value })}
          />
        </label>
        <Button type="submit" variant="outline">
          기간 적용
        </Button>
        <label>
          페이지 크기
          <select
            className="field"
            aria-label="성과 페이지 크기"
            value={filter.size}
            onChange={(e) => setFilter({ ...filter, page: 0, size: Number(e.target.value) })}
          >
            {[30, 90, 200].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
      </form>
      {error && <p role="alert">{error}</p>}
      <p className="small text-muted">
        적용 기간: {filter.from ? <Time value={filter.from} /> : '시작 제한 없음'} ~{' '}
        {filter.to ? <Time value={filter.to} /> : '종료 제한 없음'} · periodEndAt 양쪽 경계 포함
      </p>
      <QueryContent query={query} expectedScope={expectedScope}>
        {(data) => (
          <>
            <Source data={data} />
            <p className="notice">
              현재 페이지 {data.items.length}개 기록만 표시합니다. {data.size}개는 {data.size}일을
              뜻하지 않습니다. PARTIAL·새 BASELINE·연속성 미확인 구간은 연결하지 않습니다. 차트
              좌표는 근사값이며 표는 저장된 정확한 값입니다.
            </p>
            {data.items.length ? (
              <>
                <PerformanceChart items={data.items} />
                <div className="table-scroll">
                  <table className="data-table">
                    <caption className="sr-only">API 성과 이력</caption>
                    <thead>
                      <tr>
                        <th>기간 종료 · KST / Snapshot</th>
                        <th>상태 / 사유</th>
                        <th className="numeric">기간 수익률</th>
                        <th className="numeric">누적 수익률</th>
                        <th className="numeric">Index</th>
                        <th className="numeric">Drawdown / MDD</th>
                        <th>계산 시각</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((p) => (
                        <tr key={p.id}>
                          <td>
                            <Time value={p.periodEndAt} />
                            <span className="table-subtext mono">{p.portfolioSnapshotId}</span>
                          </td>
                          <td>
                            <StatusBadge status={p.performanceStatus} />
                            <span className="table-subtext">{p.safeReason ?? p.returnMethod}</span>
                          </td>
                          <td className="numeric">{percentText(p.periodReturnPercentage)}</td>
                          <td className="numeric">{percentText(p.cumulativeReturnPercentage)}</td>
                          <td className="numeric">{decimalText(p.performanceIndex)}</td>
                          <td className="numeric">
                            {percentText(p.drawdownPercentage)} /{' '}
                            {percentText(p.maxDrawdownPercentage)}
                          </td>
                          <td>
                            <Time value={p.calculatedAt} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <EmptyState
                title="해당 기간의 성과 기록이 없습니다"
                description="누락된 기간을 0%로 채우지 않습니다."
              />
            )}
            <Pages data={data} label="성과" change={(page) => setFilter({ ...filter, page })} />
          </>
        )}
      </QueryContent>
    </Panel>
  );
}
function Recommendations({ expectedScope }: { expectedScope?: Scope }) {
  const analysis = useAnalysisSource();
  const [filter, setFilter] = useState<RecommendationFilter>({ action: '', page: 0, size: 10 });
  const query = useOverviewRecommendations(filter);
  return (
    <Panel title="최근 AI 추천" description="저장된 추천 상태 · 승인 요청·체결 이력과 구분">
      <div className="overview-controls">
        <label>
          판단
          <select
            className="field"
            aria-label="추천 action"
            value={filter.action}
            onChange={(e) =>
              setFilter({
                ...filter,
                page: 0,
                action: e.target.value as RecommendationFilter['action'],
              })
            }
          >
            <option value="">전체</option>
            {['BUY', 'SELL', 'HOLD'].map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </label>
        <label>
          페이지 크기
          <select
            className="field"
            aria-label="추천 페이지 크기"
            value={filter.size}
            onChange={(e) => setFilter({ ...filter, page: 0, size: Number(e.target.value) })}
          >
            {[10, 20, 50].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
      </div>
      <QueryContent query={query} expectedScope={expectedScope}>
        {(data) => (
          <>
            <Source data={data} />
            {data.items.length ? (
              <div className="table-scroll">
                <table className="data-table">
                  <caption className="sr-only">API 최근 추천</caption>
                  <thead>
                    <tr>
                      <th>생성 시각 · KST / 추천 ID</th>
                      <th>분석 ID / 마켓</th>
                      <th>판단</th>
                      <th className="numeric">Confidence</th>
                      <th className="numeric">Trade ratio</th>
                      <th>모델</th>
                      <th>저장된 추천 status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((r) => (
                      <tr key={r.id}>
                        <td>
                          <Time value={r.createdAt} />
                          <span className="table-subtext mono">{r.id}</span>
                        </td>
                        <td>
                          {r.market}
                          <span className="table-subtext mono">{r.analysisRunId}</span>
                        </td>
                        <td>
                          <StatusBadge status={r.action} />
                        </td>
                        <td className="numeric" title={r.confidence ?? '미기록'}>
                          {ratioText(r.confidence)}
                        </td>
                        <td className="numeric" title={r.tradeRatio ?? '미기록'}>
                          {ratioText(r.tradeRatio)}
                        </td>
                        <td>{r.aiModel ?? '미기록'}</td>
                        <td>
                          <code>{r.status}</code>
                          {analysis.available && (
                            <Link
                              className="text-button"
                              to={`/ai-analysis?recommendation=${r.id}`}
                              state={{ analysisReference: { id: r.id, scope: data.scope } }}
                              onClick={() => analysis.setSource('api')}
                              aria-label={`${r.id} 실제 추천 상세`}
                            >
                              상세 조회
                            </Link>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="조건에 맞는 추천이 없습니다" />
            )}
            <Pages data={data} label="추천" change={(page) => setFilter({ ...filter, page })} />
          </>
        )}
      </QueryContent>
      <p className="small text-muted">
        Confidence는 검증된 성공 확률이 아니고 trade ratio는 실제 체결 비율이 아닙니다. 추천
        status만으로 실제 체결을 확정하지 않습니다. AI Analysis 로컬 API가 활성화된 경우에만 실제
        추천 상세로 연결합니다. DEMO 상세로 대체하지 않습니다.
      </p>
    </Panel>
  );
}
export default function OverviewApiView() {
  const client = useQueryClient();
  const summary = useOverviewSummary();
  const ready = summary.isSuccess && !summary.isFetching;
  const positions = useOverviewPositions(summary.data, ready);
  const refresh = () => {
    void client.invalidateQueries({ queryKey: overviewApiKey });
  };
  return (
    <div className="page-stack overview-api">
      <SectionHeading
        eyebrow="LOCAL API · READ ONLY"
        title="저장된 계좌와 성과를 확인합니다."
        description="PostgreSQL에 기록된 관측값입니다. 실시간 잔고나 거래소 실시간 상태를 의미하지 않습니다."
        action={
          <Button variant="outline" onClick={refresh}>
            API 새로고침
          </Button>
        }
      />
      <Panel title="계좌 평가와 성과">
        <QueryContent query={summary}>{(data) => <SummaryCards data={data} />}</QueryContent>
      </Panel>
      <Panel
        title="최신 Snapshot 보유 자산"
        description="계좌 요약과 같은 snapshot만 표시 · 현금은 요약에서 확인"
      >
        {!ready ? (
          <p role="status">계좌 요약 조회가 성공하면 동일 snapshot의 보유 자산을 확인합니다.</p>
        ) : (
          <QueryContent query={positions} expectedScope={summary.data.scope} retry={refresh}>
            {(data) => (
              <>
                <Source data={data} />
                {!data.portfolio ? (
                  <EmptyState title="계좌 snapshot이 없습니다" />
                ) : (
                  <>
                    <p className="small text-muted">
                      Snapshot {data.portfolio.snapshotId} · 관측{' '}
                      <Time value={data.portfolio.capturedAt} />
                    </p>
                    {data.items.length ? (
                      <div className="table-scroll">
                        <table className="data-table">
                          <caption className="sr-only">API 보유 자산</caption>
                          <thead>
                            <tr>
                              <th>자산 / 마켓</th>
                              <th>평가 상태 / 출처</th>
                              <th className="numeric">총 수량 / 사용 가능 / 잠금</th>
                              <th className="numeric">현재 평가 가격</th>
                              <th className="numeric">평가금액</th>
                              <th className="numeric">추정 원가</th>
                              <th className="numeric">평가손익</th>
                            </tr>
                          </thead>
                          <tbody>
                            {data.items.map((p) => (
                              <tr key={p.id}>
                                <td>
                                  {p.currency}
                                  <span className="table-subtext">
                                    {p.market ?? '거래쌍 미기록'}
                                  </span>
                                </td>
                                <td>
                                  <StatusBadge status={p.valuationStatus} />
                                  <span className="table-subtext">
                                    {p.priceSource ?? '출처 미기록'}
                                  </span>
                                </td>
                                <td className="numeric">
                                  {decimalText(p.totalQuantity)}
                                  <span className="table-subtext">
                                    {decimalText(p.availableQuantity)} /{' '}
                                    {decimalText(p.lockedQuantity)}
                                  </span>
                                </td>
                                <td className="numeric">{moneyText(p.markPrice)}</td>
                                <td className="numeric">{moneyText(p.marketValueKrw)}</td>
                                <td className="numeric">{moneyText(p.estimatedCostBasisKrw)}</td>
                                <td className={'numeric ' + valueTone(p.unrealizedPnlKrw)}>
                                  {moneyText(p.unrealizedPnlKrw)}
                                  <span className="table-subtext">
                                    {percentText(p.unrealizedPnlPercentage)}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <EmptyState title="이 snapshot에 저장된 보유 자산이 없습니다" />
                    )}
                  </>
                )}
                <p className="small text-muted">
                  평가 불가·명시적 제외 자산도 남겨 표시합니다. 누락된 가격이나 원가는 0으로
                  대체하지 않습니다. 계좌 비중은 재계산하지 않습니다.
                </p>
              </>
            )}
          </QueryContent>
        )}
      </Panel>
      <PerformanceHistory expectedScope={ready ? summary.data.scope : undefined} />
      <Recommendations expectedScope={ready ? summary.data.scope : undefined} />
      <Panel title="현재 API 미제공">
        <p>
          전일 평가금액 증감, 현재 Runtime 정책·Gate 판정, worker 상태, 실제 주문·체결 상세는 이
          화면에서 조회하지 않습니다. DEMO 값으로 채우거나 운영 정상 상태로 추정하지 않습니다.
        </p>
        <p className="small text-muted">
          다른 화면으로 이동하면 해당 화면의 DEMO / LOCAL API 출처를 확인하세요. 거래·승인·정책 변경
          기능은 없습니다.
        </p>
      </Panel>
    </div>
  );
}
