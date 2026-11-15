import { type ReactNode } from 'react';
import { useAdminSession } from './use-admin-session';
import { OverviewSourceContext, overviewApiAvailable } from './use-overview-source';

export function OverviewSourceProvider({ children }: { children: ReactNode }) {
  const session = useAdminSession();
  const available = overviewApiAvailable(
    import.meta.env.DEV,
    import.meta.env.VITE_ENABLE_OVERVIEW_API,
  );
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
