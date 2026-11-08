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
import { Link, NavLink, Outlet, useLocation } from 'react-router';
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
  const pageTitle =
    navigation.find((item) => item.to === location.pathname)?.label ?? '페이지 안내';
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
  const query = useDashboardData();
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
  const isUnknownRoute =
    !navigation.some((item) => item.to === location.pathname) && location.pathname !== '/';
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
            <strong>데모 워크스페이스</strong>
            <p>
              모든 데이터는 Mock입니다.
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
        <header className="app-header">
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
                <DialogDescription className="sr-only">
                  조회 전용 데모 대시보드 메뉴
                </DialogDescription>
                <Brand onSelect={() => setMobileOpen(false)} />
                <Navigation onSelect={() => setMobileOpen(false)} />
                <p className="text-muted small" style={{ marginTop: 'auto' }}>
                  Mock 데이터 · 조회 전용
                </p>
              </DialogContent>
            </Dialog>
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{pageTitle}</strong>
          </div>
          <div className="header-actions">
            <span className="header-timestamp">{dateTime(DEMO_AS_OF)} KST 기준</span>
            <span className="demo-pill">
              <span className="status-dot" />
              DEMO
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
              <Link to="/operations" aria-label="데모 운영 알림 보기">
                <Bell size={17} />
              </Link>
            </Button>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          <Suspense fallback={<LoadingSkeleton />}>
            {isUnknownRoute ? (
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
          </Suspense>
          <footer className="app-footer">
            <span>
              Crypto Trading Bot <span aria-hidden="true">/</span> 모든 수치는 데모입니다. 실제 운영
              데이터와 연결되지 않습니다.
            </span>
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
          </footer>
        </main>
      </div>
    </div>
  );
}
