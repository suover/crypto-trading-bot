import { type ReactNode } from 'react';
import { useAdminSession } from './use-admin-session';
import { AnalysisSourceContext, analysisApiAvailable } from './use-analysis-source';

export function AnalysisSourceProvider({ children }: { children: ReactNode }) {
  const session = useAdminSession();
  const available = analysisApiAvailable(
    import.meta.env.DEV,
    import.meta.env.VITE_ENABLE_AI_ANALYSIS_API,
  );
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
