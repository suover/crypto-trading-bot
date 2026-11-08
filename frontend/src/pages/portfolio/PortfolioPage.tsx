import { useState } from 'react';
import { Link } from 'react-router';
import { ArrowUpRight, CircleHelp, Eye, Search, ShieldCheck, Wallet } from 'lucide-react';
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import {
  CoinIdentity,
  DetailDrawer,
  EmptyState,
  InfoTip,
  MetricCard,
  Pagination,
  Panel,
  SectionHeading,
  StatusBadge,
} from '@/components/common';
import { PortfolioChart } from '@/components/charts/PortfolioChart';
import { Button } from '@/components/ui/button';
import { useDashboardData } from '@/hooks/use-dashboard-data';
import { dateTime, krw, percent, quantity, toNumber } from '@/lib/format';
import type { OrderExecution } from '@/types/dashboard';
import {
  allocationGroups,
  financialTone,
  selectPositions,
  type PositionSort,
} from './portfolio-selectors';
import './portfolio.css';

type PortfolioTab = 'holdings' | 'orders' | 'pnl';
const PAGE_SIZE = 4;

function ExecutionDetail({ order, analysisId }: { order: OrderExecution; analysisId?: string }) {
  return (
    <div className="stack">
      <div className="notice">
        <ShieldCheck size={17} aria-hidden="true" />
        <span>
          저장된 체결을 설명하는 Mock 기록입니다. 이 화면은 주문 생성·취소·재시도를 지원하지
          않습니다.
        </span>
      </div>
      <div className="detail-grid">
        <div>
          <span className="stat-label">주문 ID</span>
          <strong className="stat-value mono">{order.id}</strong>
        </div>
        <div>
          <span className="stat-label">추천 ID</span>
          <strong className="stat-value mono">{order.recommendationId}</strong>
        </div>
        <div>
          <span className="stat-label">마켓 · 방향</span>
          <strong className="stat-value">
            {order.market} <StatusBadge status={order.side} />
          </strong>
        </div>
        <div>
          <span className="stat-label">주문 시각 · KST</span>
          <strong className="stat-value">{dateTime(order.createdAt)}</strong>
        </div>
        <div>
          <span className="stat-label">처리 상태</span>
          <StatusBadge status={order.status} />
        </div>
        <div>
          <span className="stat-label">총 체결 수량</span>
          <strong className="stat-value">{quantity(order.executedQuantity)}</strong>
        </div>
        <div>
          <span className="stat-label">평균 체결가</span>
          <strong className="stat-value">{krw(order.averagePriceKrw)}</strong>
        </div>
        <div>
          <span className="stat-label">체결 금액</span>
          <strong className="stat-value">{krw(order.executedFundsKrw)}</strong>
        </div>
        <div>
          <span className="stat-label">지불 수수료</span>
          <strong className="stat-value">{krw(order.feeKrw)}</strong>
        </div>
      </div>
      {order.status === 'LIVE_UNKNOWN' && (
        <p className="notice warning-notice">
          응답이 불확실하여 체결을 확정할 수 없습니다. 미확정 수량·금액을 0으로 해석하지 않습니다.
        </p>
      )}
      {order.status === 'LIVE_EXECUTED_CANCELLED' && (
        <p className="notice">
          양수 체결량은 확정된 체결입니다. 취소는 남은 미체결 잔량의 종료를 의미합니다.
        </p>
      )}
      <div className="drawer-section">
        <h3>개별 체결 · {order.fills.length}건</h3>
        {order.fills.length ? (
          <div className="table-scroll">
            <table className="data-table compact-table">
              <thead>
                <tr>
                  <th>체결 시각 · KST</th>
                  <th className="numeric">수량</th>
                  <th className="numeric">가격</th>
                  <th className="numeric">금액</th>
                  <th className="numeric">수수료</th>
                </tr>
              </thead>
              <tbody>
                {order.fills.map((fill) => (
                  <tr key={fill.id}>
                    <td>
                      {dateTime(fill.filledAt)}
                      <div className="table-subtext mono">{fill.id}</div>
                    </td>
                    <td className="numeric">{quantity(fill.quantity)}</td>
                    <td className="numeric">{krw(fill.priceKrw)}</td>
                    <td className="numeric">{krw(fill.fundsKrw)}</td>
                    <td className="numeric">{krw(fill.feeKrw)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="확인된 개별 체결 없음"
            description="추천 수량이나 금액으로 체결 내역을 대신하지 않습니다."
          />
        )}
      </div>
      {analysisId && (
        <Link className="text-link" to={`/ai-analysis?analysis=${encodeURIComponent(analysisId)}`}>
          연결된 AI 분석 보기 <ArrowUpRight size={15} aria-hidden="true" />
        </Link>
      )}
    </div>
  );
}

export default function PortfolioPage() {
  const { data } = useDashboardData();
  const [tab, setTab] = useState<PortfolioTab>('holdings');
  const [positionSearch, setPositionSearch] = useState('');
  const [positionSort, setPositionSort] = useState<PositionSort>('value-desc');
  const [positionPage, setPositionPage] = useState(1);
  const [orderSearch, setOrderSearch] = useState('');
  const [orderSide, setOrderSide] = useState('ALL');
  const [orderSort, setOrderSort] = useState('newest');
  const [orderPage, setOrderPage] = useState(1);
  const [pnlStatus, setPnlStatus] = useState('ALL');
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);
  if (!data) return null;

  const { summary, positions, performance, orders, botPnl } = data.portfolio;
  const filteredPositions = selectPositions(positions, positionSearch, positionSort);
  const positionPages = Math.max(1, Math.ceil(filteredPositions.length / PAGE_SIZE));
  const currentPositionPage = Math.min(positionPage, positionPages);
  const visiblePositions = filteredPositions.slice(
    (currentPositionPage - 1) * PAGE_SIZE,
    currentPositionPage * PAGE_SIZE,
  );
  const filteredOrders = orders
    .filter(
      (order) =>
        order.market.toUpperCase().includes(orderSearch.trim().toUpperCase()) &&
        (orderSide === 'ALL' || order.side === orderSide),
    )
    .sort(
      (left, right) =>
        (Date.parse(right.createdAt) - Date.parse(left.createdAt)) *
        (orderSort === 'newest' ? 1 : -1),
    );
  const orderPages = Math.max(1, Math.ceil(filteredOrders.length / PAGE_SIZE));
  const currentOrderPage = Math.min(orderPage, orderPages);
  const visibleOrders = filteredOrders.slice(
    (currentOrderPage - 1) * PAGE_SIZE,
    currentOrderPage * PAGE_SIZE,
  );
  const allocations = allocationGroups(positions);
  const allocatedTotal = allocations.reduce((total, item) => total + item.value, 0);
  const selectedOrder = orders.find((order) => order.id === selectedOrderId);
  const relatedAnalysis = selectedOrder
    ? data.analyses.find(
        (analysis) => analysis.recommendation.id === selectedOrder.recommendationId,
      )
    : undefined;
  const pnlMatches = botPnl.matches.filter(
    (match) => pnlStatus === 'ALL' || match.status === pnlStatus,
  );

  return (
    <div className="page-stack">
      <SectionHeading
        eyebrow="PORTFOLIO & EXECUTION"
        title="자산의 흐름을 읽다"
        description="계좌 전체의 가치와 봇의 실현손익을 구분해 확인하세요."
        action={
          <span className="chip">
            <Wallet size={14} aria-hidden="true" /> UPBIT · KRW 계좌 데모
          </span>
        }
      />
      <div className="grid-metrics grid-metrics-six">
        <MetricCard
          label="총 계좌 평가금액"
          value={krw(summary.totalValueKrw)}
          detail={
            summary.valuationStatus === 'PARTIAL'
              ? '일부 자산 평가 불가 · PARTIAL'
              : `보유 ${summary.heldAssetCount}종 + KRW`
          }
        />
        <MetricCard
          label="보유 자산 평가손익"
          value={krw(summary.unrealizedPnlKrw, true)}
          detail="취득원가 대비 미실현 손익"
        />
        <MetricCard
          label="계좌 수익률"
          value={percent(summary.accountReturnPercent, true)}
          detail="외부 입출금 조정 · Modified Dietz"
        />
        <MetricCard
          label="봇 실현손익"
          value={krw(botPnl.realizedPnlKrw, true)}
          detail="FIFO 원가가 확인된 체결만"
        />
        <MetricCard
          label="기록된 총 체결"
          value={`${botPnl.fillCount}건`}
          detail={`${orders.length}개 주문 · 개별 Fill 기준`}
        />
        <MetricCard
          label="누적 거래 수수료"
          value={krw(botPnl.feesKrw)}
          detail="기록된 체결 수수료 합계"
        />
      </div>
      {summary.valuationStatus === 'PARTIAL' && (
        <div className="notice warning-notice">
          <CircleHelp size={18} aria-hidden="true" />
          <span>
            일부 자산의 평가 정보가 부족합니다. 확인 가능한 가치는 {krw(summary.knownValueKrw)}이며,
            총 평가금액·전체 비중·수익률은 확정하지 않습니다.
          </span>
        </div>
      )}
      <div className="portfolio-charts-grid">
        <PortfolioChart points={performance} asOf={data.asOf} />
        <Panel
          title="자산 구성"
          description={
            summary.valuationStatus === 'PARTIAL'
              ? '가격이 확인된 자산의 구성만 표시'
              : '현금 포함 · 계좌 평가금액 기준'
          }
          action={
            <InfoTip label="계좌 가치와 봇의 거래 원가는 서로 다른 기준입니다. 미평가 자산은 0원으로 가정하지 않습니다." />
          }
        >
          {allocations.length ? (
            <>
              <div
                className="allocation-chart"
                role="img"
                aria-label={`자산 구성: ${allocations.map((item) => `${item.name} ${percent((item.value / allocatedTotal) * 100)}`).join(', ')}`}
              >
                <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                  <PieChart>
                    <Pie
                      data={allocations}
                      dataKey="value"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      innerRadius="66%"
                      outerRadius="88%"
                      paddingAngle={3}
                      stroke="none"
                      isAnimationActive={false}
                    >
                      {allocations.map((entry) => (
                        <Cell key={entry.name} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      formatter={(value) =>
                        krw(typeof value === 'number' || typeof value === 'string' ? value : null)
                      }
                      contentStyle={{
                        background: 'var(--surface-elevated)',
                        border: '1px solid var(--border)',
                        borderRadius: 10,
                        color: 'var(--foreground)',
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
                <div className="donut-center" aria-hidden="true">
                  <span className="text-muted small">
                    {summary.valuationStatus === 'PARTIAL' ? '평가 가능한 자산' : '총 자산'}
                  </span>
                  <strong>
                    {summary.valuationStatus === 'PARTIAL'
                      ? `${positions.filter((position) => position.asset !== 'KRW' && position.valuationStatus === 'PRICED' && position.valueKrw !== null).length} / ${summary.heldAssetCount}`
                      : summary.heldAssetCount}
                    <small> coins</small>
                  </strong>
                  <span className="text-muted small">+ KRW</span>
                </div>
              </div>
              <div className="allocation-legend">
                {allocations.map((item) => (
                  <div className="allocation-row" key={item.name}>
                    <span>
                      <i style={{ background: item.color }} aria-hidden="true" />
                      {item.name}
                    </span>
                    <strong>{percent((item.value / allocatedTotal) * 100)}</strong>
                    <span className="text-muted">{krw(item.value)}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <EmptyState
              title="구성 데이터가 없습니다"
              description="평가 가능한 자산이 확보되면 표시합니다."
            />
          )}
        </Panel>
      </div>
      <Panel
        title="자산과 체결 기록"
        description="동일한 데모 계좌 스냅샷 · 모든 금액은 KRW 기준"
        action={<StatusBadge status={summary.valuationStatus} />}
      >
        <div className="tabs" aria-label="자산 및 거래 보기">
          {(
            [
              { id: 'holdings', label: '보유 자산', count: positions.length },
              { id: 'orders', label: '주문 · 체결', count: orders.length },
              { id: 'pnl', label: 'Bot Trading PnL', count: botPnl.matches.length },
            ] as const
          ).map((item) => (
            <button
              key={item.id}
              type="button"
              className={`tab ${tab === item.id ? 'active' : ''}`}
              aria-pressed={tab === item.id}
              aria-controls={`portfolio-${item.id}`}
              onClick={() => setTab(item.id)}
            >
              {item.label}
              <span className="tab-count">{item.count}</span>
            </button>
          ))}
        </div>
        {tab === 'holdings' && (
          <section id="portfolio-holdings" aria-label="보유 자산">
            <div className="toolbar">
              <label className="search-field">
                <Search size={16} aria-hidden="true" />
                <input
                  className="field"
                  aria-label="보유 자산 검색"
                  placeholder="코인명 또는 마켓 검색"
                  value={positionSearch}
                  onChange={(event) => {
                    setPositionSearch(event.target.value);
                    setPositionPage(1);
                  }}
                />
              </label>
              <select
                className="field"
                aria-label="보유 자산 정렬"
                value={positionSort}
                onChange={(event) => {
                  setPositionSort(event.target.value as PositionSort);
                  setPositionPage(1);
                }}
              >
                <option value="value-desc">평가금액 높은순</option>
                <option value="value-asc">평가금액 낮은순</option>
                <option value="return-desc">수익률 높은순</option>
                <option value="asset">코인명순</option>
              </select>
            </div>
            {visiblePositions.length ? (
              <div className="table-scroll">
                <table className="data-table">
                  <caption className="sr-only">
                    보유 수량, 가격, 평가금액 및 원가 대비 미실현 손익
                  </caption>
                  <thead>
                    <tr>
                      <th>자산</th>
                      <th className="numeric">보유 수량</th>
                      <th className="numeric">평균 매수가</th>
                      <th className="numeric">현재가</th>
                      <th className="numeric">평가금액</th>
                      <th className="numeric">평가손익</th>
                      <th className="numeric">수익률</th>
                      <th className="numeric">비중</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visiblePositions.map((position) => (
                      <tr key={position.id}>
                        <td>
                          <CoinIdentity
                            symbol={position.asset}
                            name={position.name}
                            market={
                              position.market ??
                              (position.asset === 'KRW' ? '현금' : '거래 마켓 없음')
                            }
                          />
                          {position.valuationStatus !== 'PRICED' && (
                            <StatusBadge status={position.valuationStatus} />
                          )}
                        </td>
                        <td className="numeric">{quantity(position.quantity)}</td>
                        <td className="numeric">{krw(position.averageBuyPriceKrw)}</td>
                        <td className="numeric">{krw(position.currentPriceKrw)}</td>
                        <td className="numeric">
                          <strong>{krw(position.valueKrw)}</strong>
                        </td>
                        <td className={`numeric ${financialTone(position.unrealizedPnlKrw)}`}>
                          {krw(position.unrealizedPnlKrw, true)}
                        </td>
                        <td className={`numeric ${financialTone(position.returnPercent)}`}>
                          {percent(position.returnPercent, true)}
                        </td>
                        <td className="numeric">{percent(position.weightPercent)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState
                title="일치하는 보유 자산이 없습니다"
                description="검색어를 변경해 확인해 보세요."
              />
            )}
            <Pagination
              page={currentPositionPage}
              pageCount={positionPages}
              total={filteredPositions.length}
              onPageChange={setPositionPage}
            />
            <p className="text-muted small table-footnote">
              평가손익은 미실현 손익이며 봇 실현손익과 합산하지 않습니다. KRW 현금의 취득원가 기반
              수익률은 표시하지 않습니다.
            </p>
          </section>
        )}
        {tab === 'orders' && (
          <section id="portfolio-orders" aria-label="주문 및 체결">
            <div className="toolbar">
              <label className="search-field">
                <Search size={16} aria-hidden="true" />
                <input
                  className="field"
                  aria-label="체결 마켓 검색"
                  placeholder="체결 마켓 검색"
                  value={orderSearch}
                  onChange={(event) => {
                    setOrderSearch(event.target.value);
                    setOrderPage(1);
                  }}
                />
              </label>
              <select
                className="field"
                aria-label="체결 방향 필터"
                value={orderSide}
                onChange={(event) => {
                  setOrderSide(event.target.value);
                  setOrderPage(1);
                }}
              >
                <option value="ALL">모든 방향</option>
                <option value="BUY">BUY</option>
                <option value="SELL">SELL</option>
              </select>
              <select
                className="field"
                aria-label="체결 정렬"
                value={orderSort}
                onChange={(event) => {
                  setOrderSort(event.target.value);
                  setOrderPage(1);
                }}
              >
                <option value="newest">최신순</option>
                <option value="oldest">오래된순</option>
              </select>
            </div>
            {visibleOrders.length ? (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>주문 시각 · KST</th>
                      <th>마켓</th>
                      <th>방향</th>
                      <th className="numeric">체결 수량</th>
                      <th className="numeric">평균 체결가</th>
                      <th className="numeric">체결 금액</th>
                      <th className="numeric">수수료</th>
                      <th>주문 상태</th>
                      <th>
                        <span className="sr-only">상세보기</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleOrders.map((order) => (
                      <tr key={order.id}>
                        <td>
                          {dateTime(order.createdAt)}
                          <div className="table-subtext mono">{order.id}</div>
                        </td>
                        <td className="mono">{order.market}</td>
                        <td>
                          <StatusBadge status={order.side} />
                        </td>
                        <td className="numeric">{quantity(order.executedQuantity)}</td>
                        <td className="numeric">{krw(order.averagePriceKrw)}</td>
                        <td className="numeric">{krw(order.executedFundsKrw)}</td>
                        <td className="numeric">{krw(order.feeKrw)}</td>
                        <td>
                          <StatusBadge status={order.status} />
                        </td>
                        <td>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`${order.id} 체결 상세`}
                            onClick={() => setSelectedOrderId(order.id)}
                          >
                            <Eye size={16} />
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState
                title="조건에 맞는 체결이 없습니다"
                description="AI 추천과 실제 체결 기록은 별도로 관리합니다."
              />
            )}
            <Pagination
              page={currentOrderPage}
              pageCount={orderPages}
              total={filteredOrders.length}
              onPageChange={setOrderPage}
            />
          </section>
        )}
        {tab === 'pnl' && (
          <section id="portfolio-pnl" aria-label="봇 실현손익">
            <div className="pnl-summary-row">
              <div>
                <span className="stat-label">FIFO로 확인한 봇 실현손익</span>
                <strong className={`pnl-summary-value ${financialTone(botPnl.realizedPnlKrw)}`}>
                  {krw(botPnl.realizedPnlKrw, true)}
                </strong>
                <span className="text-muted small">{dateTime(botPnl.asOf)} KST 기준</span>
              </div>
              <div className="pnl-counts">
                <span>
                  완전 매칭 <strong>{botPnl.fullyMatchedCount}</strong>
                </span>
                <span>
                  부분 매칭 <strong>{botPnl.partiallyMatchedCount}</strong>
                </span>
                <span>
                  매칭 불가 <strong>{botPnl.unmatchedCount}</strong>
                </span>
                <StatusBadge status={botPnl.accountingStatus} />
              </div>
            </div>
            <div className="notice">
              <CircleHelp size={17} aria-hidden="true" />
              <span>
                입증된 매수 원가와 매도 체결을 FIFO로 매칭합니다. 부분 매칭은 확인된 수량만
                인식하며, 원가가 없는 매도는 이익으로 계산하지 않습니다.
              </span>
            </div>
            <div className="toolbar">
              <select
                className="field"
                aria-label="FIFO 매칭 상태"
                value={pnlStatus}
                onChange={(event) => setPnlStatus(event.target.value)}
              >
                <option value="ALL">모든 매칭 상태</option>
                <option value="FULLY_MATCHED">완전 매칭</option>
                <option value="PARTIALLY_MATCHED">부분 매칭</option>
                <option value="UNMATCHED">매칭 불가</option>
              </select>
            </div>
            {pnlMatches.length ? (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>매도 주문</th>
                      <th>매칭 상태</th>
                      <th className="numeric">매도 / 매칭 수량</th>
                      <th className="numeric">인식한 원가</th>
                      <th className="numeric">순매도 대금</th>
                      <th className="numeric">인식한 실현손익</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pnlMatches.map((match) => (
                      <tr key={match.id}>
                        <td>
                          <strong>{match.market}</strong>
                          <div className="table-subtext mono">{match.orderId}</div>
                          <div className="table-subtext">{dateTime(match.soldAt)}</div>
                        </td>
                        <td>
                          <StatusBadge status={match.status} />
                        </td>
                        <td className="numeric">
                          {quantity(match.soldQuantity)}
                          <div className="table-subtext">
                            매칭 {quantity(match.matchedQuantity)}
                          </div>
                        </td>
                        <td className="numeric">{krw(match.costBasisKrw)}</td>
                        <td className="numeric">{krw(match.recognizedProceedsKrw)}</td>
                        <td className={`numeric ${financialTone(match.realizedPnlKrw)}`}>
                          <strong>{krw(match.realizedPnlKrw, true)}</strong>
                          {toNumber(match.realizedPnlKrw) === null && (
                            <div className="table-subtext">원가 미입증</div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState
                title="매칭 내역이 없습니다"
                description="선택한 상태에 해당하는 FIFO 기록이 없습니다."
              />
            )}
          </section>
        )}
      </Panel>
      <DetailDrawer
        open={Boolean(selectedOrderId)}
        onOpenChange={(open) => {
          if (!open) setSelectedOrderId(null);
        }}
        title={selectedOrder ? `${selectedOrder.market} 체결 기록` : '체결 기록 없음'}
        description="Execution & Fill Ledger · Demo / Mock · 조회 전용"
      >
        {selectedOrder ? (
          <ExecutionDetail order={selectedOrder} analysisId={relatedAnalysis?.id} />
        ) : (
          <EmptyState title="연결된 주문을 찾을 수 없습니다" />
        )}
      </DetailDrawer>
    </div>
  );
}
