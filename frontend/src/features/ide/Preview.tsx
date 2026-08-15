import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { Badge, Button, EmptyState, Spinner } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import type { PreviewStatus } from '../../lib/types';
import { getPreview, previewAction } from './api';

const TONES: Record<PreviewStatus, 'neutral' | 'warning' | 'success' | 'danger'> = {
  stopped: 'neutral',
  starting: 'warning',
  running: 'success',
  failed: 'danger',
};

type LogTab = 'frontend' | 'backend';

export function Preview({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  // Bumping this key remounts the iframe — the only reliable cross-origin way to reload it.
  const [reloadKey, setReloadKey] = useState(0);
  const [logsOpen, setLogsOpen] = useState(false);
  const [logTab, setLogTab] = useState<LogTab>('frontend');

  const statusQuery = useQuery({
    queryKey: ['preview', projectId],
    queryFn: () => getPreview(projectId),
    enabled: Boolean(projectId),
  });

  const act = useMutation({
    mutationFn: (action: 'start' | 'stop' | 'restart') => previewAction(projectId, action),
    onSuccess: (info) => {
      queryClient.setQueryData(['preview', projectId], info);
      setReloadKey((k) => k + 1);
    },
    onError: (err) => {
      const message = err instanceof ApiError ? err.message : 'Preview action failed';
      toast({ title: 'Preview failed', description: message, variant: 'error' });
    },
  });

  const events = useRealtimeStore((s) => s.events);
  const logs = useMemo(() => {
    const lines: Record<LogTab, string> = { frontend: '', backend: '' };
    for (const event of events) {
      if (event.event !== 'terminal.output' || event.project_id !== projectId) continue;
      if (event.payload.source !== 'preview') continue;
      const process = event.payload.process;
      if (process !== 'frontend' && process !== 'backend') continue;
      lines[process] += String(event.payload.chunk ?? '');
    }
    return lines;
  }, [events, projectId]);

  const info = statusQuery.data;
  const running = info?.fe_status === 'running';

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-edge bg-surface">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-edge px-3 py-2">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-fg-subtle">Preview</span>
          <span data-testid="fe-status">
            <Badge tone={TONES[info?.fe_status ?? 'stopped']}>
              FE {info?.fe_status ?? 'stopped'}
            </Badge>
          </span>
          <span data-testid="be-status">
            <Badge tone={TONES[info?.be_status ?? 'stopped']}>
              BE {info?.be_status ?? 'stopped'}
            </Badge>
          </span>
          {statusQuery.isFetching || act.isPending ? <Spinner /> : null}
        </div>

        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={act.isPending || !running}
            onClick={() => setReloadKey((k) => k + 1)}
            data-testid="preview-refresh"
          >
            Refresh
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={act.isPending}
            onClick={() => setLogsOpen((open) => !open)}
            aria-pressed={logsOpen}
            data-testid="preview-logs-toggle"
          >
            Logs
          </Button>
          {running ? (
            <>
              <Button
                size="sm"
                variant="secondary"
                disabled={act.isPending}
                onClick={() => act.mutate('restart')}
                data-testid="preview-restart"
              >
                Restart
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={act.isPending}
                onClick={() => act.mutate('stop')}
                data-testid="preview-stop"
              >
                Stop
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              disabled={act.isPending}
              onClick={() => act.mutate('start')}
              data-testid="preview-start"
            >
              Start
            </Button>
          )}
          {info?.fe_url ? (
            <a
              href={info.fe_url}
              target="_blank"
              rel="noreferrer"
              data-testid="preview-open-tab"
              className="rounded-lg px-2 py-1 text-xs text-brand-text hover:bg-surface-raised"
            >
              Open ↗
            </a>
          ) : null}
        </div>
      </header>

      {info?.warning ? (
        <p
          data-testid="preview-warning"
          className="shrink-0 border-b border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning"
        >
          {info.warning}
        </p>
      ) : null}

      <div className="min-h-0 flex-1">
        {running && info?.fe_url ? (
          <iframe
            key={reloadKey}
            src={info.fe_url}
            title="App preview"
            data-testid="preview-frame"
            className="h-full w-full border-0 bg-white"
            // The preview runs untrusted generated code, served from its own `*.preview.*`
            // subdomain — a different origin from the control plane, so `allow-same-origin` here
            // grants the frame only ITS OWN origin, never the control plane's; normal
            // cross-origin same-origin-policy still keeps it out of `window.parent` and this
            // app's cookies/storage. Omitting the flag was tried first, but it leaves every
            // subresource request from the frame carrying `Origin: null` (an opaque origin),
            // which the generated app's own dev server (Vite) — and any CORS-checking backend —
            // rejects; the iframe then renders permanently blank regardless of whether the
            // generated app is healthy. `allow-same-origin` would only be unsafe if the preview
            // could ever resolve to the *same* origin as the control plane, which the dedicated
            // subdomain rules out.
            sandbox="allow-scripts allow-forms allow-popups allow-modals allow-same-origin"
          />
        ) : (
          <div className="flex h-full items-center justify-center p-6">
            <EmptyState
              title={info?.fe_status === 'failed' ? 'Preview crashed' : 'Preview not running'}
              description={
                info?.fe_status === 'failed'
                  ? 'A dev server exited — check the logs, then restart.'
                  : 'Start the preview to run the generated app in its sandbox.'
              }
            />
          </div>
        )}
      </div>

      {logsOpen ? (
        <section className="h-40 shrink-0 border-t border-edge" data-testid="preview-logs">
          <div className="flex items-center gap-1 border-b border-edge px-2 py-1">
            {(['frontend', 'backend'] as LogTab[]).map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => setLogTab(tab)}
                data-testid={`log-tab-${tab}`}
                className={`rounded px-2 py-0.5 text-xs ${
                  logTab === tab ? 'bg-surface-raised text-fg' : 'text-fg-muted'
                }`}
              >
                {tab}
              </button>
            ))}
          </div>
          <pre
            data-testid="log-output"
            className="h-[calc(100%-1.75rem)] overflow-auto whitespace-pre-wrap px-3 py-2 font-mono text-xs text-fg-muted"
          >
            {logs[logTab] || `No ${logTab} output yet.`}
          </pre>
        </section>
      ) : null}
    </div>
  );
}

export default Preview;
