import { lazy, Suspense } from 'react';
import { Button } from '@/components/ui/button';
import { ErrorState, LoadingSkeleton } from '@/components/common';
import { useResearchSource } from '@/hooks/use-research-source';
import { useDashboardData } from '@/hooks/use-dashboard-data';
import ResearchDemoView from './ResearchDemoView';
import './research.css';

const ResearchApiView = lazy(() => import('./ResearchApiView'));

export default function ResearchPage() {
  const { source, setSource, available } = useResearchSource();
  const demo = useDashboardData(source === 'demo');
  return (
    <div className="page-stack">
      {available && (
        <div className="research-source-switch" role="group" aria-label="연구 데이터 출처">
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
          <span className="text-muted small">조회 전용 · 모드는 저장되지 않습니다</span>
        </div>
      )}
      {source === 'api' ? (
        <Suspense fallback={<LoadingSkeleton />}>
          <ResearchApiView />
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
        <ResearchDemoView />
      )}
    </div>
  );
}
