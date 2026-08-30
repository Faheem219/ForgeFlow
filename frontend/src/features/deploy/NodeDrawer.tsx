import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { Badge, Button, EmptyState } from '../../components/ui';
import { cn } from '../../lib/cn';
import { toast } from '../../lib/stores/toastStore';
import { getDeployLogs, getProviderLogs } from './api';
import {
  NODE_STATUS_LABELS,
  NODE_STATUS_TONES,
  logLinesFor,
  type TopologyNodeView,
} from './topologyModel';

/** Which log the drawer is showing. Two genuinely different things, so it is a choice, not a merge. */
type LogSource = 'provider' | 'pipeline';

async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast({ title: 'URL copied', variant: 'success' });
  } catch {
    toast({ title: 'Could not copy', description: text, variant: 'error' });
  }
}

/**
 * One node's details: where it lives, what it was wired with, and what it logged.
 *
 * The env section lists **key names only**. That is not a display choice — a deployment record has
 * no values in it, so there is nothing here that could leak a token or a connection string (§7).
 * Since phase-62 those keys are also written to the provider *project*, so "set" is a claim about
 * the provider's own settings and not merely about what ForgeFlow intended.
 *
 * Logs come in two kinds and the drawer says which you are reading (phase-62). The **provider** log
 * is the vendor's build output — what a Vercel node's "Logs" tab always implied. The **pipeline**
 * log is ForgeFlow's own step record, and it is the only place that says which database mode was
 * chosen, why a target was skipped, or whether the env mirror landed.
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
  // The database is not a provider deployment, so it has only ever had the pipeline log.
  const canUseProvider = node.id !== 'db';
  const [source, setSource] = useState<LogSource>(canUseProvider ? 'provider' : 'pipeline');
  const showProvider = canUseProvider && source === 'provider';

  const logsQuery = useQuery({
    queryKey: ['deploy-logs', projectId],
    queryFn: () => getDeployLogs(projectId),
  });
  const providerQuery = useQuery({
    queryKey: ['deploy-provider-logs', projectId, node.id],
    queryFn: () => getProviderLogs(projectId, node.id),
    enabled: showProvider,
  });

  const pipelineLines = logLinesFor(node.id, logsQuery.data?.log ?? '');
  const providerLines = (providerQuery.data?.lines ?? []).map((line) => line.message);
  const providerError = providerQuery.data?.error ?? null;
  const lines = showProvider ? providerLines : pipelineLines;

  return (
    <aside
      data-testid="deploy-node-drawer"
      aria-label={`${node.label} details`}
      className="flex h-full min-h-0 w-full min-w-0 flex-col gap-3 overflow-auto rounded-xl border border-edge bg-surface p-4"
    >
      <header className="flex items-start justify-between gap-2">
        <div>
          <h4 className="text-sm font-medium text-fg">{node.label}</h4>
          <p className="text-xs text-fg-subtle">{node.provider}</p>
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
          className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger"
          data-testid="drawer-error"
        >
          {node.error}
        </p>
      ) : null}

      <section>
        <h5 className="mb-1 text-xs uppercase tracking-wide text-fg-subtle">URL</h5>
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
          <p className="text-sm text-fg-subtle">
            {node.kind === 'database' ? 'Reached privately by the backend.' : 'Not deployed yet.'}
          </p>
        )}
      </section>

      <section>
        <h5 className="mb-1 text-xs uppercase tracking-wide text-fg-subtle">Environment</h5>
        {node.envKeys.length === 0 ? (
          <p className="text-sm text-fg-subtle">Nothing wired in.</p>
        ) : (
          <ul className="space-y-1" data-testid="drawer-env">
            {node.envKeys.map((key) => (
              <li
                key={key}
                className="flex items-center justify-between rounded-lg border border-edge px-2 py-1 text-xs"
              >
                <code className="min-w-0 truncate text-fg-muted" title={key}>
                  {key}
                </code>
                <span className="text-fg-faint">set</span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-1 text-[11px] text-fg-faint">
          Values stay in the control plane and are never sent to the browser.
        </p>
      </section>

      <section className="min-h-0 min-w-0 flex-1">
        <div className="mb-1 flex items-center justify-between gap-2">
          <h5 className="text-xs uppercase tracking-wide text-fg-subtle">Logs</h5>
          {canUseProvider ? (
            <div className="flex rounded-md border border-edge p-0.5" role="group">
              {(
                [
                  ['provider', node.provider || 'Provider'],
                  ['pipeline', 'Pipeline'],
                ] as Array<[LogSource, string]>
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={source === value}
                  data-testid={`drawer-log-source-${value}`}
                  onClick={() => setSource(value)}
                  className={cn(
                    'rounded px-2 py-0.5 text-[11px] capitalize transition-colors',
                    source === value
                      ? 'bg-surface-raised text-fg'
                      : 'text-fg-subtle hover:text-fg-muted',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        {/* The provider's reason for having nothing, in its own words. Shown above whatever lines
            did arrive rather than instead of them: a partial log plus an explanation beats either
            alone. */}
        {showProvider && providerError ? (
          <p
            className="mb-1 rounded-lg border border-warning/30 bg-warning/10 px-2 py-1 text-[11px] text-warning"
            data-testid="drawer-provider-log-error"
          >
            {providerError}
          </p>
        ) : null}

        {lines.length === 0 ? (
          <EmptyState
            title={showProvider ? 'No provider log' : 'No logs yet'}
            description={
              showProvider
                ? "The provider returned nothing for this deployment. ForgeFlow's own pipeline log may still have the answer."
                : 'Deploy this project to see its log.'
            }
          />
        ) : (
          <pre
            data-testid="drawer-logs"
            className="max-h-64 w-full min-w-0 overflow-auto rounded-lg border border-edge bg-surface-sunken p-2 text-xs leading-relaxed text-fg-muted"
          >
            {lines.join('\n')}
          </pre>
        )}
      </section>

      <footer className="flex items-center justify-between gap-2 border-t border-edge pt-3">
        <span className="text-[11px] text-fg-faint">
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
