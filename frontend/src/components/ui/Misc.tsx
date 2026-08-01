import type { ReactNode } from 'react';

/**
 * Small shared primitives that are too thin to deserve their own modules but too repeated to keep
 * inlining: keyboard hints, tooltips, skeletons, segmented controls, stat readouts, and the
 * indeterminate progress bar used by streaming tasks.
 */

/** A keyboard key. Used by the command palette and shortcut hints. */
export function Kbd({ children }: { children: ReactNode }): JSX.Element {
  return (
    <kbd className="inline-flex h-[1.125rem] min-w-[1.125rem] items-center justify-center rounded border border-subtle bg-elevated px-1 font-sans text-2xs font-medium text-muted">
      {children}
    </kbd>
  );
}

/**
 * CSS-only tooltip. No portal, no positioning library — it is a `::after`-style absolutely
 * positioned sibling that appears on hover/focus-within. Sufficient for chrome affordances, and it
 * cannot desync from its trigger the way a portalled tooltip can.
 */
export function Tooltip({
  label,
  side = 'bottom',
  children,
}: {
  label: string;
  side?: 'top' | 'bottom';
  children: ReactNode;
}): JSX.Element {
  const position = side === 'top' ? 'bottom-full mb-1.5' : 'top-full mt-1.5';
  return (
    <span className="group/tt relative inline-flex">
      {children}
      <span
        role="tooltip"
        className={`pointer-events-none absolute left-1/2 z-50 -translate-x-1/2 ${position} whitespace-nowrap rounded border border-line bg-overlay px-1.5 py-1 text-2xs text-secondary opacity-0 shadow-pop transition-opacity duration-fast ease-out group-hover/tt:opacity-100 group-focus-within/tt:opacity-100`}
      >
        {label}
      </span>
    </span>
  );
}

/** Loading placeholder. Shape-matched to the content it replaces, never a generic grey box. */
export function Skeleton({ className = '' }: { className?: string }): JSX.Element {
  return <div className={`skeleton ${className}`} aria-hidden />;
}

/** Several skeleton lines, for list/table placeholders. */
export function SkeletonRows({
  rows = 3,
  className = '',
}: {
  rows?: number;
  className?: string;
}): JSX.Element {
  return (
    <div className={`space-y-2 ${className}`} role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

export interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
  title?: string;
}

/**
 * Segmented control — for 2–4 mutually exclusive view modes where tabs would be too heavy
 * (theme mode, deploy mode, log source).
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  size = 'md',
  ariaLabel,
}: {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  size?: 'sm' | 'md';
  ariaLabel: string;
}): JSX.Element {
  const pad = size === 'sm' ? 'h-6 px-2 text-2xs' : 'h-7 px-2.5 text-xs';
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className="inline-flex items-center gap-0.5 rounded-md border border-subtle bg-inset p-0.5"
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            title={option.title}
            onClick={() => onChange(option.value)}
            className={`inline-flex items-center gap-1.5 rounded font-medium transition-colors duration-fast ease-out ${pad} ${
              selected ? 'bg-surface text-primary shadow-pop' : 'text-muted hover:text-secondary'
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * A labelled metric. Tabular numerals so a changing value never reflows its neighbours — which is
 * what makes a live cost/iteration readout feel stable instead of twitchy.
 */
export function Stat({
  label,
  value,
  hint,
  tone = 'default',
  testId,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: 'default' | 'success' | 'warning' | 'danger';
  testId?: string;
}): JSX.Element {
  const toneClass = {
    default: 'text-primary',
    success: 'text-success',
    warning: 'text-warning',
    danger: 'text-danger',
  }[tone];
  return (
    <div className="min-w-0">
      <dt className="eyebrow">{label}</dt>
      <dd className={`tabular mt-0.5 text-sm font-medium ${toneClass}`} data-testid={testId}>
        {value}
      </dd>
      {hint ? <p className="text-2xs text-faint">{hint}</p> : null}
    </div>
  );
}

/**
 * Indeterminate progress bar for work whose total is genuinely unknown (an agent turn). A drifting
 * segment rather than a fake percentage — inventing a percentage you cannot compute is a lie the
 * user eventually catches.
 */
export function ProgressBar({ label }: { label?: string }): JSX.Element {
  return (
    <div
      role="progressbar"
      aria-label={label ?? 'Working'}
      className="relative h-0.5 w-full overflow-hidden rounded-full bg-elevated"
    >
      <span className="motion-essential absolute inset-y-0 w-1/4 animate-bar-drift rounded-full bg-brand" />
    </div>
  );
}
