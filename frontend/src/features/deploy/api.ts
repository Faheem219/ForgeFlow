import { apiFetch } from '../../lib/apiClient';
import type {
  DeployConfigDto,
  DeployDeleteDto,
  DeployLogsDto,
  DeploymentDto,
  ProviderLogsDto,
  TopologyNodeId,
} from '../../lib/types';

/** The last deployment, or `null` before the first one — what the graph hydrates from on load. */
export function getLatestDeployment(projectId: string): Promise<DeploymentDto | null> {
  return apiFetch<DeploymentDto | null>(`/projects/${projectId}/deploy/latest`);
}

/**
 * ForgeFlow's own **pipeline** log: which database mode was chosen, why the frontend was skipped,
 * whether the env mirror landed. Nothing else records any of that, which is why it sits beside the
 * provider log rather than being replaced by it.
 */
export function getDeployLogs(projectId: string): Promise<DeployLogsDto | null> {
  return apiFetch<DeployLogsDto | null>(`/projects/${projectId}/deploy/latest/logs`);
}

/** The **provider's** own build log for one node (phase-62). Never rejects — see ProviderLogsDto. */
export function getProviderLogs(
  projectId: string,
  target: TopologyNodeId,
): Promise<ProviderLogsDto | null> {
  return apiFetch<ProviderLogsDto | null>(`/projects/${projectId}/deploy/latest/logs/${target}`);
}

/** Which credentials a BYO deploy needs on this instance — the backend provider is configurable. */
export function getDeployConfig(projectId: string): Promise<DeployConfigDto> {
  return apiFetch<DeployConfigDto>(`/projects/${projectId}/deploy/config`);
}

/** Take the current deployment down at the provider. Hosting only — the database is untouched. */
export function deleteDeployment(projectId: string): Promise<DeployDeleteDto> {
  return apiFetch<DeployDeleteDto>(`/projects/${projectId}/deploy/latest`, { method: 'DELETE' });
}
