import { apiFetch } from '../../lib/apiClient';
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
