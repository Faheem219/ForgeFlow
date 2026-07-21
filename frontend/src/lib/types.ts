// Shared control-plane DTOs mirrored from the FastAPI backend (see backend/app/**).

export type Stage = 'design' | 'requirements' | 'build' | 'test' | 'deploy' | 'validate';

export type StageStatus =
  'empty' | 'in_progress' | 'awaiting_user' | 'complete' | 'skipped' | 'stale';

export type IntentAction = 'refine' | 'proceed' | 'skip';

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export interface ProjectSummary {
  id: string;
  name: string;
  current_stage: Stage;
  status: 'active' | 'archived';
  stack: string;
  sandbox_id: string | null;
  /** Per-project design-provider override; null → the platform default (phase-16). */
  design_provider: string | null;
  app_db_name: string;
  created_at: string;
  updated_at: string;
}

export interface StageStateDto {
  stage: Stage;
  status: StageStatus;
  artifacts: string[];
  updated_at: string;
}

export interface MessageDto {
  id: string;
  stage: Stage | null;
  role: MessageRole;
  content: string;
  artifacts: string[];
  token_usage: Record<string, unknown>;
  created_at: string;
}

export interface ArtifactDto {
  id: string;
  project_id: string;
  stage: Stage;
  type: string;
  version: number;
  ref: string | null;
  meta: Record<string, unknown>;
  created_at: string;
}

/** Workspace filesystem (backend app/sandbox/fs.py). Paths are workspace-relative, POSIX. */
export interface FileNode {
  path: string;
  type: 'file' | 'dir';
  size: number | null;
}

export interface FileContent {
  path: string;
  content: string;
  size: number;
}

/** Live preview (backend app/sandbox/preview.py). */
export type PreviewStatus = 'stopped' | 'starting' | 'running' | 'failed';

export interface PreviewInfo {
  project_id: string;
  fe_url: string | null;
  be_url: string | null;
  fe_status: PreviewStatus;
  be_status: PreviewStatus;
}

export interface IntentRequest {
  stage: Stage;
  action: IntentAction;
  message?: string;
  /** Stage-specific payload (e.g. design intake: text / image_refs / own_design). */
  payload?: Record<string, unknown>;
}

/** An artifact plus its resolved text payload (backend GET /artifacts/{id}). */
export interface ArtifactDetail extends ArtifactDto {
  content: string | null;
}

// --- Design stage (phase-19) ---

export type ProviderHealth = 'ok' | 'degraded' | 'down';

export interface DesignImageRef {
  ref: string;
  filename: string;
  media_type: string;
}

export interface DesignCapabilities {
  provider: string;
  from_image: boolean;
  from_text: boolean;
  refine: boolean;
  fetch_code: boolean;
  max_images: number;
}

export interface DesignProviderInfo {
  key: string;
  health: ProviderHealth;
  capabilities: DesignCapabilities;
}

// --- Testing + repair (phases 28 / 31 / 32) ---

export type TestStatus = 'passed' | 'failed' | 'skipped';
export type TestScope = 'unit' | 'e2e' | 'all';

export interface TestFailure {
  message: string;
  assertion: string | null;
  stack: string | null;
  files_referenced: string[];
}

export interface TestResultDto {
  name: string;
  status: TestStatus;
  framework: string;
  /** The acceptance criterion this test verifies (phase-25 → 27 → 28). */
  criterion_id: string | null;
  file: string | null;
  duration_ms: number | null;
  failure: TestFailure | null;
}

export interface TestRunDto {
  id: string;
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  green: boolean;
  env: string;
  created_at: string;
  results: TestResultDto[];
  failures: TestResultDto[];
  suite_refs: string[];
  stdout_ref: string | null;
}

export interface RepairMetrics {
  initial_failing: number;
  final_failing: number;
  failing_by_iteration: number[];
  regressions_introduced: number;
  iterations: number;
  tokens_spent: number;
  cost_inr: number;
  wall_clock_s: number;
}

export interface RepairAttemptDto {
  /** The attempt's id — what the diff endpoint keys on. */
  id: string | null;
  iteration: number;
  target_files: string[];
  diff_ref: string | null;
  outcome: 'fixed' | 'no_progress' | 'regressed' | null;
}

export interface RepairEscalation {
  reason: string;
  summary: string;
  failing_tests: Array<{
    name: string;
    criterion_id: string | null;
    file: string | null;
    message: string;
  }>;
  diffs_tried: RepairAttemptDto[];
  metrics: RepairMetrics;
  /** How to resume — the controller names the Build/refine contract (phase-31). */
  resume: { stage: Stage; action: IntentAction; hint: string };
}

export interface RepairLoopDto {
  outcome: 'fixed' | 'escalated';
  metrics: RepairMetrics;
  attempts: RepairAttemptDto[];
  final_run_id: string | null;
  escalation: RepairEscalation | null;
}

export interface IntentResponse {
  stage: Stage;
  action: IntentAction;
  from_status: StageStatus;
  to_status: StageStatus;
  stale: Stage[];
  messages: MessageDto[];
  artifacts: ArtifactDto[];
  run_id: string;
}

