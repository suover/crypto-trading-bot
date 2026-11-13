import { createContext, useContext } from 'react';
export type ResearchSource = 'demo' | 'api';
export const ResearchSourceContext = createContext<{
  source: ResearchSource;
  setSource: (source: ResearchSource) => void;
  available: boolean;
}>({ source: 'demo', setSource: () => undefined, available: false });
export const useResearchSource = () => useContext(ResearchSourceContext);

export function researchApiAvailable(dev: boolean, flag: unknown): boolean {
  return dev && flag === 'true';
}
