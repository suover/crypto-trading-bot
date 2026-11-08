import type { DashboardData, DemoScenario } from '@/types/dashboard';

/** The future Spring Boot adapter implements this read-only contract. No write methods. */
export interface DashboardRepository {
  read(options?: { scenario?: DemoScenario; signal?: AbortSignal }): Promise<DashboardData>;
}
