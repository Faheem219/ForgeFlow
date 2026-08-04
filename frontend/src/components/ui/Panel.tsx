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
        <header className="flex items-center justify-between border-b border-edge px-4 py-3">
          <h3 className="text-sm font-medium text-fg">{title}</h3>
          {actions}
        </header>
      ) : null}
      <div className="p-4">{children}</div>
    </section>
  );
}
