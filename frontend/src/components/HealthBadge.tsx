import { useEffect, useState } from 'react';

import { getHealth, type HealthResponse } from '../lib/apiClient';

type HealthState = { kind: 'loading' } | { kind: 'ok'; data: HealthResponse } | { kind: 'error' };

export function HealthBadge(): JSX.Element {
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

  const { color, label } = badgeProps(health);
  return (
    <div className="flex w-fit items-center gap-2 rounded-full border border-edge bg-surface px-4 py-2">
      <span className={`h-2.5 w-2.5 rounded-full ${color}`} aria-hidden />
      <span className="text-sm text-fg-muted" data-testid="health-status">
        {label}
      </span>
    </div>
  );
}

function badgeProps(state: HealthState): { color: string; label: string } {
  switch (state.kind) {
    case 'ok':
      return {
        color: 'bg-success',
        label: `Backend healthy · ${state.data.env} · v${state.data.version}`,
      };
    case 'error':
      return { color: 'bg-danger', label: 'Backend unreachable' };
    default:
      return { color: 'bg-warning animate-pulse', label: 'Checking backend…' };
  }
}
