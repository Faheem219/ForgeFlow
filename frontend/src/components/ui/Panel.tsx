import type { ReactNode } from 'react';

/**
 * A bounded region of content. Elevation comes from the background step + hairline border defined
 * in `.panel` — not from a shadow, and never from a glow.
 *
 * `flush` drops the body padding for panels whose child owns its own layout (tables, editors,
 * terminals, graphs), which is most of the interesting ones.
 */
export function Panel({
  title,
  actions,
  description,
  children,
  className = '',
  bodyClassName = '',
  flush = false,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  flush?: boolean;
}): JSX.Element {
  const hasHeader = Boolean(title) || Boolean(actions);
  return (
    <section className={`panel ${className}`}>
      {hasHeader ? (
        <header className="flex min-h-[2.75rem] items-center justify-between gap-3 border-b border-subtle px-3.5 py-2">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-medium text-primary">{title}</h3>
            {description ? (
              <p className="mt-0.5 truncate text-xs text-muted">{description}</p>
            ) : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
        </header>
      ) : null}
      <div className={`${flush ? '' : 'p-3.5'} ${bodyClassName}`}>{children}</div>
    </section>
  );
}
