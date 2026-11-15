import { type ReactNode } from 'react';
import { useAdminSession } from './use-admin-session';
import { ResearchSourceContext, researchApiAvailable } from './use-research-source';

export function ResearchSourceProvider({ children }: { children: ReactNode }) {
  const session = useAdminSession();
  const available = researchApiAvailable(
    import.meta.env.DEV,
    import.meta.env.VITE_ENABLE_RESEARCH_API,
  );
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
