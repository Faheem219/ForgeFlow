/**
 * Shared classes for text-entry controls (input/textarea/select), so ad-hoc fields in feature
 * code render identically to the Input primitive instead of drifting per file.
 */
export const fieldClasses =
  'w-full rounded-lg border border-edge-strong bg-surface-sunken/40 px-2.5 py-1.5 text-sm text-fg ' +
  'placeholder:text-fg-faint outline-none transition-[border-color,box-shadow] duration-150 ' +
  'hover:border-fg-faint/60 focus:border-brand/70 focus:shadow-[0_0_0_3px_rgb(var(--ff-brand)/0.18)] ' +
  'disabled:cursor-not-allowed disabled:opacity-60';
