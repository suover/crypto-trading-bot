import { ApiSessionBoundary } from '@/components/auth/ApiSessionBoundary';
import { lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router';
import { ErrorState, LoadingSkeleton } from '@/components/common';
import { useAnalysisSource } from '@/hooks/use-analysis-source';
import { useDashboardData } from '@/hooks/use-dashboard-data';
import AnalysisDemoView from './AnalysisDemoView';
import './analysis.css';
const AnalysisApiView = lazy(() => import('./AnalysisApiView'));
export default function AnalysisPage() {
  const { source, available } = useAnalysisSource();
  const demo = useDashboardData(source === 'demo');
  const [params] = useSearchParams();
  return (
    <div className="page-stack analysis-api">
      {source === 'demo' && params.has('recommendation') && (
        <p className="notice">
          실제 추천 상세 링크입니다.{' '}
          {available
            ? '로그인하면 연결된 사용자의 추천을 조회합니다.'
            : '로컬 API 연결이 활성화되지 않았습니다.'}{' '}
          DEMO 상세로 대체하지 않습니다.
        </p>
      )}
      {source === 'api' ? (
        <Suspense fallback={<LoadingSkeleton />}>
          <ApiSessionBoundary>
            <AnalysisApiView />
          </ApiSessionBoundary>
        </Suspense>
      ) : demo.isPending ? (
        <LoadingSkeleton />
      ) : demo.isError ? (
        <ErrorState
          onRetry={() => {
            void demo.refetch();
          }}
        />
      ) : (
        <AnalysisDemoView />
      )}
    </div>
  );
}
