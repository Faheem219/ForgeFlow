import { useEffect, useState } from 'react';

import { getHealth, type HealthResponse } from '../lib/apiClient';

type HealthState = { kind: 'loading' } | { kind: 'ok'; data: HealthResponse } | { kind: 'error' };

export default function App(): JSX.Element {
  const [health, setHealth] = useState<HealthState>({ kind: 'loading' });

  useEffect(() => {
    let active = true;
    getHealth()
      .then((data) => {
        if (active) setHealth({ kind: 'ok', data });
      })
      .catch(() => {
        if (active) setHealth({ kind: 'error' });
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
      <div className="text-center">
        <h1 className="bg-gradient-to-r from-brand-400 to-accent-400 bg-clip-text text-4xl font-semibold text-transparent">
          ForgeFlow
        </h1>
        <p className="mt-2 text-slate-400">
          Idea &rarr; design &rarr; build &rarr; test &amp; self-heal &rarr; deploy &rarr; live.
        </p>
      </div>

      <HealthBadge state={health} />
    </main>
  );
}

function HealthBadge({ state }: { state: HealthState }): JSX.Element {
  const { color, label } = badgeProps(state);
  return (
    <div className="flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900/60 px-4 py-2">
      <span className={`h-2.5 w-2.5 rounded-full ${color}`} aria-hidden />
      <span className="text-sm text-slate-300" data-testid="health-status">
        {label}
      </span>
    </div>
  );
}

function badgeProps(state: HealthState): { color: string; label: string } {
  switch (state.kind) {
    case 'ok':
      return {
        color: 'bg-emerald-400',
        label: `Backend healthy · ${state.data.env} · v${state.data.version}`,
      };
    case 'error':
      return { color: 'bg-red-500', label: 'Backend unreachable' };
    default:
      return { color: 'bg-amber-400 animate-pulse', label: 'Checking backend…' };
  }
}
