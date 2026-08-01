import type { ReactNode } from 'react';

import { fieldClasses } from '../../components/ui';
import { ThemeToggleButton } from '../../app/layout/ThemeToggle';

/**
 * Sign-in / sign-up frame.
 *
 * The one place the product is allowed a little atmosphere — but it stays a *background*: a faint
 * grid that fades out behind the card, not a gradient blob. The card itself is the same panel
 * chrome as the rest of the app, so the first screen a user sees is honest about the tool behind it.
 */
export function AuthShell({
  title,
  children,
  footer,
}: {
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}): JSX.Element {
  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-canvas p-6">
      {/* Background grid, masked to fade toward the edges. Purely decorative. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.55]"
        style={{
          backgroundImage:
            'linear-gradient(rgb(var(--border-subtle)) 1px, transparent 1px), linear-gradient(90deg, rgb(var(--border-subtle)) 1px, transparent 1px)',
          backgroundSize: '44px 44px',
          maskImage: 'radial-gradient(ellipse 70% 55% at 50% 45%, black, transparent)',
          WebkitMaskImage: 'radial-gradient(ellipse 70% 55% at 50% 45%, black, transparent)',
        }}
      />

      <div className="absolute right-4 top-4">
        <ThemeToggleButton />
      </div>

      <div className="panel relative w-full max-w-[22rem] animate-slide-up p-7 shadow-overlay">
        <div className="mb-6 flex items-center gap-2.5">
          <span
            className="grid h-7 w-7 place-items-center rounded-md bg-brand text-xs font-bold text-brand-fg"
            aria-hidden
          >
            FF
          </span>
          <div>
            <h1 className="text-sm font-semibold tracking-tight text-primary">ForgeFlow</h1>
            <h2 className="text-xs text-muted">{title}</h2>
          </div>
        </div>
        {children}
        {footer ? <p className="mt-5 text-center text-xs text-muted">{footer}</p> : null}
      </div>
    </main>
  );
}

export function Field({
  label,
  ...props
}: { label: string } & React.InputHTMLAttributes<HTMLInputElement>): JSX.Element {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-secondary">{label}</span>
      <input className={`${fieldClasses} h-9 px-2.5 text-sm`} {...props} />
    </label>
  );
}
