import { apiFetch } from '../../lib/apiClient';
import type { ProjectCostDto, RunDto } from '../../lib/types';

/** Spend + budget headroom for one project, broken down by the work that caused it. */
export function getProjectCost(projectId: string): Promise<ProjectCostDto> {
  return apiFetch<ProjectCostDto>(`/projects/${projectId}/cost`);
}

/** The run explorer: every costed unit of work on this project, newest first. */
export function listRuns(projectId: string): Promise<RunDto[]> {
  return apiFetch<RunDto[]>(`/projects/${projectId}/runs`);
}
