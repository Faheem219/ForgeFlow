import { useQuery } from '@tanstack/react-query';
import { History } from 'lucide-react';

import { Badge, EmptyState, Skeleton } from '../../components/ui';
import { formatDateTimeIST } from '../../lib/datetime';
import { listAudit } from './api';
import type { ConfigAudit } from './types';

function value(v: unknown): string {
  if (v === null || v === undefined) return '—';
  return typeof v === 'string' ? v : JSON.stringify(v);
}

function Row({ entry }: { entry: ConfigAudit }): JSX.Element {
  return (
    <li className="rounded-lg border border-edge p-2 text-sm" data-testid="audit-row">
      <div className="flex flex-wrap items-center gap-2">
        <code className="text-fg">{entry.key}</code>
        <Badge tone={entry.action === 'delete' ? 'warning' : 'brand'}>{entry.action}</Badge>
        <span className="text-xs text-fg-subtle">{formatDateTimeIST(entry.created_at)}</span>
      </div>
      <p className="mt-1 font-mono text-xs text-fg-muted">
        <span className="text-danger">{value(entry.before)}</span>
        {' → '}
        <span className="text-success">{value(entry.after)}</span>
      </p>
      <p className="text-[11px] text-fg-faint">
        by {entry.updated_by ? entry.updated_by : 'system'}
      </p>
    </li>
  );
}

/** Read-only history of config changes — who/what/when, before→after (phase-51 audit log). */
export function AuditView(): JSX.Element {
  const query = useQuery({ queryKey: ['admin-audit'], queryFn: listAudit });

  if (query.isLoading) {
    return (
      <div className="space-y-2" aria-hidden>
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="space-y-1.5 rounded-xl border border-edge p-3">
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-3 w-72" />
          </div>
        ))}
      </div>
    );
  }
  const entries = query.data ?? [];
  if (entries.length === 0) {
    return (
      <EmptyState
        icon={<History aria-hidden className="h-6 w-6" strokeWidth={1.5} />}
        title="No changes yet"
        description="Config edits will be recorded here."
      />
    );
  }
  return (
    <ul className="space-y-2" data-testid="audit-list">
      {entries.map((entry, i) => (
        <Row key={`${entry.key}-${entry.created_at}-${i}`} entry={entry} />
      ))}
    </ul>
  );
}

export default AuditView;
