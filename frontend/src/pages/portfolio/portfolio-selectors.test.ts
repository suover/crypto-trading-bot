import { describe, expect, it } from 'vitest';
import { dashboardData } from '@/data/mock/dashboard';
import type { PortfolioPosition } from '@/types/dashboard';
import { allocationGroups, financialTone, selectPositions } from './portfolio-selectors';

const unpriced: PortfolioPosition = {
  id: 'position-unpriced',
  market: null,
  asset: 'TEST',
  name: '평가 불가 예제',
  quantity: '1',
  averageBuyPriceKrw: null,
  currentPriceKrw: null,
  valueKrw: null,
  costBasisKrw: null,
  unrealizedPnlKrw: null,
  returnPercent: null,
  weightPercent: null,
  valuationStatus: 'UNPRICED',
  priceSource: 'UNAVAILABLE',
};

describe('portfolio display selectors', () => {
  it('searches both Korean asset names and KRW market names', () => {
    expect(
      selectPositions(dashboardData.portfolio.positions, '이더리움', 'asset').map(
        (position) => position.asset,
      ),
    ).toEqual(['ETH']);
    expect(
      selectPositions(dashboardData.portfolio.positions, 'krw-btc', 'asset').map(
        (position) => position.asset,
      ),
    ).toEqual(['BTC']);
  });

  it.each(['value-desc', 'value-asc', 'return-desc'] as const)(
    'places null valuations last for %s without changing their value',
    (sort) => {
      const positions = [unpriced, ...dashboardData.portfolio.positions];
      const result = selectPositions(positions, '', sort);
      expect(result.find((position) => position.id === unpriced.id)?.valueKrw).toBeNull();
      const pricedIndex = result.findIndex((position) => position.asset === 'BTC');
      expect(result.findIndex((position) => position.id === unpriced.id)).toBeGreaterThan(
        pricedIndex,
      );
      expect(positions[0]).toBe(unpriced);
    },
  );

  it('groups the chart to the same known total as the holdings table', () => {
    const groups = allocationGroups(dashboardData.portfolio.positions);
    expect(groups.map((group) => group.name)).toEqual(['BTC', 'ETH', '기타 코인', 'KRW']);
    expect(groups.reduce((sum, group) => sum + group.value, 0)).toBe(
      Number(dashboardData.portfolio.summary.totalValueKrw),
    );
  });

  it('does not turn an unpriced holding into a zero-valued allocation', () => {
    expect(allocationGroups([unpriced])).toEqual([]);
    expect(allocationGroups([...dashboardData.portfolio.positions, unpriced])).toEqual(
      allocationGroups(dashboardData.portfolio.positions),
    );
  });

  it('distinguishes financial null, zero, positive and negative values', () => {
    expect(financialTone(null)).toBe('');
    expect(financialTone('0')).toBe('');
    expect(financialTone('1')).toBe('positive');
    expect(financialTone('-1')).toBe('negative');
  });
});
