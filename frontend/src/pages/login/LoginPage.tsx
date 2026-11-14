import { useOverviewSource } from '@/hooks/use-overview-source';
import { useResearchSource } from '@/hooks/use-research-source';
import { useAnalysisSource } from '@/hooks/use-analysis-source';
import { useEffect, useState, type FormEvent } from 'react';
import { Link, Navigate, useLocation } from 'react-router';
import { useAdminSession } from '@/hooks/use-admin-session';
import { AuthError } from '@/services/auth-api';
import { Button } from '@/components/ui/button';
import './login.css';

function returnPath(state: unknown) {
  const path = state && typeof state === 'object' && 'returnTo' in state ? state.returnTo : null;
  return typeof path === 'string' && /^\/(overview|research|ai-analysis)(\?[^#\\]*)?$/.test(path)
    ? path
    : '/overview';
}
export default function LoginPage() {
  const session = useAdminSession();
  const overview = useOverviewSource();
  const research = useResearchSource();
  const analysis = useAnalysisSource();
  const { status, check } = session;
  const location = useLocation();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (status === 'unchecked') check();
  }, [status, check]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError('');
    const entered = password;
    setPassword('');
    try {
      await session.login(username, entered);
    } catch (failure) {
      setError(
        failure instanceof AuthError && failure.status === 401
          ? '아이디 또는 비밀번호를 확인해 주세요.'
          : failure instanceof AuthError && failure.status === 429
            ? '로그인 시도가 많습니다. 잠시 후 다시 시도해 주세요.'
            : '로그인에 실패했습니다. 서버 연결을 확인하고 다시 시도해 주세요.',
      );
    } finally {
      setPending(false);
    }
  }
  if (session.status === 'authenticated')
    return <Navigate to={returnPath(location.state)} replace />;
  return (
    <main className="login-page">
      <section className="card login-card">
        <span className="eyebrow">CRYPTO TRADING BOT · READ ONLY</span>
        <h1>관리자 로그인</h1>
        <p>
          계좌 현황과 연구·AI 분석 데이터를 조회합니다. 로그인은 거래나 주문 승인을 실행하지
          않습니다.
        </p>
        {!session.available ? (
          <p role="alert">
            이 환경은 DEMO 전용입니다. 로컬 API 연결을 활성화한 환경에서 로그인할 수 있습니다.
          </p>
        ) : session.status === 'unchecked' || session.status === 'checking' ? (
          <p role="status">로그인 상태 확인 중…</p>
        ) : (
          <form
            onSubmit={(event) => {
              void submit(event);
            }}
          >
            {session.status === 'error' && (
              <p role="alert">
                인증 상태 확인에 실패했습니다. 연결을 확인하고 다시 로그인해 주세요.
              </p>
            )}
            <label htmlFor="admin-username">관리자 아이디</label>
            <input
              id="admin-username"
              className="field"
              autoComplete="username"
              required
              maxLength={128}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={pending}
            />
            <label htmlFor="admin-password">비밀번호</label>
            <input
              id="admin-password"
              className="field"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={pending}
            />
            {error && <p role="alert">{error}</p>}
            <Button type="submit" disabled={pending}>
              {pending ? '로그인 중…' : '로그인'}
            </Button>
          </form>
        )}
        <Link
          className="text-button"
          to="/overview"
          onClick={() => {
            overview.setSource('demo');
            research.setSource('demo');
            analysis.setSource('demo');
          }}
        >
          DEMO 대시보드로 이동
        </Link>
      </section>
    </main>
  );
}
