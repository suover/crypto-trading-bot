import { useQuery } from '@tanstack/react-query';
import { overviewApi } from '@/services/overview-api-client';
import type { Summary, PerformanceFilter, RecommendationFilter } from '@/types/overview-api';
export const overviewApiKey = ['overview-local-api'] as const;
const options = {
  retry: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
  staleTime: 0,
  gcTime: 0,
} as const;
export const useOverviewSummary = () =>
  useQuery({
    ...options,
    queryKey: [...overviewApiKey, 'summary'],
    queryFn: ({ signal }) => overviewApi.summary(signal),
  });
export const useOverviewPositions = (summary: Summary | undefined, enabled: boolean) =>
  useQuery({
    ...options,
    enabled: enabled && summary !== undefined,
    queryKey: [
      ...overviewApiKey,
      'positions',
      summary?.scope,
      summary?.portfolio?.snapshotId ?? null,
      summary?.portfolio?.capturedAt ?? null,
    ],
    queryFn: ({ signal }) => {
      if (!summary) throw new Error('Summary required');
      return overviewApi.positions(summary, signal);
    },
  });
export const useOverviewPerformance = (filter: PerformanceFilter) =>
  useQuery({
    ...options,
    queryKey: [...overviewApiKey, 'performance', filter],
    queryFn: ({ signal }) => overviewApi.performance(filter, signal),
  });
export const useOverviewRecommendations = (filter: RecommendationFilter) =>
  useQuery({
    ...options,
    queryKey: [...overviewApiKey, 'recommendations', filter],
    queryFn: ({ signal }) => overviewApi.recommendations(filter, signal),
  });
