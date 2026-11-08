import { useState, type ReactNode } from 'react';
import type { DashboardRepository } from '@/services/dashboard-repository';
import type { DemoScenario } from '@/types/dashboard';
import { DemoScenarioContext, mockRepository } from './use-dashboard-data';

export function DemoScenarioProvider({
  children,
  initialScenario = 'normal',
  adapter = mockRepository,
}: {
  children: ReactNode;
  initialScenario?: DemoScenario;
  adapter?: DashboardRepository;
}) {
  const [scenario, setScenario] = useState<DemoScenario>(initialScenario);
  return (
    <DemoScenarioContext.Provider value={{ scenario, setScenario, repository: adapter }}>
      {children}
    </DemoScenarioContext.Provider>
  );
}
