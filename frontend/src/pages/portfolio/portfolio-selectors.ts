import { toNumber } from '@/lib/format';
import type { PortfolioPosition } from '@/types/dashboard';

export type PositionSort = 'value-desc' | 'value-asc' | 'return-desc' | 'asset';

export function selectPositions(
  positions: PortfolioPosition[],
  search: string,
  sort: PositionSort,
): PortfolioPosition[] {
  const term = search.trim().toLocaleLowerCase('ko-KR');
  return positions
    .filter((position) =>
      `${position.asset} ${position.name} ${position.market ?? ''}`
        .toLocaleLowerCase('ko-KR')
        .includes(term),
    )
    .sort((left, right) => {
      if (sort === 'asset') return left.asset.localeCompare(right.asset);
      const leftValue = sort === 'return-desc' ? left.returnPercent : toNumber(left.valueKrw);
      const rightValue = sort === 'return-desc' ? right.returnPercent : toNumber(right.valueKrw);
      // Unpriced positions remain visible at the end, never treated as zero-valued.
      if (leftValue === null) return rightValue === null ? 0 : 1;
      if (rightValue === null) return -1;
      return sort === 'value-asc' ? leftValue - rightValue : rightValue - leftValue;
    });
}

export function allocationGroups(positions: PortfolioPosition[]) {
  const groups = new Map<string, number>();
  for (const position of positions) {
    const value = toNumber(position.valueKrw);
    if (value === null || position.valuationStatus !== 'PRICED') continue;
    const name = ['BTC', 'ETH', 'KRW'].includes(position.asset) ? position.asset : '기타 코인';
    groups.set(name, (groups.get(name) ?? 0) + value);
  }
  return ['BTC', 'ETH', '기타 코인', 'KRW']
    .map((name, index) => ({
      name,
      value: groups.get(name) ?? 0,
      color: `var(--chart-${index + 1})`,
    }))
    .filter((item) => item.value > 0);
}

export function financialTone(value: string | number | null) {
  const number = toNumber(value);
  return number === null || number === 0 ? '' : number > 0 ? 'positive' : 'negative';
}
