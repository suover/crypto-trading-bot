import { lazy, Suspense } from 'react';
import { Button } from '@/components/ui/button';
import { ErrorState, LoadingSkeleton } from '@/components/common';
import { useOverviewSource } from '@/hooks/use-overview-source';
import { useDashboardData } from '@/hooks/use-dashboard-data';
import OverviewDemoView from './OverviewDemoView';
import './overview.css';
const OverviewApiView = lazy(() => import('./OverviewApiView'));
export default function OverviewPage() {
  const { source, setSource, available } = useOverviewSource();
  const demo = useDashboardData(source === 'demo');
  return (
    <div className="page-stack">
      {available && (
        <div className="overview-controls" role="group" aria-label="대시보드 데이터 출처">
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
      {source === 'api' ? (
        <Suspense fallback={<LoadingSkeleton />}>
          <OverviewApiView />
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
        <OverviewDemoView />
      )}
    </div>
  );
}
