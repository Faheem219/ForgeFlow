/**
 * Filter-bar model (phase-42) — pure, so the translation to a Mongo filter is testable on its own.
 *
 * The UI deliberately offers a small, safe vocabulary rather than a raw query box: every operator
 * here is on the backend's allowlist, so an ordinary use of the filter bar can never trip a
 * guardrail. (The backend still validates — this just means the UI does not lead people into
 * errors.)
 */

export type FilterOp = 'eq' | 'ne' | 'contains' | 'gt' | 'gte' | 'lt' | 'lte' | 'exists';

export const FILTER_OPS: { value: FilterOp; label: string }[] = [
  { value: 'eq', label: 'equals' },
  { value: 'ne', label: 'not equals' },
  { value: 'contains', label: 'contains' },
  { value: 'gt', label: '>' },
  { value: 'gte', label: '≥' },
  { value: 'lt', label: '<' },
  { value: 'lte', label: '≤' },
  { value: 'exists', label: 'exists' },
];

export interface FilterDraft {
  field: string;
  op: FilterOp;
  value: string;
}

export const EMPTY_FILTER: FilterDraft = { field: '', op: 'eq', value: '' };

/** Escape a user's text so "contains" searches for it literally, not as a regex. */
function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Coerce a typed value: JSON when it parses (numbers, booleans, null), otherwise the raw string.
 * That lets `done` / `false` behave as people expect without asking them to think about types.
 */
export function coerceValue(raw: string): unknown {
  const trimmed = raw.trim();
  if (trimmed === '') return '';
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return raw;
  }
}

/** Translate the draft into a Mongo filter, or `null` when there is nothing to filter by. */
export function buildFilter(draft: FilterDraft): Record<string, unknown> | null {
  const field = draft.field.trim();
  if (!field) return null;

  if (draft.op === 'exists') {
    // "exists" is the one operator with no value — treat a blank/true value as "present".
    return { [field]: { $exists: coerceValue(draft.value) !== false } };
  }
  if (draft.value.trim() === '') return null;

  const value = coerceValue(draft.value);
  switch (draft.op) {
    case 'eq':
      return { [field]: value };
    case 'ne':
      return { [field]: { $ne: value } };
    case 'contains':
      return { [field]: { $regex: escapeRegex(String(value)), $options: 'i' } };
    default:
      return { [field]: { [`$${draft.op}`]: value } };
  }
}
