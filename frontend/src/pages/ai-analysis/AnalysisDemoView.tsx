import { useState } from 'react';
import { useSearchParams } from 'react-router';
import {
  ArrowDownLeft,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Eye,
  Minus,
  Search,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import {
  DetailDrawer,
  EmptyState,
  MetricCard,
  Panel,
  SectionHeading,
  StatusBadge,
} from '@/components/common';
import { Button } from '@/components/ui/button';
import { useDashboardData } from '@/hooks/use-dashboard-data';
import { dateTime, krw, quantity, ratio, percent } from '@/lib/format';
import type { AnalysisRun, ApprovalStatus, DashboardData, TradeAction } from '@/types/dashboard';
import { selectAnalyses, type AnalysisFilters } from './analysis-selectors';
import './analysis.css';

const PAGE_SIZE = 8;
const initialFilters: AnalysisFilters = {
  search: '',
  action: 'ALL',
  approval: 'ALL',
  days: null,
  sort: 'newest',
};

function AnalysisDetail({ analysis, data }: { analysis: AnalysisRun; data: DashboardData }) {
  const recommendation = analysis.recommendation;
  const candidate = analysis.candidates.find((item) => item.market === recommendation.market);
  const order = data.portfolio.orders.find((item) => item.id === recommendation.orderId);

  return (
    <div className="stack">
      <div className="notice">
        <ShieldCheck size={17} aria-hidden="true" />
        <span>
          조회 전용 데모입니다. BUY/SELL 추천과 Telegram 승인, 주문 체결은 서로 다른 단계입니다.
        </span>
      </div>
      <div className="detail-grid">
        <div>
          <span className="stat-label">분석 ID</span>
          <strong className="stat-value mono">{analysis.id}</strong>
        </div>
        <div>
          <span className="stat-label">분석 시각 · KST</span>
          <strong className="stat-value">{dateTime(analysis.startedAt)}</strong>
        </div>
        <div>
          <span className="stat-label">AI 모델</span>
          <strong className="stat-value">{recommendation.model}</strong>
        </div>
        <div>
          <span className="stat-label">Market Universe</span>
          <strong className="stat-value">
            {analysis.universeMode} · {analysis.candidates.length}개 후보
          </strong>
        </div>
        <div>
          <span className="stat-label">선택 마켓 · 행동</span>
          <strong className="stat-value">
            {recommendation.market} <StatusBadge status={recommendation.action} />
          </strong>
        </div>
        <div>
          <span className="stat-label">거래 비율</span>
          <strong className="stat-value">{ratio(recommendation.tradeRatio)}</strong>
        </div>
      </div>
      <div className="drawer-section">
        <h3>Pipeline identity</h3>
        <p className="mono text-muted break-all">{analysis.pipelineRunId}</p>
      </div>
      <div className="drawer-section">
        <div className="section-inline">
          <h3>AI 판단 사유</h3>
          <span className="chip">
            <Sparkles size={13} aria-hidden="true" /> Confidence {ratio(recommendation.confidence)}
          </span>
        </div>
        <p className="reason-text">{recommendation.reason}</p>
        <p className="text-muted small">
          Confidence는 모델이 보고한 수치이며 검증된 성공 확률이 아닙니다. 정확한 주문 금액·수량은
          애플리케이션이 별도로 계산합니다.
        </p>
      </div>
      {candidate ? (
        <div className="drawer-section">
          <div className="section-inline">
            <h3>다중 시간봉 분석</h3>
            <span className="text-muted small">
              {candidate.market} · 현재가 {krw(candidate.currentPriceKrw)}
            </span>
          </div>
          <div className="table-scroll">
            <table className="data-table compact-table">
              <caption className="sr-only">선택 마켓의 시간봉별 지표와 데이터 품질</caption>
              <thead>
                <tr>
                  <th>시간봉</th>
                  <th>추세 / 품질</th>
                  <th className="numeric">SMA 5 / 20</th>
                  <th className="numeric">EMA 5 / 20</th>
                  <th className="numeric">RSI 14</th>
                  <th className="numeric">MACD</th>
                  <th className="numeric">ATR 14</th>
                  <th className="numeric">변동성</th>
                  <th className="numeric">거래량 비율</th>
                </tr>
              </thead>
              <tbody>
                {candidate.timeframes.map((timeframe) => (
                  <tr key={timeframe.timeframe}>
                    <td className="mono">{timeframe.timeframe}</td>
                    <td>
                      <StatusBadge status={timeframe.trend} />
                      <div className="table-subtext">{timeframe.quality}</div>
                    </td>
                    <td className="numeric">
                      {krw(timeframe.sma5)}
                      <div className="table-subtext">{krw(timeframe.sma20)}</div>
                    </td>
                    <td className="numeric">
                      {krw(timeframe.ema5)}
                      <div className="table-subtext">{krw(timeframe.ema20)}</div>
                    </td>
                    <td className="numeric">{quantity(timeframe.rsi14)}</td>
                    <td className="numeric">{quantity(timeframe.macd)}</td>
                    <td className="numeric">{krw(timeframe.atr14)}</td>
                    <td className="numeric">{percent(timeframe.volatilityPercent)}</td>
                    <td className="numeric">
                      {timeframe.volumeRatio === null ? '—' : `${quantity(timeframe.volumeRatio)}×`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-muted small">
            24시간 KRW 거래대금 {krw(candidate.quoteTradeValue24hKrw)} · 없는 원본 지표는 —로
            표시합니다.
          </p>
        </div>
      ) : (
        <EmptyState
          title="시장 지표가 없습니다"
          description="해당 분석에 저장된 마켓 지표가 없어 추정하지 않습니다."
        />
      )}
      <div className="drawer-section">
        <h3>이번 분석의 후보</h3>
        <div className="table-scroll">
          <table className="data-table compact-table">
            <thead>
              <tr>
                <th>마켓</th>
                <th>선정 출처</th>
                <th className="numeric">Rank / Score</th>
                <th>BUY 가능</th>
                <th>SELL 가능</th>
              </tr>
            </thead>
            <tbody>
              {analysis.candidates.map((item) => (
                <tr key={item.id}>
                  <td className="mono">{item.market}</td>
                  <td>
                    <StatusBadge status={item.selectionSource} />
                  </td>
                  <td className="numeric">
                    {item.rank ?? '—'} / {quantity(item.score)}
                  </td>
                  <td>{item.buyEligible ? '가능' : '불가'}</td>
                  <td>{item.sellEligible ? '가능' : '불가'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="drawer-section">
        <h3>추천 → 승인 → 체결</h3>
        <div className="execution-steps">
          <div>
            <span className="stat-label">01 추천 상태</span>
            <StatusBadge status={recommendation.status} />
          </div>
          <div>
            <span className="stat-label">02 Telegram 승인</span>
            <StatusBadge status={recommendation.approvalStatus} />
          </div>
          <div>
            <span className="stat-label">03 주문 처리</span>
            <StatusBadge status={recommendation.executionStatus} />
          </div>
        </div>
        {recommendation.action === 'HOLD' && (
          <p className="text-muted">
            HOLD는 정상적인 관망 판단입니다. 승인 요청이나 주문을 생성하지 않습니다.
          </p>
        )}
        {order ? (
          <div className="detail-grid">
            <div>
              <span className="stat-label">주문 ID</span>
              <strong className="stat-value mono">{order.id}</strong>
            </div>
            <div>
              <span className="stat-label">체결 수량</span>
              <strong className="stat-value">{quantity(order.executedQuantity)}</strong>
            </div>
            <div>
              <span className="stat-label">평균 체결가</span>
              <strong className="stat-value">{krw(order.averagePriceKrw)}</strong>
            </div>
            <div>
              <span className="stat-label">체결 금액 · 수수료 별도</span>
              <strong className="stat-value">{krw(order.executedFundsKrw)}</strong>
            </div>
          </div>
        ) : (
          <p className="text-muted small">
            연결된 주문 체결 내역이 없습니다. 추천값을 체결값으로 표시하지 않습니다.
          </p>
        )}
      </div>
    </div>
  );
}

export default function AnalysisDemoView() {
  const { data } = useDashboardData();
  const [filters, setFilters] = useState<AnalysisFilters>(initialFilters);
  const [page, setPage] = useState(1);
  const [searchParams, setSearchParams] = useSearchParams();
  if (!data) return null;

  const filtered = selectAnalyses(data.analyses, filters, data.asOf);
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages);
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const selectedId = searchParams.get('analysis');
  const selected = data.analyses.find((analysis) => analysis.id === selectedId);
  const counts = (action: TradeAction) =>
    data.analyses.filter((analysis) => analysis.recommendation.action === action).length;

  function updateFilters(next: Partial<AnalysisFilters>) {
    setFilters((current) => ({ ...current, ...next }));
    setPage(1);
  }

  function closeDetail() {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.delete('analysis');
      return next;
    });
  }

  return (
    <div className="page-stack">
      <SectionHeading
        eyebrow="AI DECISION INTELLIGENCE"
        title="판단의 근거를, 한눈에"
        description="시장 분석부터 추천·승인·체결까지. 각 단계의 기록을 분리해 확인하세요."
        action={
          <span className="chip">
            <Sparkles size={14} aria-hidden="true" /> OpenAI · Structured Output
          </span>
        }
      />
      <div className="grid-metrics">
        <MetricCard
          label="전체 분석 기록"
          value={`${data.analyses.length}회`}
          detail="데모 데이터 전체 기간"
        />
        <MetricCard
          label="BUY · 매수 판단"
          value={`${counts('BUY')}회`}
          detail="추천 이후 Telegram 승인 필요"
        />
        <MetricCard
          label="SELL · 매도 판단"
          value={`${counts('SELL')}회`}
          detail="보유 자산 내 매도 검토"
        />
        <MetricCard
          label="HOLD · 관망 판단"
          value={`${counts('HOLD')}회`}
          detail="승인 요청 없이 정상 종료"
        />
      </div>
      <Panel
        title="AI 분석 기록"
        description="모든 값은 고정된 데모 스냅샷입니다. Confidence는 모델의 보고값입니다."
        action={<span className="chip">UPBIT · KRW</span>}
      >
        <div className="toolbar">
          <label className="search-field">
            <Search size={16} aria-hidden="true" />
            <input
              className="field"
              aria-label="마켓 검색"
              placeholder="마켓 검색, 예: BTC"
              value={filters.search}
              onChange={(event) => updateFilters({ search: event.target.value })}
            />
          </label>
          <label className="filter-label">
            <span className="sr-only">판단 필터</span>
            <select
              className="field"
              aria-label="판단 필터"
              value={filters.action}
              onChange={(event) =>
                updateFilters({ action: event.target.value as AnalysisFilters['action'] })
              }
            >
              <option value="ALL">모든 판단</option>
              <option value="BUY">BUY · 매수</option>
              <option value="SELL">SELL · 매도</option>
              <option value="HOLD">HOLD · 관망</option>
            </select>
          </label>
          <label className="filter-label">
            <span className="sr-only">승인 상태 필터</span>
            <select
              className="field"
              aria-label="승인 상태 필터"
              value={filters.approval}
              onChange={(event) =>
                updateFilters({ approval: event.target.value as ApprovalStatus | 'ALL' })
              }
            >
              <option value="ALL">모든 승인 상태</option>
              <option value="PENDING">승인 대기</option>
              <option value="APPROVED">승인 완료</option>
              <option value="REJECTED">거절</option>
              <option value="EXPIRED">만료</option>
              <option value="NOT_REQUIRED">승인 불필요</option>
            </select>
          </label>
          <select
            className="field"
            aria-label="분석 기간"
            value={filters.days ?? 'ALL'}
            onChange={(event) =>
              updateFilters({
                days: event.target.value === 'ALL' ? null : Number(event.target.value),
              })
            }
          >
            <option value="ALL">전체 기간</option>
            <option value="7">최근 7일</option>
            <option value="30">최근 30일</option>
            <option value="90">최근 90일</option>
          </select>
          <select
            className="field"
            aria-label="분석 정렬"
            value={filters.sort}
            onChange={(event) =>
              updateFilters({ sort: event.target.value as AnalysisFilters['sort'] })
            }
          >
            <option value="newest">최신순</option>
            <option value="oldest">오래된순</option>
          </select>
        </div>
        {visible.length ? (
          <div className="table-scroll">
            <table className="data-table analysis-table">
              <caption className="sr-only">
                AI 추천과 별도로 관리되는 승인 및 주문 처리 상태
              </caption>
              <thead>
                <tr>
                  <th>분석 시각 · KST</th>
                  <th>선택 마켓</th>
                  <th>판단</th>
                  <th className="numeric">Confidence</th>
                  <th className="numeric">거래 비율</th>
                  <th>Telegram 승인</th>
                  <th>주문 상태</th>
                  <th>
                    <span className="sr-only">상세보기</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((analysis) => {
                  const recommendation = analysis.recommendation;
                  const symbol = recommendation.market.replace('KRW-', '');
                  const ActionIcon =
                    recommendation.action === 'BUY'
                      ? ArrowUpRight
                      : recommendation.action === 'SELL'
                        ? ArrowDownLeft
                        : Minus;
                  return (
                    <tr key={analysis.id}>
                      <td>
                        <span>{dateTime(analysis.startedAt)}</span>
                        <div className="table-subtext mono">{analysis.id}</div>
                      </td>
                      <td>
                        <div className="asset-cell">
                          <span
                            className={`asset-symbol asset-${symbol.toLowerCase()}`}
                            aria-hidden="true"
                          >
                            {symbol.slice(0, 1)}
                          </span>
                          <div>
                            <strong>{symbol}</strong>
                            <span className="table-subtext">{recommendation.market}</span>
                          </div>
                        </div>
                      </td>
                      <td>
                        <span
                          className={`action-label action-${recommendation.action.toLowerCase()}`}
                        >
                          <ActionIcon size={14} aria-hidden="true" />
                          {recommendation.action}
                        </span>
                      </td>
                      <td className="numeric">
                        <span>{ratio(recommendation.confidence)}</span>
                        <div className="confidence-track" aria-hidden="true">
                          <span style={{ width: `${(recommendation.confidence ?? 0) * 100}%` }} />
                        </div>
                      </td>
                      <td className="numeric">{ratio(recommendation.tradeRatio)}</td>
                      <td>
                        <StatusBadge status={recommendation.approvalStatus} />
                      </td>
                      <td>
                        <StatusBadge status={recommendation.executionStatus} />
                      </td>
                      <td>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`${analysis.id} 분석 상세`}
                          onClick={() =>
                            setSearchParams((previous) => {
                              const next = new URLSearchParams(previous);
                              next.set('analysis', analysis.id);
                              return next;
                            })
                          }
                        >
                          <Eye size={16} />
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="조건에 맞는 분석이 없습니다"
            description="다른 마켓이나 기간으로 검색해 보세요."
          />
        )}
        <div className="pagination analysis-pagination">
          <span className="text-muted small">
            총 {filtered.length}개 · {filtered.length ? (currentPage - 1) * PAGE_SIZE + 1 : 0}–
            {Math.min(currentPage * PAGE_SIZE, filtered.length)} 표시
          </span>
          <div className="pagination-actions">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setFilters(initialFilters);
                setPage(1);
              }}
            >
              필터 초기화
            </Button>
            <Button
              variant="outline"
              size="icon"
              aria-label="이전 분석 페이지"
              disabled={currentPage === 1}
              onClick={() => setPage(currentPage - 1)}
            >
              <ChevronLeft size={16} />
            </Button>
            <span className="small mono" aria-live="polite">
              {currentPage} / {pages}
            </span>
            <Button
              variant="outline"
              size="icon"
              aria-label="다음 분석 페이지"
              disabled={currentPage === pages}
              onClick={() => setPage(currentPage + 1)}
            >
              <ChevronRight size={16} />
            </Button>
          </div>
        </div>
      </Panel>
      <div className="notice">
        <ShieldCheck size={17} aria-hidden="true" />
        <span>
          HOLD는 실패가 아닌 정상적인 판단입니다. 실제 주문은 유효한 Telegram 승인과 모든 실행
          안전장치를 통과한 BUY/SELL만 가능합니다.
        </span>
      </div>
      <DetailDrawer
        open={Boolean(selectedId)}
        onOpenChange={(open) => {
          if (!open) closeDetail();
        }}
        title={selected ? `${selected.recommendation.market} 분석 상세` : '분석을 찾을 수 없습니다'}
        description={
          selected
            ? `${dateTime(selected.startedAt)} KST · ${selected.id} · Mock data`
            : '현재 데모 데이터에 해당 분석 ID가 없습니다.'
        }
      >
        {selected ? (
          <AnalysisDetail analysis={selected} data={data} />
        ) : (
          <EmptyState title="분석 기록 없음" description="목록에서 다른 분석을 선택해 주세요." />
        )}
      </DetailDrawer>
    </div>
  );
}
