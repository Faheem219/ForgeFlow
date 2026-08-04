import type { ReactNode } from 'react';

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
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm rounded-2xl border border-edge bg-surface p-8 shadow-xl">
        <h1 className="mb-1 bg-gradient-to-r from-brand to-accent bg-clip-text text-2xl font-semibold text-transparent">
          ForgeFlow
        </h1>
        <h2 className="mb-6 text-sm text-fg-muted">{title}</h2>
        {children}
        {footer ? <p className="mt-6 text-center text-sm text-fg-muted">{footer}</p> : null}
      </div>
    </main>
  );
}

export function Field({
  label,
  ...props
}: { label: string } & React.InputHTMLAttributes<HTMLInputElement>): JSX.Element {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-fg-muted">{label}</span>
      <input
        className="w-full rounded-lg border border-edge-strong bg-surface-sunken px-3 py-2 text-fg outline-none focus:border-brand"
        {...props}
      />
    </label>
  );
}
