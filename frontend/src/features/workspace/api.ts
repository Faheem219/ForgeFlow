import { apiFetch } from '../../lib/apiClient';
import type {
  ArtifactDto,
  IntentRequest,
  IntentResponse,
  MessageDto,
  ProjectSummary,
  Stage,
  StageStateDto,
} from '../../lib/types';

export function getProject(id: string): Promise<ProjectSummary> {
  return apiFetch<ProjectSummary>(`/projects/${id}`);
}

export function listStages(id: string): Promise<StageStateDto[]> {
  return apiFetch<StageStateDto[]>(`/projects/${id}/stages`);
}

export function listMessages(id: string, stage?: Stage): Promise<MessageDto[]> {
  const query = stage ? `?stage=${stage}` : '';
  return apiFetch<MessageDto[]>(`/projects/${id}/messages${query}`);
}

export function listArtifacts(id: string, stage?: Stage): Promise<ArtifactDto[]> {
  const query = stage ? `?stage=${stage}` : '';
  return apiFetch<ArtifactDto[]>(`/projects/${id}/artifacts${query}`);
}

export function submitIntent(id: string, body: IntentRequest): Promise<IntentResponse> {
  return apiFetch<IntentResponse>(`/projects/${id}/intent`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
