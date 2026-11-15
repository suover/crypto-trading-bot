import { ApiSessionBoundary } from '@/components/auth/ApiSessionBoundary';
import { lazy, Suspense } from 'react';
import { ErrorState, LoadingSkeleton } from '@/components/common';
import { useResearchSource } from '@/hooks/use-research-source';
import { useDashboardData } from '@/hooks/use-dashboard-data';
import ResearchDemoView from './ResearchDemoView';
import './research.css';

const ResearchApiView = lazy(() => import('./ResearchApiView'));

export default function ResearchPage() {
  const { source } = useResearchSource();
  const demo = useDashboardData(source === 'demo');
  return (
    <div className="page-stack">
      {source === 'api' ? (
        <Suspense fallback={<LoadingSkeleton />}>
          <ApiSessionBoundary>
            <ResearchApiView />
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
        <ResearchDemoView />
      )}
    </div>
  );
}
