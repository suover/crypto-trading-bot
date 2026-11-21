import { EmptyState, StatusBadge } from '@/components/common';
import { decimalText, moneyText, percentText, valueTone } from '@/lib/overview-format';
import type { Positions } from '@/types/overview-api';
import { Source, Time } from './api-components';

export function StoredPositions({ data }: { data: Positions }) {
  return (
    <>
      <Source data={data} />
      {!data.portfolio ? (
        <EmptyState title="계좌 snapshot이 없습니다" />
      ) : (
        <>
          <p className="small text-muted">
            Snapshot {data.portfolio.snapshotId} · 관측 <Time value={data.portfolio.capturedAt} />
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
                    <th className="numeric">평가 기준 가격</th>
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
                        <span className="table-subtext">{p.market ?? '거래쌍 미기록'}</span>
                      </td>
                      <td>
                        <StatusBadge status={p.valuationStatus} />
                        <span className="table-subtext">{p.priceSource ?? '출처 미기록'}</span>
                      </td>
                      <td className="numeric">
                        {decimalText(p.totalQuantity)}
                        <span className="table-subtext">
                          {decimalText(p.availableQuantity)} / {decimalText(p.lockedQuantity)}
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
        평가 불가·명시적 제외 자산도 남겨 표시합니다. 누락된 가격이나 원가는 0으로 대체하지
        않습니다. 계좌 비중은 재계산하지 않습니다.
      </p>
    </>
  );
}
