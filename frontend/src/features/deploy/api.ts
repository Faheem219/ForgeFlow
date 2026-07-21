import { apiFetch } from '../../lib/apiClient';
import type { DeployLogsDto, DeploymentDto } from '../../lib/types';

/** The last deployment, or `null` before the first one — what the graph hydrates from on load. */
export function getLatestDeployment(projectId: string): Promise<DeploymentDto | null> {
  return apiFetch<DeploymentDto | null>(`/projects/${projectId}/deploy/latest`);
}

/** The step log the last deploy recorded; the node drawer filters it down to one node's lines. */
export function getDeployLogs(projectId: string): Promise<DeployLogsDto | null> {
  return apiFetch<DeployLogsDto | null>(`/projects/${projectId}/deploy/latest/logs`);
}
