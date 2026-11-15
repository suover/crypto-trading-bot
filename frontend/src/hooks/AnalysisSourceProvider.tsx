import { type ReactNode } from 'react';
import { useAdminSession } from './use-admin-session';
import { AnalysisSourceContext } from './use-analysis-source';
import { dashboardApis } from '@/lib/dashboard-api-config';

export function AnalysisSourceProvider({ children }: { children: ReactNode }) {
  const session = useAdminSession();
  const available = dashboardApis().analysis;
  return (
    <AnalysisSourceContext.Provider
      value={{
        source: available && session.status === 'authenticated' ? 'api' : 'demo',
        available,
      }}
    >
      {children}
    </AnalysisSourceContext.Provider>
  );
}
