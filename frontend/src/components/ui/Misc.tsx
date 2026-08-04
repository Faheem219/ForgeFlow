import { useId } from 'react';
import type { ReactNode } from 'react';

import { cn } from '../../lib/cn';

/** Keyboard-key chip, for shortcut hints (`⌘K`, `Esc`). */
export function Kbd({ children }: { children: ReactNode }): JSX.Element {
  return (
    <kbd className="inline-flex min-w-[1.4rem] items-center justify-center rounded border border-edge-strong bg-surface-raised px-1.5 py-0.5 font-sans text-[11px] font-medium text-fg-subtle shadow-[inset_0_-1px_0_rgb(var(--ff-edge-strong))]">
      {children}
    </kbd>
  );
}

/** Loading placeholder block. Size it with width/height utilities where it is used. */
export function Skeleton({ className }: { className?: string }): JSX.Element {
  return (
    <span
      aria-hidden
      className={cn('block animate-pulse rounded-md bg-surface-raised', className)}
    />
  );
}

/**
 * Minimal hover/focus tooltip. CSS-driven (group-hover/group-focus) rather than a positioning
 * library — content is short labels, and the trigger wraps its child inline.
 */
export function Tooltip({
  label,
  side = 'top',
  children,
}: {
  label: string;
  side?: 'top' | 'bottom';
  children: ReactNode;
}): JSX.Element {
  const id = useId();
  return (
    <span className="group relative inline-flex" aria-describedby={id}>
      {children}
      <span
        id={id}
        role="tooltip"
        className={cn(
          'pointer-events-none absolute left-1/2 z-40 -translate-x-1/2 whitespace-nowrap rounded-md',
          'border border-edge bg-surface-overlay px-2 py-1 text-xs text-fg shadow-lg',
          'opacity-0 transition-opacity duration-100 group-hover:opacity-100 group-focus-within:opacity-100',
          side === 'top' ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
        )}
      >
        {label}
      </span>
    </span>
  );
}

export interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
}

/** Compact mutually-exclusive switch (view modes, filters) — quieter than a row of Buttons. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
}): JSX.Element {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className="inline-flex items-center gap-0.5 rounded-lg border border-edge bg-surface-sunken p-0.5"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          onClick={() => onChange(option.value)}
          className={cn(
            'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
            option.value === value
              ? 'bg-surface text-fg shadow-sm'
              : 'text-fg-subtle hover:text-fg-muted',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** Labeled figure for stat rows (cost, tests passed, tokens). */
export function Stat({
  label,
  value,
  detail,
  tone = 'default',
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: 'default' | 'success' | 'danger';
}): JSX.Element {
  return (
    <div className="rounded-xl border border-edge bg-surface px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-fg-faint">{label}</p>
      <p
        className={cn(
          'text-xl font-semibold tabular-nums',
          tone === 'success' && 'text-success',
          tone === 'danger' && 'text-danger',
          tone === 'default' && 'text-fg',
        )}
        data-testid={`stat-${label.toLowerCase().replace(/[^a-z]+/g, '-')}`}
      >
        {value}
      </p>
      {detail ? <p className="mt-0.5 text-[11px] text-fg-faint">{detail}</p> : null}
    </div>
  );
}
