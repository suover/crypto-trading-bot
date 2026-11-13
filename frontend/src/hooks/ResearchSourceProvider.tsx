import { useState, type ReactNode } from 'react';
import {
  ResearchSourceContext,
  researchApiAvailable,
  type ResearchSource,
} from './use-research-source';

export function ResearchSourceProvider({ children }: { children: ReactNode }) {
  const [source, setSource] = useState<ResearchSource>('demo');
  const available = researchApiAvailable(
    import.meta.env.DEV,
    import.meta.env.VITE_ENABLE_RESEARCH_API,
  );
  return (
    <ResearchSourceContext.Provider
      value={{
        source: available ? source : 'demo',
        available,
        setSource: (value) => setSource(available ? value : 'demo'),
      }}
    >
      {children}
    </ResearchSourceContext.Provider>
  );
}
