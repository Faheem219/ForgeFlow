import type { ReactNode } from 'react';

/**
 * Status pill. Each tone carries its own foreground/background/border token pair so contrast is
 * correct in BOTH themes — the common failure here is a dark-theme pill (`emerald-300` on a tinted
 * background) reused on white, where it becomes unreadable.
 *
 * `dot` prefixes a status dot for the cases where the label alone is ambiguous at a glance.
 */

type Tone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'info';

const tones: Record<Tone, string> = {
  neutral: 'bg-elevated text-muted border-subtle',
  brand: 'bg-brand-bg text-brand-text border-brand-border',
  success: 'bg-success-bg text-success border-success-border',
  warning: 'bg-warning-bg text-warning border-warning-border',
  danger: 'bg-danger-bg text-danger border-danger-border',
  info: 'bg-info-bg text-info border-info-border',
};

const dots: Record<Tone, string> = {
  neutral: 'bg-faint',
  brand: 'bg-brand',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-info',
};

export function Badge({
  tone = 'neutral',
  dot = false,
  pulse = false,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  /** Pulse the dot — reserved for genuinely live state (in-progress, streaming). */
  pulse?: boolean;
  children: ReactNode;
}): JSX.Element {
  return (
    <span
      // `shrink-0 whitespace-nowrap` is upstream's fix for badges being squashed in tight flex
      // rows; kept alongside the tokenised styling.
      className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-[0.1875rem] text-2xs font-medium ${tones[tone]}`}
    >
      {dot ? (
        <span
          aria-hidden
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${dots[tone]} ${
            pulse ? 'motion-essential animate-pulse-dot' : ''
          }`}
        />
      ) : null}
      {children}
    </span>
  );
}
