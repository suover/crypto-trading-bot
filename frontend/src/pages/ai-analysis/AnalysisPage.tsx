import { ApiSessionBoundary } from '@/components/auth/ApiSessionBoundary';
import { lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router';
import { Button } from '@/components/ui/button';
import { ErrorState, LoadingSkeleton } from '@/components/common';
import { useAnalysisSource } from '@/hooks/use-analysis-source';
import { useDashboardData } from '@/hooks/use-dashboard-data';
import AnalysisDemoView from './AnalysisDemoView';
import './analysis.css';
const AnalysisApiView = lazy(() => import('./AnalysisApiView'));
export default function AnalysisPage() {
  const { source, setSource, available } = useAnalysisSource();
  const demo = useDashboardData(source === 'demo');
  const [params] = useSearchParams();
  return (
    <div className="page-stack analysis-api">
      {available && (
        <div className="overview-controls" role="group" aria-label="AI 분석 데이터 출처">
          <Button
            variant={source === 'demo' ? 'default' : 'outline'}
            aria-pressed={source === 'demo'}
            onClick={() => setSource('demo')}
          >
            DEMO 화면
          </Button>
          <Button
            variant={source === 'api' ? 'default' : 'outline'}
            aria-pressed={source === 'api'}
            onClick={() => setSource('api')}
          >
            LOCAL API · READ ONLY
          </Button>
          <span className="small text-muted">조회 전용 · 모드는 저장되지 않습니다</span>
        </div>
      )}
      {source === 'demo' && params.has('recommendation') && (
        <p className="notice">
          실제 추천 상세 링크입니다.{' '}
          {available
            ? 'LOCAL API를 선택하면 조회합니다.'
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
