import type { ReactNode } from 'react';

export function Panel({
  title,
  actions,
  children,
  className = '',
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}): JSX.Element {
  const hasHeader = Boolean(title) || Boolean(actions);
  return (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/40 ${className}`}>
      {hasHeader ? (
        <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <h3 className="text-sm font-medium text-slate-200">{title}</h3>
          {actions}
        </header>
      ) : null}
      <div className="p-4">{children}</div>
    </section>
  );
}
