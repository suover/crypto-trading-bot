import type { ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { AnalysisApiError, analysisAuthenticationPath } from '@/services/analysis-api-client';
export function AnalysisError({ error, retry }: { error: Error; retry: () => void }) {
  const e = error instanceof AnalysisApiError ? error : new AnalysisApiError('data');
  const message =
    e.status === 401
      ? '관리자 인증이 필요합니다'
      : e.status === 403
        ? '접근이 거부되었습니다'
        : e.status === 404
          ? '추천을 찾을 수 없거나 조회 범위 밖입니다'
          : e.kind === 'input' || e.status === 400
            ? '입력 조건을 확인해 주세요'
            : e.kind === 'consistency'
              ? '조회 데이터 연결이 일치하지 않습니다. 상세를 다시 조회하세요.'
              : e.kind === 'data'
                ? '잘못된 API 데이터입니다'
                : 'API 데이터를 조회하지 못했습니다';
  return (
    <div role="alert" className="empty-state">
      <h3>{message}</h3>
      <p>실패한 조회를 DEMO나 0으로 대체하지 않습니다.</p>
      {e.status === 401 && (
        <a
          className="text-button"
          href={analysisAuthenticationPath}
          target="_blank"
          rel="noopener noreferrer"
        >
          새 탭에서 관리자 인증
        </a>
      )}
      <Button variant="outline" onClick={retry}>
        다시 시도
      </Button>
    </div>
  );
}
export function AnalysisQuery<T>({
  query,
  children,
}: {
  query: UseQueryResult<T, Error>;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending || query.isFetching)
    return (
      <div role="status" className="empty-state">
        API 데이터 조회 중…
      </div>
    );
  if (query.isError)
    return (
      <AnalysisError
        error={query.error}
        retry={() => {
          void query.refetch();
        }}
      />
    );
  return <>{children(query.data)}</>;
}
