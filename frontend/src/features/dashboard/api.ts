import { LONG_TASK_TIMEOUT_MS, apiFetch } from '../../lib/apiClient';
import type { ProjectSummary } from '../../lib/types';

export function listProjects(): Promise<ProjectSummary[]> {
  return apiFetch<ProjectSummary[]>('/projects');
}

export function createProject(name: string): Promise<ProjectSummary> {
  return apiFetch<ProjectSummary>('/projects', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

/** What a delete actually reclaimed; `warnings` names anything that outlived the project. */
export interface ProjectDeleteResult {
  id: string;
  sandbox_removed: boolean;
  app_db_dropped: boolean;
  documents_removed: number;
  warnings: string[];
}

/**
 * Delete a project and everything it owns. Given a generous deadline: the server tears down a
 * Docker container and drops a database before it answers, and neither is instant.
 */
export function deleteProject(id: string): Promise<ProjectDeleteResult> {
  return apiFetch<ProjectDeleteResult>(`/projects/${id}`, {
    method: 'DELETE',
    timeoutMs: LONG_TASK_TIMEOUT_MS,
  });
}
