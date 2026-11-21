import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { DetailDrawer, EmptyState, Panel, SectionHeading, StatusBadge } from '@/components/common';
import { Button } from '@/components/ui/button';
import { accountReadOptions } from '@/hooks/use-overview-api';
import { decimalText, moneyText } from '@/lib/overview-format';
import { Pages, QueryContent, Time } from '@/pages/overview/api-components';
import { StoredPositions } from '@/pages/overview/StoredPositions';
import { overviewApi } from '@/services/overview-api-client';
import { portfolioApi } from '@/services/portfolio-api-client';
import type { Scope } from '@/types/overview-api';
import type { Order } from '@/types/analysis-api';
import '@/pages/overview/overview.css';

function OrderDetail({ order, scope }: { order: Order; scope: Scope }) {
  const [page, setPage] = useState(0);
  const fills = useQuery({
    ...accountReadOptions,
    queryKey: ['portfolio-api', 'fills', scope, order.id, page],
    queryFn: ({ signal }) => portfolioApi.fills(order.id, scope, page, signal),
  });
  return (
    <div className="stack">
      <p>
        {order.market} · {order.side} · {order.tradingMode} · <StatusBadge status={order.status} />
      </p>
      <dl className="detail-grid">
        {[
          ['주문 기록 금액', moneyText(order.amountKrw)],
          ['주문 기록 수량', decimalText(order.quantity)],
          ['체결 수량', decimalText(order.executedQuantity)],
          ['체결 금액', moneyText(order.executedFundsKrw)],
          [`평균 체결가 · ${scope.quoteAsset}`, decimalText(order.averageExecutionPrice)],
          [`지불 수수료 · ${scope.quoteAsset}`, decimalText(order.paidFee)],
          ['미체결 잔량', decimalText(order.remainingQuantity)],
          ['체결 건수', order.tradesCount ?? '미기록'],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="stat-label">{label}</dt>
            <dd className="stat-value mono">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="small text-muted">
        체결 동기화 시각 · <Time value={order.executionSyncedAt} />
      </p>
      <h3>개별 체결 내역</h3>
      <QueryContent query={fills}>
        {(data) => (
          <>
            {data.items.length > 0 ? (
              <div className="table-scroll">
                <table className="data-table">
                  <caption className="sr-only">개별 체결 내역</caption>
                  <thead>
                    <tr>
                      <th>원장 기록 시각 · KST</th>
                      <th className="numeric">체결 수량</th>
                      <th className="numeric">체결가 · {scope.quoteAsset}</th>
                      <th className="numeric">체결 금액</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((fill) => (
                      <tr key={fill.id}>
                        <td>
                          <Time value={fill.recordedAt} />
                        </td>
                        <td className="numeric">{decimalText(fill.quantity)}</td>
                        <td className="numeric">{decimalText(fill.price)}</td>
                        <td className="numeric">{moneyText(fill.fundsKrw)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="저장된 개별 체결 기록이 없습니다" />
            )}
            <Pages data={data} label="체결" change={setPage} />
          </>
        )}
      </QueryContent>
    </div>
  );
}

function OrderHistory({ expectedScope }: { expectedScope?: Scope }) {
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const query = useQuery({
    ...accountReadOptions,
    queryKey: ['portfolio-api', 'orders', page],
    queryFn: ({ signal }) => portfolioApi.orders(page, signal),
  });
  return (
    <Panel
      title="봇 주문 이력"
      description="주문 기록 시각순 · LIVE 실거래와 MOCK 모의거래를 구분합니다."
    >
      <QueryContent query={query} expectedScope={expectedScope}>
        {(data) => {
          const selected = data.items.find((order) => order.id === selectedId);
          return (
            <>
              {data.items.length > 0 ? (
                <div className="table-scroll">
                  <table className="data-table">
                    <caption className="sr-only">봇 주문 이력</caption>
                    <thead>
                      <tr>
                        <th>주문 기록 시각 · KST</th>
                        <th>마켓 / 거래 모드</th>
                        <th>방향</th>
                        <th>주문 상태</th>
                        <th className="numeric">체결 수량</th>
                        <th className="numeric">체결 금액</th>
                        <th>
                          <span className="sr-only">체결 상세</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((order) => (
                        <tr key={order.id}>
                          <td>
                            <Time value={order.createdAt} />
                          </td>
                          <td>
                            {order.market}
                            <span className="table-subtext">{order.tradingMode}</span>
                          </td>
                          <td>
                            <StatusBadge status={order.side} />
                          </td>
                          <td>
                            <StatusBadge status={order.status} />
                          </td>
                          <td className="numeric">{decimalText(order.executedQuantity)}</td>
                          <td className="numeric">{moneyText(order.executedFundsKrw)}</td>
                          <td>
                            <Button
                              variant="ghost"
                              aria-label={`주문 ${order.id} 체결 상세`}
                              onClick={() => setSelectedId(order.id)}
                            >
                              상세
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState title="저장된 봇 주문이 없습니다" />
              )}
              <Pages data={data} label="주문" change={setPage} />
              {selected && (
                <DetailDrawer
                  open
                  onOpenChange={(open) => {
                    if (!open) setSelectedId(null);
                  }}
                  title={`주문 ${selected.id} 체결 상세`}
                  description="주문 원장에 저장된 체결 요약과 개별 기록입니다."
                  sourceLabel="API · READ ONLY"
                >
                  <OrderDetail key={selected.id} order={selected} scope={data.scope} />
                </DetailDrawer>
              )}
            </>
          );
        }}
      </QueryContent>
    </Panel>
  );
}

export default function PortfolioApiView() {
  const [tab, setTab] = useState<'holdings' | 'orders'>('holdings');
  const positions = useQuery({
    ...accountReadOptions,
    queryKey: ['portfolio-api', 'positions'],
    queryFn: ({ signal }) => overviewApi.latestPositions(signal),
    enabled: tab === 'holdings',
  });
  return (
    <div className="page-stack overview-api">
      <SectionHeading title="자산 및 거래" />
      <div className="tabs" aria-label="자산 및 거래 보기">
        <button
          type="button"
          className={`tab ${tab === 'holdings' ? 'active' : ''}`}
          aria-pressed={tab === 'holdings'}
          onClick={() => setTab('holdings')}
        >
          보유 자산
        </button>
        <button
          type="button"
          className={`tab ${tab === 'orders' ? 'active' : ''}`}
          aria-pressed={tab === 'orders'}
          onClick={() => setTab('orders')}
        >
          주문·체결 이력
        </button>
      </div>
      {tab === 'holdings' ? (
        <Panel title="보유 자산" description="최근 계좌 평가 시점에 기록된 보유 수량과 가격입니다.">
          <QueryContent query={positions}>
            {(data) => (
              <>
                {data.portfolio && (
                  <dl className="overview-provenance">
                    <div>
                      <dt>총 계좌 평가금액</dt>
                      <dd>
                        {moneyText(data.portfolio.totalValueKrw)} ·{' '}
                        <StatusBadge status={data.portfolio.valuationStatus} />
                      </dd>
                    </div>
                    <div>
                      <dt>보유 현금 · KRW</dt>
                      <dd>{moneyText(data.portfolio.cashTotalKrw)}</dd>
                    </div>
                  </dl>
                )}
                <StoredPositions data={data} />
              </>
            )}
          </QueryContent>
        </Panel>
      ) : (
        <OrderHistory expectedScope={positions.data?.scope} />
      )}
    </div>
  );
}
