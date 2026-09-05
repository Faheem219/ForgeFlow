import { apiFetch } from '../../lib/apiClient';
import type {
  RepairLoopDto,
  TestRunDto,
  TestRunEnv,
  TestRunSummaryDto,
  TestScope,
} from '../../lib/types';

export function runTests(
  projectId: string,
  scope: TestScope = 'all',
  filter?: string,
): Promise<TestRunDto> {
  return apiFetch<TestRunDto>(`/projects/${projectId}/tests/run`, {
    method: 'POST',
    body: JSON.stringify({ scope, filter: filter || null }),
  });
}

/**
 * Test runs, newest first (the backend sorts descending by creation). Summaries only — fetch a
 * run with {@link getTestRun} for its per-test results.
 *
 * Scoped to the sandbox on purpose: live-validation runs (phase-39) share the same collection but
 * only cover the E2E subset, so an unscoped list would make the Test stage show the live run's
 * handful of results as if the suites had shrunk. Live results come from `getLiveRun` instead.
 */
export function listTestRuns(
  projectId: string,
  env: TestRunEnv = 'sandbox',
): Promise<TestRunSummaryDto[]> {
  return apiFetch<TestRunSummaryDto[]>(`/projects/${projectId}/tests/runs?env=${env}`);
}

export function getTestRun(projectId: string, runId: string): Promise<TestRunDto> {
  return apiFetch<TestRunDto>(`/projects/${projectId}/tests/runs/${runId}`);
}

export function getTestStdout(projectId: string, runId: string): Promise<{ stdout: string }> {
  return apiFetch<{ id: string; stdout: string }>(
    `/projects/${projectId}/tests/runs/${runId}/stdout`,
  );
}

/** Start the bounded repair loop; iterations stream as realtime events meanwhile. */
export function runRepair(projectId: string, testRunId?: string): Promise<RepairLoopDto> {
  return apiFetch<RepairLoopDto>(`/projects/${projectId}/repair/run`, {
    method: 'POST',
    body: JSON.stringify({ test_run_id: testRunId ?? null }),
  });
}

/** The persisted loop report, so a reload doesn't lose the escalation payload. */
export function getLatestRepair(projectId: string): Promise<RepairLoopDto | null> {
  return apiFetch<RepairLoopDto | null>(`/projects/${projectId}/repair/latest`);
}

/** Ask a running loop to stop at the next iteration boundary. */
export function cancelRepair(projectId: string): Promise<{ cancelling: boolean }> {
  return apiFetch<{ cancelling: boolean }>(`/projects/${projectId}/repair/cancel`, {
    method: 'POST',
  });
}

export function getAttemptDiff(projectId: string, attemptId: string): Promise<{ diff: string }> {
  return apiFetch<{ attempt_id: string; iteration: number; diff: string }>(
    `/projects/${projectId}/repair/attempts/${attemptId}/diff`,
  );
}
