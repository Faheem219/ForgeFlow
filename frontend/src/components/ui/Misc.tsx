import { useId } from 'react';
import type { ReactNode } from 'react';

import { cn } from '../../lib/cn';

/** Keyboard-key chip, for shortcut hints (`⌘K`, `Esc`). */
export function Kbd({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <kbd
      className={cn(
        'inline-flex h-[18px] min-w-[18px] items-center justify-center rounded border border-edge bg-surface-raised px-1 font-sans text-2xs font-medium leading-none text-fg-subtle',
        className,
      )}
    >
      {children}
    </kbd>
  );
}

/** Loading placeholder block. Size it with width/height utilities where it is used. */
export function Skeleton({ className }: { className?: string }): JSX.Element {
  return (
    <span
      aria-hidden
      className={cn('block rounded-md bg-fg/[0.06] motion-safe:animate-pulse', className)}
    />
  );
}

/**
 * Minimal hover/focus tooltip. CSS-driven rather than a positioning library — content is short
 * labels, and the trigger wraps its child inline. It waits a beat before showing on hover (a toolbar
 * that flashes labels as the pointer crosses it is noise) but shows at once for keyboard focus.
 *
 * The group is *named* (`group/tip`): an unnamed `group` would also fire whenever any ancestor
 * that happens to be a `group` is hovered — a whole stage row lighting up every tooltip inside it.
 */
export function Tooltip({
  label,
  side = 'top',
  align = 'center',
  children,
  className,
}: {
  label: string;
  side?: 'top' | 'bottom' | 'right';
  align?: 'start' | 'center' | 'end';
  children: ReactNode;
  className?: string;
}): JSX.Element {
  const id = useId();
  return (
    <span className={cn('group/tip relative inline-flex', className)} aria-describedby={id}>
      {children}
      <span
        id={id}
        role="tooltip"
        className={cn(
          'pointer-events-none absolute z-50 whitespace-nowrap rounded-md bg-surface-overlay px-2 py-1 text-xs text-fg shadow-pop',
          'opacity-0 transition-opacity duration-100',
          'group-hover/tip:opacity-100 group-hover/tip:delay-500 group-focus-within/tip:opacity-100 group-focus-within/tip:delay-0',
          side === 'top' && 'bottom-full mb-1.5',
          side === 'bottom' && 'top-full mt-1.5',
          side === 'right' && 'left-full top-1/2 ml-2 -translate-y-1/2',
          side !== 'right' && align === 'center' && 'left-1/2 -translate-x-1/2',
          side !== 'right' && align === 'start' && 'left-0',
          side !== 'right' && align === 'end' && 'right-0',
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
      className="inline-flex items-center gap-0.5 rounded-lg bg-fg/[0.05] p-0.5"
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
              ? 'bg-surface text-fg shadow-raise'
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
    <div className="min-w-0 rounded-xl border border-edge bg-surface px-3.5 py-3">
      <p className="text-xs text-fg-subtle">{label}</p>
      <p
        className={cn(
          'mt-1 truncate text-xl font-semibold tabular-nums tracking-tight',
          tone === 'success' && 'text-success',
          tone === 'danger' && 'text-danger',
          tone === 'default' && 'text-fg',
        )}
        data-testid={`stat-${label.toLowerCase().replace(/[^a-z]+/g, '-')}`}
      >
        {value}
      </p>
      {detail ? <p className="mt-0.5 truncate text-2xs text-fg-subtle">{detail}</p> : null}
    </div>
  );
}
