import { createContext, useContext } from 'react';
export type AnalysisSource = 'demo' | 'api';
export const AnalysisSourceContext = createContext<{
  source: AnalysisSource;
  available: boolean;
}>({ source: 'demo', available: false });
export const useAnalysisSource = () => useContext(AnalysisSourceContext);
