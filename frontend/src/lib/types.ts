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

export interface IntentRequest {
  stage: Stage;
  action: IntentAction;
  message?: string;
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
