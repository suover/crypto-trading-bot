import { useState, type ReactNode } from 'react';
import {
  OverviewSourceContext,
  overviewApiAvailable,
  type OverviewSource,
} from './use-overview-source';
export function OverviewSourceProvider({ children }: { children: ReactNode }) {
  const [source, setSource] = useState<OverviewSource>('demo');
  const available = overviewApiAvailable(
    import.meta.env.DEV,
    import.meta.env.VITE_ENABLE_OVERVIEW_API,
  );
  return (
    <OverviewSourceContext.Provider
      value={{
        source: available ? source : 'demo',
        available,
        setSource: (v) => setSource(available ? v : 'demo'),
      }}
    >
      {children}
    </OverviewSourceContext.Provider>
  );
}
