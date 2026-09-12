// Shared control-plane DTOs mirrored from the FastAPI backend (see backend/app/**).

export type Stage = 'design' | 'requirements' | 'build' | 'test' | 'deploy' | 'validate';

export type StageStatus =
  'empty' | 'in_progress' | 'awaiting_user' | 'complete' | 'skipped' | 'stale';

/**
 * `unskip` is the inverse of `skip` — it restores the status a stage held before it was skipped,
 * without re-running the stage (backend app/orchestrator/conductor.py).
 */
export type IntentAction = 'refine' | 'proceed' | 'skip' | 'unskip';

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
  /**
   * Set when the dev servers are healthy but the URLs still cannot load — today: the reverse proxy
   * serving `*.preview.localhost` is not running, so the iframe shows the browser's own
   * "refused to connect" and blames the generated app for an infrastructure problem.
   */
  warning?: string | null;
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

/**
 * A design provider waiting on a decision before it will design (Stitch answers a broad brief
 * with a scope proposal as often as it answers with a screen). `null` when nothing is waiting.
 */
export interface DesignQuestionDto {
  id: string;
  provider: string;
  question: string;
  /** Quick replies the provider offered — one click sends one as the answer. */
  suggestions: string[];
  /** `text` (a generate) or `refine` — what the answer resumes. */
  source: string;
  created_at: string;
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

/**
 * Where a run was executed: the sandbox preview, or the deployed URL (live validation, phase-39).
 * Both share one collection, so a reader that means one of them has to say which.
 */
export type TestRunEnv = 'sandbox' | 'live';

/** What the run *list* endpoint returns — counts only, no per-test results. */
export interface TestRunSummaryDto {
  id: string;
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  green: boolean;
  env: TestRunEnv;
  created_at: string;
}

/** One run in full — only the single-run endpoints (`/tests/run`, `/tests/runs/{id}`) return this. */
export interface TestRunDto extends TestRunSummaryDto {
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
  /** Attempts that wrote no file at all (phase-63); absent on reports stored before it. */
  noop_attempts?: number;
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
  /** The loop undid this patch: it broke a passing test and fixed nothing (the diff still reads). */
  reverted?: boolean;
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
  /** Editable product name for the app; blank falls back to the project's own name. */
  app_name: string;
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
  /** Omit or leave blank to keep whatever name is already saved. */
  app_name?: string;
  features: FeatureInput[];
}

/** A whole drafted spec: the proposed features plus a name for the app itself. */
export interface DraftedSpec {
  app_name: string;
  features: DraftedFeature[];
}

/** An editable AI proposal from the optional Haiku assist (never auto-applied). */
export interface SuggestedCriterion {
  text: string;
  kind: CriterionKind;
}

/**
 * One feature of an AI-drafted spec (POST /projects/{id}/requirements/draft). Criterion `id`s are
 * absent by design: a draft is unsaved, so nothing has a stable join key until the user saves.
 */
export interface DraftedFeature {
  name: string;
  description: string;
  inputs: string[];
  expected_behaviors: string[];
  acceptance_criteria: SuggestedCriterion[];
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
export type DeploymentStatus = 'live' | 'degraded' | 'failed' | 'pending' | 'deleted';

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

/** One line of a provider's own build log (phase-62). */
export interface ProviderLogLineDto {
  message: string;
  ts: string | null;
}

/**
 * A target's log as the **provider** tells it (backend GET .../logs/{target}).
 *
 * Total by design: a node with no provider deployment, a record predating ref persistence, a
 * revoked token and a deployment deleted in the provider's dashboard all come back as an empty
 * `lines` plus an `error` sentence — never a failed request, because a log fetch must not be able
 * to break the topology panel.
 */
export interface ProviderLogsDto {
  deployment_id: string;
  target: string;
  provider: string | null;
  lines: ProviderLogLineDto[];
  error: string | null;
}

/** Deploy-shape facts the UI cannot infer (phase-62): which credentials a BYO deploy needs. */
export interface DeployConfigDto {
  be_provider: string;
  byo_required_credentials: CredentialKind[];
  byo_optional_credentials: CredentialKind[];
}

/** Outcome of taking a deployment down. `warnings` name provider resources that outlived it. */
export interface DeployDeleteDto {
  deployment_id: string;
  destroyed: string[];
  record_deleted: boolean;
  warnings: string[];
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

// --- Data browser (phase-41 API / phase-42 UI) ---

/** Where a project's data lives. There is deliberately no URI field — it is a secret (§7). */
export interface DbInfoDto {
  mode: 'platform' | 'byo';
  db_name: string;
  managed: boolean;
}

export interface CollectionDto {
  name: string;
  count: number;
}

/** A document from a generated app — schema-agnostic, so an open record is the honest type. */
export type DataDocument = Record<string, unknown>;

export interface DocumentPageDto {
  documents: DataDocument[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

// --- Evaluation harness (phase-44 records / phase-45 dashboard) ---

export type EvalOutcome = 'delivered' | 'escalated' | 'failed';

/** Passing criteria out of the total. `rate` is null when nothing ran — never a fabricated 0. */
export interface EvalPassRate {
  total: number;
  passed: number;
  failed: number;
  rate: number | null;
}

/** One spec's run, as phase-44's runner recorded it. */
export interface EvalRecordDto {
  spec_id: string;
  title: string;
  difficulty: string;
  outcome: EvalOutcome;
  first_pass: EvalPassRate;
  post_repair: EvalPassRate;
  repair_delta: number | null;
  repaired?: boolean;
  repair_iterations: number;
  repair_outcome: string | null;
  regressions: number;
  tokens: number;
  inr_cost: number;
  screenshot_to_url_seconds: number | null;
  wall_clock_seconds: number;
  live_url: string | null;
  error: string | null;
}

/** A numeric summary; every field is null when nothing was measured. */
export interface EvalDistribution {
  count: number;
  mean: number | null;
  median: number | null;
  min: number | null;
  max: number | null;
  values: number[];
}

export interface EvalSummary {
  specs: number;
  scored: number;
  outcomes: Record<EvalOutcome, number>;
  first_pass: EvalDistribution;
  post_repair: EvalDistribution;
  repair_delta: EvalDistribution;
  repair_iterations: EvalDistribution;
  tokens: EvalDistribution;
  inr_cost: EvalDistribution;
  screenshot_to_url_seconds: EvalDistribution;
  wall_clock_seconds: EvalDistribution;
  specs_improved_by_repair: number;
  specs_unchanged_by_repair: number;
  green_first_pass: number;
  green_post_repair: number;
  total_regressions: number;
  by_difficulty: Record<
    string,
    { specs: number; first_pass_mean: number | null; post_repair_mean: number | null }
  >;
}

/** `available: false` before the harness has ever run. */
export interface EvalSummaryDto {
  available: boolean;
  summary?: EvalSummary;
  records?: EvalRecordDto[];
  source?: string | null;
}

export interface EvalRunDto {
  source: string;
  started_at: string | null;
  finished_at: string | null;
  legs: string[];
  specs: number;
}

// --- Cost & observability (phase-46) ---

/** A cap of `null` means unlimited — headroom is then unknown, never 0. */
export interface BudgetDto {
  spent_inr: number;
  cap_inr: number | null;
  headroom_inr: number | null;
  used_ratio: number | null;
  warn_ratio: number;
  warning: boolean;
  halted: boolean;
}

export interface ProjectCostDto {
  project_id: string;
  tokens: number;
  inr: number;
  runs: number;
  by_kind: Record<string, { runs: number; tokens: number; inr: number }>;
  project: BudgetDto;
  global_budget: BudgetDto;
}

/** One costed unit of work. `duration_s` is null while a run is still open. */
export interface RunDto {
  id: string;
  kind: string;
  tokens: number;
  inr: number;
  outcome: string | null;
  tool_calls: number;
  started_at: string;
  finished_at: string | null;
  duration_s: number | null;
}
