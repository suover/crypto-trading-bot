import { resolveDashboardApis } from '../../dashboard-api-options';

export function dashboardApis() {
  return resolveDashboardApis(import.meta.env.PROD, import.meta.env.DEV, {
    research: import.meta.env.VITE_ENABLE_RESEARCH_API,
    overview: import.meta.env.VITE_ENABLE_OVERVIEW_API,
    analysis: import.meta.env.VITE_ENABLE_AI_ANALYSIS_API,
  });
}
