/**
 * Table model for the data browser (phase-42) — pure, and separate from the component so the
 * column/sort logic is testable (and so fast-refresh keeps working on the component itself).
 */

import type { DataDocument } from '../../lib/types';

export type SortState = { field: string; direction: 1 | -1 } | null;

/**
 * Columns are derived from the documents themselves — generated apps have no schema ForgeFlow knows
 * about, so the table shows whatever is actually there, with `_id` pinned first.
 */
export function columnsOf(documents: DataDocument[]): string[] {
  const seen = new Set<string>();
  for (const doc of documents) {
    for (const key of Object.keys(doc)) seen.add(key);
  }
  seen.delete('_id');
  return ['_id', ...Array.from(seen)];
}

/** Render a BSON-ish value compactly; objects and arrays collapse to JSON rather than sprawling. */
export function formatCell(value: unknown): string {
  if (value === null) return 'null';
  if (value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** Clicking a column cycles ascending → descending → unsorted. */
export function nextSort(current: SortState, field: string): SortState {
  if (!current || current.field !== field) return { field, direction: 1 };
  if (current.direction === 1) return { field, direction: -1 };
  return null;
}
