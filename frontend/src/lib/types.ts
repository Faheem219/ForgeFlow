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
