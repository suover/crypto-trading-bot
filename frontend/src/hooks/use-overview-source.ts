import { createContext, useContext } from 'react';
export type OverviewSource = 'demo' | 'api';
export const OverviewSourceContext = createContext<{
  source: OverviewSource;
  setSource: (source: OverviewSource) => void;
  available: boolean;
}>({ source: 'demo', setSource: () => undefined, available: false });
export const useOverviewSource = () => useContext(OverviewSourceContext);
export const overviewApiAvailable = (dev: boolean, flag: unknown) => dev && flag === 'true';
