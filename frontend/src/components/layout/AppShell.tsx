import { useAdminSession } from '@/hooks/use-admin-session';
import { ApiSessionBoundary } from '@/components/auth/ApiSessionBoundary';
import { Suspense, useEffect, useState } from 'react';
import {
  Activity,
  ArrowUpRight,
  Bell,
  ChartNoAxesCombined,
  ChevronRight,
  FlaskConical,
  LayoutDashboard,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
  Sparkles,
  Sun,
  Wallet,
} from 'lucide-react';
import { Link, NavLink, Outlet, useLocation, useMatch } from 'react-router';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { ErrorState, LoadingSkeleton } from '@/components/common';
import { useDashboardData, useDemoScenario } from '@/hooks/use-dashboard-data';
import { DEMO_AS_OF } from '@/data/mock/dashboard';
import { dateTime } from '@/lib/format';
import type { DemoScenario } from '@/types/dashboard';
import { useResearchSource } from '@/hooks/use-research-source';
import { useOverviewSource } from '@/hooks/use-overview-source';
import { useAnalysisSource } from '@/hooks/use-analysis-source';

const navigation = [
  { to: '/overview', label: '대시보드', icon: LayoutDashboard },
  { to: '/ai-analysis', label: 'AI 매매 분석', icon: Sparkles },
  { to: '/portfolio', label: '자산 및 거래', icon: Wallet },
  { to: '/research', label: '전략 연구', icon: FlaskConical },
  { to: '/operations', label: '운영 모니터링', icon: Activity },
];

function Brand({ onSelect }: { onSelect?: () => void }) {
  return (
    <Link
      to="/overview"
      className="brand"
      aria-label="Crypto Trading Bot 대시보드"
      onClick={onSelect}
    >
      <span className="brand-mark">
        <ChartNoAxesCombined size={21} strokeWidth={1.7} />
      </span>
      <span className="brand-text">
        <span className="brand-name">Crypto Trading Bot</span>
        <span className="brand-subtitle">RESEARCH & TRADE</span>
      </span>
    </Link>
  );
}
function Navigation({ onSelect }: { onSelect?: () => void }) {
  return (
    <nav aria-label="주요 메뉴">
      {navigation.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          title={label}
          onClick={onSelect}
          className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
        >
          <Icon size={18} strokeWidth={1.7} />
          <span>{label}</span>
          {to === '/research' && <span className="nav-number">LAB</span>}
        </NavLink>
      ))}
    </nav>
  );
}

