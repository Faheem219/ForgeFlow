// Admin dashboard DTOs — mirrored from the phase-51 config API (backend app/core/config_admin.py
// and app/core/cost_router.py). Kept local to the admin feature to avoid bloating shared types.

export type SettingType = 'str' | 'int' | 'float' | 'bool' | 'enum' | 'json';
export type SettingSource = 'db' | 'env' | 'default';

/** One admin-editable setting: its metadata, effective value, and where that value comes from. */
export interface SettingView {
  key: string;
  category: string;
  type: SettingType;
  description: string;
  sensitive: boolean;
  restart_required: boolean;
  choices: string[];
  /** Effective value (masked to "••••••" when sensitive). */
  value: unknown;
  default: unknown;
  source: SettingSource;
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

// The sidebar sections, in order. Config categories map to registry `category`; the last two are
// read-only panels.
export type AdminSection =
  | 'models'
  | 'providers'
  | 'deploy'
  | 'sandbox'
  | 'budget'
  | 'repair'
  | 'feature_flags'
  | 'observability'
  | 'audit';

export const ADMIN_SECTIONS: Array<{ id: AdminSection; label: string }> = [
  { id: 'models', label: 'Models' },
  { id: 'providers', label: 'Providers' },
  { id: 'deploy', label: 'Deploy' },
  { id: 'sandbox', label: 'Sandbox' },
  { id: 'budget', label: 'Budget & Pricing' },
  { id: 'repair', label: 'Repair Loop' },
  { id: 'feature_flags', label: 'Feature Flags' },
  { id: 'observability', label: 'Observability' },
  { id: 'audit', label: 'Audit' },
];

/** Sections that render a config catalog (everything except the two read-only panels). */
export const CONFIG_SECTIONS: AdminSection[] = [
  'models',
  'providers',
  'deploy',
  'sandbox',
  'budget',
  'repair',
  'feature_flags',
];
