import { useQuery } from '@tanstack/react-query';
import { analysisApi } from '@/services/analysis-api-client';
import type { Search, Detail, DetailReference } from '@/types/analysis-api';
export const analysisApiKey = ['analysis-local-api'] as const;
const options = {
  retry: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
  staleTime: 0,
  gcTime: 0,
} as const;
export const useAnalysisRecommendations = (filter: Search) =>
  useQuery({
    ...options,
    queryKey: [...analysisApiKey, 'list', filter],
    queryFn: ({ signal }) => analysisApi.recommendations(filter, signal),
  });
export const useAnalysisDetail = (ref: DetailReference) =>
  useQuery({
    ...options,
    queryKey: [...analysisApiKey, 'detail', ref],
    queryFn: ({ signal }) => analysisApi.detail(ref, signal),
  });
export const useAnalysisCandidates = (detail: Detail, page: number) =>
  useQuery({
    ...options,
    enabled: detail.universe.status === 'VERIFIED',
    queryKey: [
      ...analysisApiKey,
      'candidates',
      detail.recommendation.id,
      detail.scope,
      detail.universe,
      page,
    ],
    queryFn: ({ signal }) => analysisApi.candidates(detail, page, 20, signal),
  });
