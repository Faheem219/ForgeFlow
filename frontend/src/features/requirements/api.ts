import { ApiError, apiFetch } from '../../lib/apiClient';
import type {
  DraftedSpec,
  RequirementSpecDto,
  RequirementSpecInput,
  SuggestedCriterion,
} from '../../lib/types';

/** Latest saved spec, or `null` when the project has no requirements yet (404 → null). */
export async function getRequirements(projectId: string): Promise<RequirementSpecDto | null> {
  try {
    return await apiFetch<RequirementSpecDto>(`/projects/${projectId}/requirements`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

/** Save the spec. The backend appends a new version only when the content changes (phase-25). */
export function saveRequirements(
  projectId: string,
  spec: RequirementSpecInput,
): Promise<RequirementSpecDto> {
  return apiFetch<RequirementSpecDto>(`/projects/${projectId}/requirements`, {
    method: 'POST',
    body: JSON.stringify(spec),
  });
}

/** Every version, oldest → newest. */
export function listRequirementVersions(projectId: string): Promise<RequirementSpecDto[]> {
  return apiFetch<RequirementSpecDto[]>(`/projects/${projectId}/requirements/versions`);
}

/** Optional AI-assist: editable acceptance-criteria proposals for one feature (Haiku). */
export function suggestCriteria(
  projectId: string,
  name: string,
  description: string,
): Promise<SuggestedCriterion[]> {
  return apiFetch<{ criteria: SuggestedCriterion[] }>(
    `/projects/${projectId}/requirements/suggest`,
    { method: 'POST', body: JSON.stringify({ name, description }) },
  ).then((r) => r.criteria);
}

/**
 * Optional AI-assist: a freeform description → a whole draft feature list (Haiku). The draft is
 * never persisted — it seeds the guided form, and only an explicit save creates a version.
 */
export function draftRequirements(projectId: string, description: string): Promise<DraftedSpec> {
  return apiFetch<DraftedSpec>(`/projects/${projectId}/requirements/draft`, {
    method: 'POST',
    body: JSON.stringify({ description }),
  });
}

/** Map a saved spec back into the request shape, preserving criterion ids for stable joins. */
export function specToInput(spec: RequirementSpecDto): RequirementSpecInput {
  return {
    app_name: spec.app_name,
    features: spec.features.map((f) => ({
      name: f.name,
      description: f.description,
      inputs: [...f.inputs],
      expected_behaviors: [...f.expected_behaviors],
      acceptance_criteria: f.acceptance_criteria.map((c) => ({
        id: c.id,
        text: c.text,
        kind: c.kind,
      })),
    })),
  };
}
