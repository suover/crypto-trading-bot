import type { ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { dateTime } from '@/lib/format';
import { sameScope } from '@/lib/overview-api-parser';
import { OverviewApiError } from '@/services/overview-api-client';
import type { Envelope, Page, Scope } from '@/types/overview-api';

export function Time({ value }: { value: string | null }) {
  return value === null ? (
    <>미기록</>
  ) : (
    <time dateTime={value} title={value}>
      {dateTime(value)} KST
    </time>
  );
}
export function Source({ data }: { data: Envelope }) {
  return (
    <p className="overview-source small text-muted">
      User {data.scope.userId} · {data.scope.exchange} / {data.scope.quoteAsset} · 응답 생성{' '}
      <Time value={data.generatedAt} />
    </p>
  );
}
export function ApiError({ error, retry }: { error: Error; retry: () => void }) {
  const e = error instanceof OverviewApiError ? error : new OverviewApiError('data');
  const message =
    e.status === 401
      ? '관리자 인증이 필요합니다'
      : e.status === 403
        ? '접근이 거부되었습니다. 관리자 권한과 사용자 범위를 확인하세요.'
        : e.status === 400 || e.kind === 'input'
          ? '입력 조건을 확인해 주세요'
          : e.kind === 'consistency'
            ? '조회 기준이 변경되었습니다. 계좌 데이터를 새로고침하세요.'
            : e.status
              ? '서버 오류로 데이터를 조회하지 못했습니다'
              : e.kind === 'network'
                ? 'API 연결에 실패했습니다'
                : '잘못된 API 데이터입니다. 응답 계약을 확인해 주세요.';
  return (
    <div className="empty-state" role="alert">
      <h3>{message}</h3>
      <p>실패한 조회를 DEMO나 0으로 대체하지 않습니다.</p>
      <Button variant="outline" onClick={retry}>
        다시 시도
      </Button>
    </div>
  );
}
export function QueryContent<T extends Envelope>({
  query,
  expectedScope,
  children,
  retry,
}: {
  query: UseQueryResult<T, Error>;
  expectedScope?: Scope;
  children: (data: T) => ReactNode;
  retry?: () => void;
}) {
  const refetch =
    retry ??
    (() => {
      void query.refetch();
    });
  if (query.isFetching || query.isPending)
    return (
      <div className="empty-state" role="status">
        API 데이터 조회 중…
      </div>
    );
  if (query.isError) return <ApiError error={query.error} retry={refetch} />;
  if (expectedScope && !sameScope(expectedScope, query.data.scope))
    return <ApiError error={new OverviewApiError('consistency')} retry={refetch} />;
  return <>{children(query.data)}</>;
}
export function Pages({
  data,
  label,
  change,
}: {
  data: Page<unknown>;
  label: string;
  change: (page: number) => void;
}) {
  const offsetLimit = (data.page + 1) * data.size > 1_000_000;
  return (
    <nav className="pagination overview-controls" aria-label={label + ' 페이지 이동'}>
      <span>
        총 {BigInt(data.totalElements).toLocaleString('ko-KR')}건 ·{' '}
        {data.totalPages === '0' ? 0 : data.page + 1} /{' '}
        {BigInt(data.totalPages).toLocaleString('ko-KR')} 페이지
      </span>
      <div className="overview-controls">
        <Button
          variant="outline"
          aria-label={'이전 ' + label + ' 페이지'}
          disabled={data.page === 0}
          onClick={() => change(data.page - 1)}
        >
          이전
        </Button>
        <Button
          variant="outline"
          aria-label={'다음 ' + label + ' 페이지'}
          disabled={BigInt(data.page) + 1n >= BigInt(data.totalPages) || offsetLimit}
          onClick={() => change(data.page + 1)}
        >
          다음
        </Button>
      </div>
      {offsetLimit && <span>조회 상한입니다. 기간을 좁혀 주세요.</span>}
    </nav>
  );
}
