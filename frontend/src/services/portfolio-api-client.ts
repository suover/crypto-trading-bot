import { parseOrderPage, parseOrderFillPage } from '@/lib/analysis-api-parser';
import { sameScope } from '@/lib/overview-api-parser';
import { validId } from '@/lib/research-api-parser';
import type { Page, Scope } from '@/types/overview-api';
import { OverviewApiError, readAccountApi } from './overview-api-client';

const pageSize = 20;
function pageQuery(page: number) {
  if (!Number.isSafeInteger(page) || page < 0 || page * pageSize > 1_000_000)
    throw new OverviewApiError('input');
  return `page=${page}&size=${pageSize}`;
}
function checkedPage<T extends Page<unknown>>(data: T, page: number) {
  if (data.size !== pageSize || data.page !== page) throw new OverviewApiError('data');
  return data;
}

export const portfolioApi = {
  orders(page: number, signal?: AbortSignal) {
    return readAccountApi(
      `/api/v1/portfolio/orders?${pageQuery(page)}`,
      (raw) => checkedPage(parseOrderPage(raw), page),
      signal,
    );
  },
  fills(orderId: string, scope: Scope, page: number, signal?: AbortSignal) {
    if (!validId(orderId)) throw new OverviewApiError('input');
    return readAccountApi(
      `/api/v1/portfolio/orders/${orderId}/fills?${pageQuery(page)}`,
      (raw) => {
        const data = checkedPage(parseOrderFillPage(raw), page);
        if (data.orderId !== orderId || !sameScope(data.scope, scope))
          throw new OverviewApiError('consistency');
        return data;
      },
      signal,
    );
  },
};
