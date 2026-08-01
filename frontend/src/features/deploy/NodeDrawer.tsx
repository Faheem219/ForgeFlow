import { useQuery } from '@tanstack/react-query';

import { Badge, Button, EmptyState } from '../../components/ui';
import { toast } from '../../lib/stores/toastStore';
import { getDeployLogs } from './api';
import {
  NODE_STATUS_LABELS,
  NODE_STATUS_TONES,
  logLinesFor,
  type TopologyNodeView,
} from './topologyModel';

async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast({ title: 'URL copied', variant: 'success' });
  } catch {
    toast({ title: 'Could not copy', description: text, variant: 'error' });
  }
}

/**
 * One node's details: where it lives, what it was wired with, and what the deploy logged for it.
 *
 * The env section lists **key names only**. That is not a display choice — a deployment record has
 * no values in it, so there is nothing here that could leak a token or a connection string (§7).
 */
export function NodeDrawer({
  projectId,
  node,
  onClose,
  onRedeploy,
  redeployDisabledReason,
  redeploying = false,
}: {
  projectId: string;
  node: TopologyNodeView;
  onClose: () => void;
  onRedeploy: () => void;
  redeployDisabledReason?: string | null;
  redeploying?: boolean;
}): JSX.Element {
  const logsQuery = useQuery({
    queryKey: ['deploy-logs', projectId],
    queryFn: () => getDeployLogs(projectId),
  });

  const lines = logLinesFor(node.id, logsQuery.data?.log ?? '');

  return (
    <aside
      data-testid="deploy-node-drawer"
      aria-label={`${node.label} details`}
      className="flex h-full min-h-0 w-full flex-col gap-3 overflow-auto rounded-xl border border-subtle bg-surface/40 p-4"
    >
      <header className="flex items-start justify-between gap-2">
        <div>
          <h4 className="text-sm font-medium text-primary">{node.label}</h4>
          <p className="text-xs text-faint">{node.provider}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={NODE_STATUS_TONES[node.liveStatus]}>
            {NODE_STATUS_LABELS[node.liveStatus]}
          </Badge>
          <Button size="sm" variant="secondary" onClick={onClose} data-testid="drawer-close">
            Close
          </Button>
        </div>
      </header>

      {node.error ? (
        <p
          className="rounded-lg border border-danger-border bg-danger-bg px-3 py-2 text-xs text-danger"
          data-testid="drawer-error"
        >
          {node.error}
        </p>
      ) : null}

      <section>
        <h5 className="mb-1 text-xs uppercase tracking-wide text-faint">URL</h5>
        {node.liveUrl ? (
          <div className="flex items-center gap-2">
            <a
              href={node.liveUrl}
              target="_blank"
              rel="noreferrer"
              data-testid="drawer-url"
              className="truncate text-sm text-brand-text hover:underline"
            >
              {node.liveUrl}
            </a>
            <Button
              size="sm"
              variant="secondary"
              data-testid="drawer-copy-url"
              onClick={() => void copy(node.liveUrl as string)}
            >
              Copy
            </Button>
          </div>
        ) : (
          <p className="text-sm text-faint">
            {node.kind === 'database' ? 'Reached privately by the backend.' : 'Not deployed yet.'}
          </p>
        )}
      </section>

      <section>
        <h5 className="mb-1 text-xs uppercase tracking-wide text-faint">Environment</h5>
        {node.envKeys.length === 0 ? (
          <p className="text-sm text-faint">Nothing wired in.</p>
        ) : (
          <ul className="space-y-1" data-testid="drawer-env">
            {node.envKeys.map((key) => (
              <li
                key={key}
                className="flex items-center justify-between rounded-lg border border-subtle px-2 py-1 text-xs"
              >
                <code className="text-secondary">{key}</code>
                <span className="text-faint">set</span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-1 text-[11px] text-faint">
          Values stay in the control plane and are never sent to the browser.
        </p>
      </section>

      <section className="min-h-0 flex-1">
        <h5 className="mb-1 text-xs uppercase tracking-wide text-faint">Logs</h5>
        {lines.length === 0 ? (
          <EmptyState title="No logs yet" description="Deploy this project to see its log." />
        ) : (
          <pre
            data-testid="drawer-logs"
            className="max-h-48 overflow-auto rounded-lg border border-subtle bg-inset p-2 text-xs text-secondary"
          >
            {lines.join('\n')}
          </pre>
        )}
      </section>

      <footer className="flex items-center justify-between gap-2 border-t border-subtle pt-3">
        <span className="text-[11px] text-faint">
          Redeploy re-runs the pipeline (backend, then frontend).
        </span>
        <Button
          size="sm"
          data-testid="drawer-redeploy"
          disabled={Boolean(redeployDisabledReason) || redeploying}
          title={redeployDisabledReason ?? undefined}
          onClick={onRedeploy}
        >
          {redeploying ? 'Redeploying…' : 'Redeploy'}
        </Button>
      </footer>
    </aside>
  );
}

export default NodeDrawer;
