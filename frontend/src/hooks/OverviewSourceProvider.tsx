import { type ReactNode } from 'react';
import { useAdminSession } from './use-admin-session';
import { OverviewSourceContext } from './use-overview-source';
import { dashboardApis } from '@/lib/dashboard-api-config';

export function OverviewSourceProvider({ children }: { children: ReactNode }) {
  const session = useAdminSession();
  const available = dashboardApis().overview;
  return (
    <OverviewSourceContext.Provider
      value={{
        source: available && session.status === 'authenticated' ? 'api' : 'demo',
        available,
      }}
    >
      {children}
    </OverviewSourceContext.Provider>
  );
}
