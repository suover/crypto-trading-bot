import { useEffect, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router';
import { useAdminSession } from '@/hooks/use-admin-session';
import { Button } from '@/components/ui/button';

export function ApiSessionBoundary({ children }: { children: ReactNode }) {
  const session = useAdminSession();
  const { status, check } = session;
  const location = useLocation();
  useEffect(() => {
    if (status === 'unchecked') check();
  }, [status, check]);
  if (session.status === 'authenticated') return children;
  if (session.status === 'guest')
    return (
      <Navigate to="/login" replace state={{ returnTo: location.pathname + location.search }} />
    );
  if (session.status === 'error')
    return (
      <div role="alert" className="empty-state">
        <p>인증 상태를 확인하지 못했습니다. 서버 연결 또는 로그아웃 결과를 확인해 주세요.</p>
        <Button onClick={session.check}>인증 상태 다시 확인</Button>
      </div>
    );
  return <p role="status">로그인 상태 확인 중…</p>;
}
