import { ApiSessionBoundary } from '@/components/auth/ApiSessionBoundary';
import { lazy, Suspense } from 'react';
import { ErrorState, LoadingSkeleton } from '@/components/common';
import { useOverviewSource } from '@/hooks/use-overview-source';
import { useDashboardData } from '@/hooks/use-dashboard-data';
import OverviewDemoView from './OverviewDemoView';
import './overview.css';
const OverviewApiView = lazy(() => import('./OverviewApiView'));
export default function OverviewPage() {
  const { source } = useOverviewSource();
  const demo = useDashboardData(source === 'demo');
  return (
    <div className="page-stack">
      {source === 'api' ? (
        <Suspense fallback={<LoadingSkeleton />}>
          <ApiSessionBoundary>
            <OverviewApiView />
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
        <OverviewDemoView />
      )}
    </div>
  );
}
