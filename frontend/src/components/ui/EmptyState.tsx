import type { ReactNode } from 'react';

import { cn } from '../../lib/cn';

/**
 * Nothing here yet. Deliberately frameless: an empty state usually sits inside a panel that already
 * has a border, and a dashed box inside a bordered box is chrome describing chrome. The icon stays
 * small and unboxed — it is a hint of what will appear, not a feature tile.
 */
export function EmptyState({
  title,
  description,
  icon,
  action,
  className,
}: {
  title: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <div
      className={cn('flex flex-col items-center justify-center px-6 py-10 text-center', className)}
    >
      {icon ? (
        <div className="mb-3 text-fg-faint [&_svg]:h-5 [&_svg]:w-5" aria-hidden>
          {icon}
        </div>
      ) : null}
      <p className="text-sm font-medium text-fg">{title}</p>
      {description ? (
        <p className="mt-1 max-w-xs text-xs leading-relaxed text-fg-subtle">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
