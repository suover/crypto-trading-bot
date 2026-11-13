import { createContext, useContext } from 'react';
export type AnalysisSource = 'demo' | 'api';
export const AnalysisSourceContext = createContext<{
  source: AnalysisSource;
  available: boolean;
  setSource: (source: AnalysisSource) => void;
}>({ source: 'demo', available: false, setSource: () => undefined });
export const useAnalysisSource = () => useContext(AnalysisSourceContext);
export const analysisApiAvailable = (dev: boolean, flag: unknown) => dev && flag === 'true';