// --- Requirements stage (phase-25 schema / phase-26 UI) ---

/** How a criterion is exercised: a unit test, an e2e flow, or (default) either. */
export type CriterionKind = 'unit' | 'e2e' | 'either';

/** Persisted acceptance criterion — `id` is the stable join key across requirements → tests. */
export interface CriterionDto {
  id: string;
  text: string;
  kind: CriterionKind;
}

export interface FeatureDto {
  name: string;
  description: string;
  inputs: string[];
  expected_behaviors: string[];
  acceptance_criteria: CriterionDto[];
}

/** A saved requirements spec version (backend GET /projects/{id}/requirements). */
export interface RequirementSpecDto {
  project_id: string;
  version: number;
  features: FeatureDto[];
  /** True when the test-gen agent inferred the spec because requirements were skipped (phase-27). */
  inferred?: boolean;
  created_at: string;
}

/** Request-body criterion — round-trip `id` to preserve it; omit/null for a new one. */
export interface CriterionInput {
  id?: string | null;
  text: string;
  kind: CriterionKind;
}

export interface FeatureInput {
  name: string;
  description: string;
  inputs: string[];
  expected_behaviors: string[];
  acceptance_criteria: CriterionInput[];
}

export interface RequirementSpecInput {
  features: FeatureInput[];
}

/** An editable AI proposal from the optional Haiku assist (never auto-applied). */
export interface SuggestedCriterion {
  text: string;
  kind: CriterionKind;
}

// --- Credentials (phase-34) ---

export type CredentialKind = 'vercel' | 'render' | 'mongo_uri' | 'stitch' | 'figma';

/** `byo` = this user's own token; `platform` = ForgeFlow's (never managed from the client). */
export type CredentialScope = 'platform' | 'byo';

/**
 * Credential **metadata**. There is deliberately no field for the value: the API has no endpoint
 * that returns a stored secret, so the client can never hold one (phase-34, §7).
 */
export interface CredentialDto {
  kind: CredentialKind;
  scope: CredentialScope;
  created_at: string;
  /** Last four characters, so two tokens can be told apart without revealing either. */
  last4: string | null;
}

// --- Deploy (phase-37 record / phase-38 topology) ---

export type DeployMode = 'seamless' | 'byo';

/** Overall deployment health: everything live, some target down, or nothing usable. */
export type DeploymentStatus = 'live' | 'degraded' | 'failed' | 'pending';

export type TopologyNodeId = 'fe' | 'be' | 'db';

/** One box in the graph, as the orchestrator recorded it. Carries a URL, never env values. */
export interface TopologyNodeDto {
  id: TopologyNodeId;
  kind: 'frontend' | 'backend' | 'database';
  provider: string;
  url?: string | null;
  status: string;
}

/** A wiring edge; `label` is the env **key** that carries the target's URL/URI (never its value). */
export interface TopologyEdgeDto {
  source: TopologyNodeId;
  target: TopologyNodeId;
  label?: string;
}

export interface TopologySnapshotDto {
  nodes: TopologyNodeDto[];
  edges: TopologyEdgeDto[];
  status?: string;
}

/**
 * A persisted deployment (backend GET /projects/{id}/deploy/latest). There is deliberately no env
 * field: a deployment records URLs and shape, and secrets never leave the control plane (§7).
 */
export interface DeploymentDto {
  id: string;
  mode: DeployMode;
  status: DeploymentStatus;
  fe_target: string | null;
  be_target: string | null;
  db_target: string | null;
  urls: Partial<Record<TopologyNodeId, string>>;
  topology_snapshot: TopologySnapshotDto;
  created_at: string;
}

export interface DeployLogsDto {
  deployment_id: string;
  log: string;
}

// --- Live validation (phase-40) ---

export type ValidationOutcome = 'validated' | 'escalated';

/** Why validation stopped short of a verified deployment. */
export type ValidationReason = 'cycle_cap' | 'env_config' | 'repair_escalated' | 'redeploy_failed';

/** How a live failure was classified: a bug the repair loop can fix, or an env/config problem. */
export type LiveDiagnosis = 'code' | 'env';

/** One pass of the outer validate→repair→redeploy→re-validate loop. */
export interface ValidationCycleDto {
  index: number;
  live_run_id: string | null;
  failing: number;
  green: boolean;
  diagnosis: LiveDiagnosis | null;
  repair_outcome: string | null;
  redeploy_status: string | null;
  note: string | null;
}

export interface ValidationReportDto {
  outcome: ValidationOutcome;
  /** The final live link — the whole point of M4. */
  url: string | null;
  cycles: ValidationCycleDto[];
  live_run_id: string | null;
  reason: ValidationReason | null;
  summary: string;
  failing_tests: TestResultDto[];
  /** Present only when the *inner* repair loop escalated, so its UI can be reused as-is. */
  repair_escalation: RepairEscalation | null;
  wall_clock_s: number;
}

/** The newest run made against the deployed URL (never a sandbox run). */
export interface LiveRunDto {
  id: string;
  total: number;
  passed: number;
  failed: number;
  green: boolean;
  created_at: string;
  results: TestResultDto[];
}
