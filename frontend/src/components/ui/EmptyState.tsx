import type { ReactNode } from 'react';

/**
 * Empty / zero state. Deliberately quiet: a dashed hairline, muted text, and — where one is given —
 * a single action. An empty state that shouts competes with the content that will replace it.
 */
export function EmptyState({
  title,
  description,
  icon,
  action,
  compact = false,
}: {
  title: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
  /** Tighter padding for empty states nested inside an already-small panel. */
  compact?: boolean;
}): JSX.Element {
  return (
    <div
      className={`flex animate-fade-in flex-col items-center justify-center rounded-lg border border-dashed border-line text-center ${
        compact ? 'px-4 py-6' : 'px-6 py-10'
      }`}
    >
      {icon ? <div className="mb-2.5 text-faint">{icon}</div> : null}
      <p className="text-sm font-medium text-secondary">{title}</p>
      {description ? <p className="mt-1 max-w-sm text-xs text-muted">{description}</p> : null}
      {action ? <div className="mt-3.5">{action}</div> : null}
    </div>
  );
}
