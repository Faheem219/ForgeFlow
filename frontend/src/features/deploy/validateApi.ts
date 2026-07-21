import { apiFetch } from '../../lib/apiClient';
import type { LiveRunDto, ValidationReportDto } from '../../lib/types';

/** The last validation report, or `null` before validation has run. */
export function getLatestValidation(projectId: string): Promise<ValidationReportDto | null> {
  return apiFetch<ValidationReportDto | null>(`/projects/${projectId}/validate/latest`);
}

/** The newest `env=live` run — per-test results from production. */
export function getLiveRun(projectId: string): Promise<LiveRunDto | null> {
  return apiFetch<LiveRunDto | null>(`/projects/${projectId}/validate/live-run`);
}
