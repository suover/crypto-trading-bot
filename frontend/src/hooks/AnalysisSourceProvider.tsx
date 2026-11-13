import { useState, type ReactNode } from 'react';
import {
  AnalysisSourceContext,
  analysisApiAvailable,
  type AnalysisSource,
} from './use-analysis-source';
export function AnalysisSourceProvider({ children }: { children: ReactNode }) {
  const [source, setSource] = useState<AnalysisSource>('demo');
  const available = analysisApiAvailable(
    import.meta.env.DEV,
    import.meta.env.VITE_ENABLE_AI_ANALYSIS_API,
  );
  return (
    <AnalysisSourceContext.Provider
      value={{
        source: available ? source : 'demo',
        available,
        setSource: (v) => setSource(available ? v : 'demo'),
      }}
    >
      {children}
    </AnalysisSourceContext.Provider>
  );
}
