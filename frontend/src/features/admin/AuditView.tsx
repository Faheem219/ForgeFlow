import { useQuery } from '@tanstack/react-query';

import { Badge, EmptyState, Spinner } from '../../components/ui';
import { listAudit } from './api';
import type { ConfigAudit } from './types';

function value(v: unknown): string {
  if (v === null || v === undefined) return '—';
  return typeof v === 'string' ? v : JSON.stringify(v);
}

function Row({ entry }: { entry: ConfigAudit }): JSX.Element {
  return (
    <li className="rounded-lg border border-slate-800 p-2 text-sm" data-testid="audit-row">
      <div className="flex flex-wrap items-center gap-2">
        <code className="text-slate-100">{entry.key}</code>
        <Badge tone={entry.action === 'delete' ? 'warning' : 'brand'}>{entry.action}</Badge>
        <span className="text-xs text-slate-500">{new Date(entry.created_at).toLocaleString()}</span>
      </div>
      <p className="mt-1 font-mono text-xs text-slate-400">
        <span className="text-red-300">{value(entry.before)}</span>
        {' → '}
        <span className="text-emerald-300">{value(entry.after)}</span>
      </p>
      <p className="text-[11px] text-slate-600">
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
      <div className="flex justify-center py-6">
        <Spinner />
      </div>
    );
  }
  const entries = query.data ?? [];
  if (entries.length === 0) {
    return <EmptyState title="No changes yet" description="Config edits will be recorded here." />;
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
