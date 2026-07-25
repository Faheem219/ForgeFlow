import { apiFetch } from '../../lib/apiClient';
import type { ArtifactDetail, ArtifactDto } from '../../lib/types';

/** The structured build report the codegen agent persists (backend app/agents/codegen.py). */
export interface BuildReportData {
  boot_status: string;
  files_changed: string[];
  features_built: string[];
  follow_ups: string[];
  notes: string[];
  commit: string | null;
  tests_passed: boolean | null;
  summary: string;
  plan: string;
}

export const BUILD_REPORT_KIND = 'build_report';

/** A build running right now, as reported by the server (survives a page reload). */
export interface BuildActivity {
  run_id: string;
  started_at: string;
  step: string;
  label: string;
  target: string;
  files: string[];
}

export interface ProjectActivity {
  build: BuildActivity | null;
}

/**
 * Long-running work in flight for this project.
 *
 * The reattach point: a build is detached from the request that started it, so after a reload this
 * is the only way to discover one is still running (and what it has written so far).
 */
export function getActivity(projectId: string): Promise<ProjectActivity> {
  return apiFetch<ProjectActivity>(`/projects/${projectId}/activity`);
}

/** Build-report artifacts (code_change with meta.kind === 'build_report'), oldest → newest. */
export function listBuildReports(projectId: string): Promise<ArtifactDto[]> {
  return apiFetch<ArtifactDto[]>(`/projects/${projectId}/artifacts?stage=build&type=code_change`);
}

export function getArtifact(artifactId: string): Promise<ArtifactDetail> {
  return apiFetch<ArtifactDetail>(`/artifacts/${artifactId}`);
}

export function parseBuildReport(content: string | null | undefined): BuildReportData | null {
  if (!content) return null;
  try {
    return JSON.parse(content) as BuildReportData;
  } catch {
    return null;
  }
}
