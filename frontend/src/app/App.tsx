import { Navigate, Route, Routes } from 'react-router';
import { lazy } from 'react';
import { AppShell } from '@/components/layout/AppShell';
import { Link } from 'react-router';
import { ResearchSourceProvider } from '@/hooks/ResearchSourceProvider';
import { OverviewSourceProvider } from '@/hooks/OverviewSourceProvider';
import { AnalysisSourceProvider } from '@/hooks/AnalysisSourceProvider';

const OverviewPage = lazy(() => import('@/pages/overview/OverviewPage'));
const AnalysisPage = lazy(() => import('@/pages/ai-analysis/AnalysisPage'));
const PortfolioPage = lazy(() => import('@/pages/portfolio/PortfolioPage'));
const ResearchPage = lazy(() => import('@/pages/research/ResearchPage'));
const OperationsPage = lazy(() => import('@/pages/operations/OperationsPage'));

export function App() {
  return (
    <ResearchSourceProvider>
      <OverviewSourceProvider>
        <AnalysisSourceProvider>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<Navigate to="/overview" replace />} />
              <Route path="overview" element={<OverviewPage />} />
              <Route path="ai-analysis" element={<AnalysisPage />} />
              <Route path="portfolio" element={<PortfolioPage />} />
              <Route path="research" element={<ResearchPage />} />
              <Route path="operations" element={<OperationsPage />} />
              <Route
                path="*"
                element={
                  <div className="card empty-state">
                    <span className="eyebrow">404 · PAGE NOT FOUND</span>
                    <h1>페이지를 찾을 수 없습니다</h1>
                    <p>주소를 확인하거나 대시보드로 돌아가 주세요.</p>
                    <Link className="text-button" to="/overview">
                      대시보드로 돌아가기 →
                    </Link>
                  </div>
                }
              />
            </Route>
          </Routes>
        </AnalysisSourceProvider>
      </OverviewSourceProvider>
    </ResearchSourceProvider>
  );
}
