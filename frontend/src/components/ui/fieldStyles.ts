/**
 * Shared classes for text-entry controls (input/textarea/select), so ad-hoc fields in feature
 * code render identically to the Input primitive instead of drifting per file.
 */
export const fieldClasses =
  'w-full rounded-lg border border-edge-strong bg-surface px-3 py-2 text-sm text-fg ' +
  'placeholder:text-fg-faint outline-none transition-colors ' +
  'hover:border-fg-faint focus:border-brand focus:ring-1 focus:ring-brand ' +
  'disabled:cursor-not-allowed disabled:opacity-60';