export function AppShell() {
  const location = useLocation();
  // Match with the router's own decoding and trailing-slash rules.
  const pageMatch = useMatch('/:page');
  const pathname = pageMatch?.params.page
    ? '/' + pageMatch.params.page.toLowerCase()
    : location.pathname;
  const session = useAdminSession();
  const pageTitle = navigation.find((item) => item.to === pathname)?.label ?? '페이지 안내';
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try {
      return localStorage.getItem('ctb-dashboard-theme') === 'light' ? 'light' : 'dark';
    } catch {
      return 'dark';
    }
  });
  const { scenario, setScenario } = useDemoScenario();
  const { source } = useResearchSource();
  const overview = useOverviewSource();
  const analysis = useAnalysisSource();
  const isAnalysis = pathname === '/ai-analysis';
  const isResearch = pathname === '/research';
  const isOverview = pathname === '/overview';
  const isApi =
    (isResearch && source === 'api') ||
    (isOverview && overview.source === 'api') ||
    (isAnalysis && analysis.source === 'api');
  const apiName = isAnalysis ? 'AI Analysis' : isOverview ? 'Overview' : 'Research';
  const isDemo = !session.available || session.status === 'guest';
  const isAuthenticated = session.status === 'authenticated';
  const sourceLabel = isDemo
    ? 'DEMO'
    : isApi
      ? 'LOCAL API · READ ONLY'
      : isAuthenticated
        ? '실제 데이터 미연동'
        : '인증 상태 확인';
  const sourceDescription = isDemo
    ? '현재 화면의 데이터는 Mock입니다.'
    : isApi
      ? `현재 ${apiName}는 로그인한 사용자 데이터입니다.`
      : isAuthenticated
        ? '이 화면의 실제 데이터 조회는 연결되지 않았습니다.'
        : '인증 상태를 확인한 뒤 데이터를 표시합니다.';
  const query = useDashboardData(isDemo);
  useEffect(() => {
    document.documentElement.classList.toggle('light', theme === 'light');
    document.documentElement.classList.toggle('dark', theme === 'dark');
    try {
      localStorage.setItem('ctb-dashboard-theme', theme);
    } catch {
      /* Theme still works when storage is unavailable. */
    }
  }, [theme]);
  useEffect(() => {
    document.title = `${pageTitle} · Crypto Trading Bot`;
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [pageTitle]);
  const isUnknownRoute = !navigation.some((item) => item.to === pathname) && pathname !== '/';
  return (
    <div className={`app-shell${collapsed ? ' is-collapsed' : ''}`}>
      <a className="skip-link" href="#main-content">
        본문으로 건너뛰기
      </a>
      <aside className="sidebar">
        <Brand />
        <div className="nav-label">WORKSPACE</div>
        <Navigation />
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <ShieldCheck size={20} />
            <strong>{isDemo ? '데모 워크스페이스' : '내 워크스페이스'}</strong>
            <p>
              {sourceDescription}
              <br />
              거래·승인·정책 변경 없이
              <br />
              안전하게 둘러보세요.
            </p>
          </div>
          <div className="sidebar-footer">
            <span>FOUNDATION v0.1.0</span>
            <Button
              variant="ghost"
              size="icon"
              aria-label={collapsed ? '사이드바 펼치기' : '사이드바 접기'}
              onClick={() => setCollapsed(!collapsed)}
            >
              {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </Button>
          </div>
        </div>
      </aside>
      <div className="app-body">
        <header className={`app-header${session.available ? ' has-admin-session' : ''}`}>
          <div className="header-breadcrumb">
            <Dialog open={mobileOpen} onOpenChange={setMobileOpen}>
              <DialogTrigger asChild>
                <Button
                  className="mobile-menu-trigger"
                  variant="ghost"
                  size="icon"
                  aria-label="메뉴 열기"
                >
                  <Menu size={20} />
                </Button>
              </DialogTrigger>
              <DialogContent className="mobile-navigation">
                <DialogTitle className="sr-only">주요 메뉴</DialogTitle>
                <DialogDescription className="sr-only">조회 전용 대시보드 메뉴</DialogDescription>
                <Brand onSelect={() => setMobileOpen(false)} />
                <Navigation onSelect={() => setMobileOpen(false)} />
                <p className="text-muted small" style={{ marginTop: 'auto' }}>
                  {sourceLabel} · 조회 전용
                </p>
              </DialogContent>
            </Dialog>
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{pageTitle}</strong>
          </div>
          <div className="header-actions">
            {session.available &&
              (session.status === 'authenticated' ? (
                <>
                  <span className="small session-username" title={session.username ?? undefined}>
                    {session.username}
                  </span>
                  <Button
                    variant="outline"
                    onClick={() => {
                      void session.logout();
                    }}
                  >
                    로그아웃
                  </Button>
                </>
              ) : (
                <Button variant="outline" asChild>
                  <Link to="/login" state={{ returnTo: location.pathname + location.search }}>
                    관리자 로그인
                  </Link>
                </Button>
              ))}
            <span className="header-timestamp">
              {isDemo ? dateTime(DEMO_AS_OF) + ' KST 기준' : '관측·응답 시각은 각 조회 영역에 표시'}
            </span>
            <span className="demo-pill">
              <span className="status-dot" />
              {sourceLabel}
            </span>
            <span className="header-divider" />
            <Button
              variant="ghost"
              size="icon"
              aria-label={theme === 'dark' ? '라이트 모드로 전환' : '다크 모드로 전환'}
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            >
              {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
            </Button>
            <Button variant="ghost" size="icon" asChild>
              <Link to="/operations" aria-label={isDemo ? '데모 운영 알림 보기' : '운영 화면 보기'}>
                <Bell size={17} />
              </Link>
            </Button>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          <Suspense fallback={<LoadingSkeleton />}>
            <ApiSessionBoundary allowGuest>
              {isAuthenticated && !isApi && !isUnknownRoute && pathname !== '/' ? (
                <section className="card empty-state">
                  <h1>{pageTitle}</h1>
                  <h2>실제 데이터 미연동</h2>
                  <p>이 화면은 아직 실제 데이터 조회가 연결되지 않았습니다.</p>
                  <p>데모 데이터는 로그아웃 후 둘러볼 수 있습니다.</p>
                </section>
              ) : pathname === '/' || isUnknownRoute || isResearch || isOverview || isAnalysis ? (
                <Outlet />
              ) : query.isPending ? (
                <LoadingSkeleton />
              ) : query.isError ? (
                <ErrorState
                  onRetry={() => {
                    void query.refetch();
                  }}
                />
              ) : (
                <Outlet />
              )}
            </ApiSessionBoundary>
          </Suspense>
          <footer className="app-footer">
            <span>
              Crypto Trading Bot <span aria-hidden="true">/</span>{' '}
              {isDemo
                ? '모든 수치는 데모입니다. 실제 운영 데이터와 연결되지 않습니다.'
                : `${sourceDescription} 거래·승인·활성화는 실행하지 않습니다.`}
            </span>
            {isDemo && (
              <label className="demo-controls">
                데모 상태
                <select
                  className="field"
                  aria-label="데모 데이터 상태"
                  value={scenario}
                  onChange={(event) => setScenario(event.target.value as DemoScenario)}
                >
                  <option value="normal">정상 데이터</option>
                  <option value="partial">부분 평가 · PARTIAL</option>
                  <option value="empty">빈 데이터</option>
                  <option value="loading">로딩</option>
                  <option value="error">오류</option>
                </select>
                <ArrowUpRight size={12} aria-hidden="true" />
              </label>
            )}
          </footer>
        </main>
      </div>
    </div>
  );
}
