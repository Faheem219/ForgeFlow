import type { ReactNode } from 'react';

import { cn } from '../../lib/cn';

export function Panel({
  title,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}): JSX.Element {
  const hasHeader = Boolean(title) || Boolean(actions);
  return (
    <section className={cn('rounded-xl border border-edge bg-surface', className)}>
      {hasHeader ? (
        <header className="flex min-h-[2.75rem] items-center justify-between gap-3 border-b border-edge px-4 py-2">
          <h3 className="text-sm font-medium text-fg">{title}</h3>
          {actions}
        </header>
      ) : null}
      <div className="p-4">{children}</div>
    </section>
  );
}
