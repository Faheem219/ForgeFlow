import { apiBaseUrl, apiFetch } from '../../lib/apiClient';
import { useAuthStore } from '../../lib/stores/authStore';
import type { EvalRunDto, EvalSummaryDto } from '../../lib/types';

/** Aggregates + per-spec records for the newest run (or a named one). */
export function getEvalSummary(source?: string): Promise<EvalSummaryDto> {
  const query = source ? `?source=${encodeURIComponent(source)}` : '';
  return apiFetch<EvalSummaryDto>(`/eval/summary${query}`);
}

export function listEvalRuns(): Promise<EvalRunDto[]> {
  return apiFetch<EvalRunDto[]>('/eval/runs');
}

/**
 * Fetch a report as text. The endpoints are authenticated, so a bare `<a download>` would 401 —
 * the token has to ride on the request, which means fetching and handing back a blob.
 */
export async function fetchReport(
  format: 'csv' | 'md',
  source?: string | null,
): Promise<{ filename: string; text: string }> {
  const { token } = useAuthStore.getState();
  const query = source ? `?source=${encodeURIComponent(source)}` : '';
  const res = await fetch(`${apiBaseUrl}/eval/report.${format}${query}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Export failed with status ${res.status}`);
  return { filename: `forgeflow-eval.${format}`, text: await res.text() };
}
