import { createContext, useContext } from 'react';
export type OverviewSource = 'demo' | 'api';
export const OverviewSourceContext = createContext<{
  source: OverviewSource;
  available: boolean;
}>({ source: 'demo', available: false });
export const useOverviewSource = () => useContext(OverviewSourceContext);
