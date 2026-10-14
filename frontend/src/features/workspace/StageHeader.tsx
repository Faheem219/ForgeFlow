import type { ReactNode } from 'react';

/**
 * The one header every stage panel uses: what this is and where it stands on the left, what you can
 * do about it on the right. Each stage used to lay this row out by hand, so the status badge, the
 * live-progress note and the primary action all moved a little from one stage to the next.
 */
export function StageHeader({
  title,
  status,
  meta,
  actions,
}: {
  title: ReactNode;
  /** The stage status badge (wrapped by the caller in its own test id). */
  status?: ReactNode;
  /** Secondary facts: a live progress note, a provider, a scope. */
  meta?: ReactNode;
  actions?: ReactNode;
}): JSX.Element {
  return (
    // The left group truncates instead of pushing the actions onto a second row (`flex-1` has a zero
    // basis, so only the actions' own width can force a wrap — and `ml-auto` keeps them right-aligned
    // if a very narrow pane does).
    <header className="flex min-h-[2.25rem] shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5">
      <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
        <h2 className="shrink-0 text-[13px] font-semibold text-fg">{title}</h2>
        {status}
        {meta}
      </div>
      {actions ? (
        <div className="ml-auto flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          {actions}
        </div>
      ) : null}
    </header>
  );
}

/** A small section label inside a stage panel — sentence case, quiet, no box. */
export function SectionLabel({
  children,
  aside,
}: {
  children: ReactNode;
  aside?: ReactNode;
}): JSX.Element {
  return (
    <div className="mb-2 flex items-center justify-between gap-2">
      <h3 className="text-xs font-medium text-fg-muted">{children}</h3>
      {aside}
    </div>
  );
}
