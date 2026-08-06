import { useRef } from 'react';
import type { HTMLAttributes, ReactNode } from 'react';

import { cn } from '../../lib/cn';

export interface SpotlightCardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

/**
 * Card wrapper whose surface carries a soft brand glow that follows the cursor. Pure CSS custom
 * properties driven by pointermove — no animation library, no rerenders, nothing on touch
 * devices (where hover never fires, so the overlay simply stays at opacity 0).
 *
 * Give it the card's radius (e.g. `rounded-xl`) so the overlay clips with `rounded-[inherit]`.
 */
export function SpotlightCard({ className, children, ...props }: SpotlightCardProps): JSX.Element {
  const ref = useRef<HTMLDivElement | null>(null);

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>): void {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    el.style.setProperty('--spot-x', `${event.clientX - rect.left}px`);
    el.style.setProperty('--spot-y', `${event.clientY - rect.top}px`);
  }

  return (
    <div
      {...props}
      ref={ref}
      onPointerMove={onPointerMove}
      className={cn('group/spot relative', className)}
    >
      {children}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 rounded-[inherit] opacity-0 transition-opacity duration-300 group-hover/spot:opacity-100"
        style={{
          background:
            'radial-gradient(220px circle at var(--spot-x, 50%) var(--spot-y, 50%), rgb(var(--ff-brand) / 0.1), transparent 70%)',
        }}
      />
    </div>
  );
}
