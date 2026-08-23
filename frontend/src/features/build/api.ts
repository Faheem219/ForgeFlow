import { apiFetch } from '../../lib/apiClient';
import type { ArtifactDetail, ArtifactDto } from '../../lib/types';

/** One phase of a phased build (backend app/agents/build_phases.py). */
export interface BuildPhaseData {
  id: string;
  title: string;
  kind: string;
  status: string;
  files: string[];
  commit: string | null;
  attempts: number;
  note: string;
  summary: string;
}

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
  /** phase-56 — absent on every report written before phasing, hence always defaulted. */
  phases: BuildPhaseData[];
  verification: Record<string, unknown> | null;
  outcome: string;
  stop_reason: string | null;
  /**
   * phase-60 — which path the build took: 'small' is one incremental pass, 'large' is a planned,
   * phase-by-phase build. Empty on every report written before scope routing.
   */
  scope: string;
  scope_reason: string;
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
  /** The phase in flight (phase-56) — what a reload reattaches the phase rail to. */
  phase_index?: number;
  phase_total?: number;
  phase_id?: string;
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

function strArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function parsePhase(value: unknown): BuildPhaseData | null {
  if (typeof value !== 'object' || value === null) return null;
  const raw = value as Record<string, unknown>;
  const title = str(raw.title);
  if (!title) return null;
  return {
    id: str(raw.id, title),
    title,
    kind: str(raw.kind, 'backend'),
    status: str(raw.status, 'pending'),
    files: strArray(raw.files),
    commit: typeof raw.commit === 'string' ? raw.commit : null,
    attempts: typeof raw.attempts === 'number' ? raw.attempts : 0,
    note: str(raw.note),
    summary: str(raw.summary),
  };
}

/**
 * Parse a persisted build-report artifact into a shape the panel can render without guards.
 *
 * Every field is defaulted rather than trusted. The panel calls `.length` / `.join` on four array
 * fields, so a report that is old, truncated, or hand-edited in the artifact viewer used to crash
 * the whole Build tab. Defaulting here — including `phases: []` — is also what makes a
 * pre-phase-56 report render exactly as it always did.
 */
export function parseBuildReport(content: string | null | undefined): BuildReportData | null {
  if (!content) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null;
  const raw = parsed as Record<string, unknown>;
  return {
    boot_status: str(raw.boot_status, 'not_started'),
    files_changed: strArray(raw.files_changed),
    features_built: strArray(raw.features_built),
    follow_ups: strArray(raw.follow_ups),
    notes: strArray(raw.notes),
    commit: typeof raw.commit === 'string' ? raw.commit : null,
    tests_passed: typeof raw.tests_passed === 'boolean' ? raw.tests_passed : null,
    summary: str(raw.summary),
    plan: str(raw.plan),
    phases: Array.isArray(raw.phases)
      ? raw.phases.map(parsePhase).filter((p): p is BuildPhaseData => p !== null)
      : [],
    verification:
      typeof raw.verification === 'object' &&
      raw.verification !== null &&
      !Array.isArray(raw.verification)
        ? (raw.verification as Record<string, unknown>)
        : null,
    outcome: str(raw.outcome),
    stop_reason: typeof raw.stop_reason === 'string' ? raw.stop_reason : null,
    scope: str(raw.scope),
    scope_reason: str(raw.scope_reason),
  };
}
