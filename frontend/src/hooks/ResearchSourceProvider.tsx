import { type ReactNode } from 'react';
import { useAdminSession } from './use-admin-session';
import { ResearchSourceContext } from './use-research-source';
import { dashboardApis } from '@/lib/dashboard-api-config';

export function ResearchSourceProvider({ children }: { children: ReactNode }) {
  const session = useAdminSession();
  const available = dashboardApis().research;
  return (
    <ResearchSourceContext.Provider
      value={{
        source: available && session.status === 'authenticated' ? 'api' : 'demo',
        available,
      }}
    >
      {children}
    </ResearchSourceContext.Provider>
  );
}
