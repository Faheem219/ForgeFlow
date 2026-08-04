// Admin dashboard DTOs — mirrored from the phase-51 config API (backend app/core/config_admin.py
// and app/core/cost_router.py). Kept local to the admin feature to avoid bloating shared types.

export type SettingType = 'str' | 'int' | 'float' | 'bool' | 'enum' | 'json';
export type SettingSource = 'db' | 'env' | 'default';

/** One admin-editable setting: its metadata, effective value, and where that value comes from. */
export interface SettingView {
  key: string;
  /** Human label, e.g. `openai_base_url` → "OpenAI base URL". */
  label: string;
  /** The environment variable this key falls back to when there is no admin override. */
  env_var: string;
  category: string;
  type: SettingType;
  description: string;
  sensitive: boolean;
  restart_required: boolean;
  /** Read-only: an override is impossible or unsafe (see `locked_reason`). */
  locked?: boolean;
  locked_reason?: string;
  choices: string[];
  minimum?: number | null;
  maximum?: number | null;
  nullable?: boolean;
  /** Effective value (masked to "••••••" when sensitive). */
  value: unknown;
  default: unknown;
  source: SettingSource;
}

/** Result of saving several keys at once: what landed, and why anything else did not. */
export interface BulkResult {
  updated: SettingView[];
  errors: Record<string, string>;
}

export interface ConfigAudit {
  key: string;
  action: 'update' | 'delete';
  before: unknown;
  after: unknown;
  updated_by: string | null;
  created_at: string;
}

export interface Budget {
  spent_inr: number;
  cap_inr: number | null;
  headroom_inr: number | null;
  used_ratio: number | null;
  warn_ratio: number;
  warning: boolean;
  halted: boolean;
}

export interface GlobalCost {
  inr: number;
  tokens: number;
  runs: number;
  projects: number;
  top_projects: Array<Record<string, unknown>>;
  budget: Budget;
}

// The sidebar sections, in order. Config sections map 1:1 to a registry `category` (every
// `Settings` field belongs to exactly one); the last two are read-only panels.
export type AdminSection =
  | 'models'
  | 'agents'
  | 'budget'
  | 'providers'
  | 'sandbox'
  | 'preview'
  | 'testing'
  | 'repair'
  | 'deploy'
  | 'storage'
  | 'database'
  | 'realtime'
  | 'auth'
  | 'core'
  | 'feature_flags'
  | 'observability'
  | 'audit';

/** Sidebar grouping — 17 sections read as a wall; four themes read as a map. */
export type AdminGroup = 'Intelligence' | 'Pipeline' | 'Platform' | 'Insights';

export interface AdminSectionMeta {
  id: AdminSection;
  label: string;
  group: AdminGroup;
  /** One line under the section title: what an operator changes here. */
  blurb: string;
}

export const ADMIN_SECTIONS: AdminSectionMeta[] = [
  {
    id: 'models',
    label: 'Models & provider',
    group: 'Intelligence',
    blurb:
      'Which LLM backend the agents talk to, the model ids they use, API keys and endpoint URLs. ' +
      'Switch between Anthropic and any OpenAI-compatible endpoint here.',
  },
  {
    id: 'agents',
    label: 'Agent loop',
    group: 'Intelligence',
    blurb: 'The scaffold agents build from, and how much context and output they may carry.',
  },
  {
    id: 'budget',
    label: 'Budget & pricing',
    group: 'Intelligence',
    blurb: 'Spend caps, the warning threshold, and per-model ₹/token pricing.',
  },
  {
    id: 'providers',
    label: 'Design providers',
    group: 'Pipeline',
    blurb:
      'The active design backend (Stitch / Figma / fake), its credentials, and the fallback order.',
  },
  {
    id: 'sandbox',
    label: 'Sandbox',
    group: 'Pipeline',
    blurb: 'Resource limits and execution rules for the containers generated code runs in.',
  },
  {
    id: 'preview',
    label: 'Live preview',
    group: 'Pipeline',
    blurb:
      'Dev-server commands, ports, health probes and the docker networks preview traffic uses.',
  },
  {
    id: 'testing',
    label: 'Testing & validation',
    group: 'Pipeline',
    blurb:
      'Suite timeouts, which E2E subset runs against production, and warm-up for sleeping hosts.',
  },
  {
    id: 'repair',
    label: 'Repair loop',
    group: 'Pipeline',
    blurb:
      'The bounds that keep self-healing finite: iteration cap, stall threshold, context budget.',
  },
  {
    id: 'deploy',
    label: 'Deploy',
    group: 'Pipeline',
    blurb: 'Vercel/Render credentials and endpoints, credential mode, and the infra analyzer.',
  },
  {
    id: 'storage',
    label: 'Artifacts & blobs',
    group: 'Platform',
    blurb: 'Where oversized artifact payloads live and when they stop being stored inline.',
  },
  {
    id: 'database',
    label: 'Database',
    group: 'Platform',
    blurb: 'Control-plane Mongo wiring and the cluster that hosts generated apps’ data.',
  },
  {
    id: 'realtime',
    label: 'Realtime',
    group: 'Platform',
    blurb: 'WebSocket replay buffer and heartbeat.',
  },
  {
    id: 'auth',
    label: 'Auth & limits',
    group: 'Platform',
    blurb: 'Session lifetime, rate limits, request size caps and seeded accounts.',
  },
  {
    id: 'core',
    label: 'Core',
    group: 'Platform',
    blurb: 'Environment label, version, CORS origins, logging and the vault encryption key.',
  },
  {
    id: 'feature_flags',
    label: 'Feature flags',
    group: 'Platform',
    blurb: 'Platform-wide toggles.',
  },
  {
    id: 'observability',
    label: 'Observability',
    group: 'Insights',
    blurb: 'Platform-wide spend against the configured caps.',
  },
  {
    id: 'audit',
    label: 'Audit log',
    group: 'Insights',
    blurb: 'Every config change: who, what, when, and the value before and after.',
  },
];

export const ADMIN_GROUPS: AdminGroup[] = ['Intelligence', 'Pipeline', 'Platform', 'Insights'];

/** Sections that render a config catalog (everything except the two read-only panels). */
export const CONFIG_SECTIONS: AdminSection[] = ADMIN_SECTIONS.filter(
  (s) => s.group !== 'Insights',
).map((s) => s.id);
