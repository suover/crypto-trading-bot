import { createContext, useContext } from 'react';
import { useQuery } from '@tanstack/react-query';
import { createMockRepository } from '@/services/mock-dashboard-repository';
import type { DashboardRepository } from '@/services/dashboard-repository';
import type { DemoScenario } from '@/types/dashboard';

export const mockRepository = createMockRepository();
export const DemoScenarioContext = createContext<{
  scenario: DemoScenario;
  setScenario: (scenario: DemoScenario) => void;
  repository: DashboardRepository;
}>({ scenario: 'normal', setScenario: () => undefined, repository: mockRepository });

export function useDemoScenario() {
  const { scenario, setScenario } = useContext(DemoScenarioContext);
  return { scenario, setScenario };
}

export function useDashboardData() {
  const { scenario, repository: adapter } = useContext(DemoScenarioContext);
  return useQuery({
    queryKey: ['dashboard', scenario],
    queryFn: ({ signal }) => adapter.read({ scenario, signal }),
    staleTime: Infinity,
    retry: false,
  });
}
