import { createContext, useContext } from 'react';
export type ResearchSource = 'demo' | 'api';
export const ResearchSourceContext = createContext<{
  source: ResearchSource;
  available: boolean;
}>({ source: 'demo', available: false });
export const useResearchSource = () => useContext(ResearchSourceContext);
