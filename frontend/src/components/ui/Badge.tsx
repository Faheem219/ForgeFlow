import type { ReactNode } from 'react';

import { cn } from '../../lib/cn';

type Tone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger';

/*
 * Tinted fill, no outline. A bordered pill on every status turns a dense screen into a wall of
 * capsules; the tint alone is enough to group the label, and the hue carries the meaning.
 */
const tones: Record<Tone, string> = {
  neutral: 'bg-fg/[0.07] text-fg-muted',
  brand: 'bg-brand/15 text-brand-text',
  success: 'bg-success/[0.13] text-success',
  warning: 'bg-warning/[0.13] text-warning',
  danger: 'bg-danger/[0.13] text-danger',
};

const dots: Record<Tone, string> = {
  neutral: 'bg-fg-faint',
  brand: 'bg-brand-text',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
};

export function Badge({
  tone = 'neutral',
  dot = false,
  className,
  children,
}: {
  tone?: Tone;
  /** A leading status dot — for live states (running, healthy) rather than categories. */
  dot?: boolean;
  className?: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <span
      className={cn(
        'inline-flex h-[18px] shrink-0 items-center gap-1 whitespace-nowrap rounded-[5px] px-1.5 text-2xs font-medium leading-none',
        tones[tone],
        className,
      )}
    >
      {dot ? <span aria-hidden className={cn('h-1.5 w-1.5 rounded-full', dots[tone])} /> : null}
      {children}
    </span>
  );
}
