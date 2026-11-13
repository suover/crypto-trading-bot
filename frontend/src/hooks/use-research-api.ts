import { useQuery } from '@tanstack/react-query';
import { researchApi } from '@/services/research-api-client';
import type { CandidateFilters } from '@/types/research-api';
export const researchApiKey = ['research-local-api'] as const;
const options = {
  retry: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
  staleTime: 0,
  gcTime: 0,
} as const;
export const useResearchSummary = () =>
  useQuery({
    ...options,
    queryKey: [...researchApiKey, 'summary'],
    queryFn: ({ signal }) => researchApi.summary(signal),
  });
export const useResearchCandidates = (filters: CandidateFilters) =>
  useQuery({
    ...options,
    queryKey: [...researchApiKey, 'candidates', filters],
    queryFn: ({ signal }) => researchApi.candidates(filters, signal),
  });
export const useResearchCandidateDetail = (id: string) =>
  useQuery({
    ...options,
    queryKey: [...researchApiKey, 'detail', id],
    queryFn: ({ signal }) => researchApi.detail(id, signal),
  });
export const useResearchSnapshots = (page: number, size: number) =>
  useQuery({
    ...options,
    queryKey: [...researchApiKey, 'snapshots', page, size],
    queryFn: ({ signal }) => researchApi.snapshots(page, size, signal),
  });
export const useResearchAutomationStatus = () =>
  useQuery({
    ...options,
    queryKey: [...researchApiKey, 'automation'],
    queryFn: ({ signal }) => researchApi.automation(signal),
  });
