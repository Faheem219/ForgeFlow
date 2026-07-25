import type { ReactNode } from 'react';

type Tone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger';

const tones: Record<Tone, string> = {
  neutral: 'bg-slate-800 text-slate-300',
  brand: 'bg-brand-600/20 text-brand-300',
  success: 'bg-emerald-600/20 text-emerald-300',
  warning: 'bg-amber-600/20 text-amber-300',
  danger: 'bg-red-600/20 text-red-300',
};

export function Badge({
  tone = 'neutral',
  children,
}: {
  tone?: Tone;
  children: ReactNode;
}): JSX.Element {
  return (
    <span
      className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
