import { afterEach, expect, it, vi } from 'vitest';
import { dashboardApis } from './dashboard-api-config';

afterEach(() => vi.unstubAllEnvs());

it.each([
  [true, '', { research: true, overview: true, analysis: true }],
  [false, 'true', { research: true, overview: false, analysis: false }],
  [false, '', { research: false, overview: false, analysis: false }],
] as const)(
  'uses the configured APIs for PROD=%s and development Research flag=%s',
  (prod, researchFlag, expected) => {
    vi.stubEnv('DEV', !prod);
    vi.stubEnv('PROD', prod);
    vi.stubEnv('VITE_ENABLE_RESEARCH_API', researchFlag);
    vi.stubEnv('VITE_ENABLE_OVERVIEW_API', 'false');
    vi.stubEnv('VITE_ENABLE_AI_ANALYSIS_API', '');
    expect(dashboardApis()).toEqual(expected);
  },
);
