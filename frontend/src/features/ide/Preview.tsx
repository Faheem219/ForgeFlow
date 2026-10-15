import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Logs, MonitorPlay, RefreshCw, RotateCcw, Square } from 'lucide-react';
import { useMemo, useState } from 'react';

/**
 * A preview control: icon-only when the header is tight, icon and name when there is room. Either
 * way its accessible name is the same word.
 */
function ToolButton({
  label,
  labelled,
  icon,
  className,
  ...props
}: {
  label: string;
  labelled: boolean;
  icon: ReactNode;
  className?: string;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'children'>): JSX.Element {
  if (!labelled) {
    return (
      <IconButton label={label} className={className} {...props}>
        {icon}
      </IconButton>
    );
  }
  return (
    <button
      type="button"
      aria-label={label}
      className={cn(
        'inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-fg-subtle transition-colors hover:bg-surface-raised hover:text-fg disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent [&_svg]:h-3.5 [&_svg]:w-3.5',
        props['aria-pressed'] && 'bg-surface-raised text-fg',
        className,
      )}
      {...props}
    >
      {icon}
      <span aria-hidden>{label}</span>
    </button>
  );
}

import { Badge, Button, EmptyState, IconButton, Spinner } from '../../components/ui';
import { cn } from '../../lib/cn';
import { useMeasuredWidth } from '../../lib/useNarrow';
import type { ReactNode } from 'react';
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
  // Which frame (by reload key) has finished loading — anything else is still on its way in.
  const [loadedKey, setLoadedKey] = useState<number | null>(null);

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
  // The header adapts to the pane, not the viewport — the preview is routinely dragged narrow beside
  // the editor, and maximized in focus mode. Below ~460px the address bar and status words give way
  // to the controls; from ~640px the controls carry their names as well as their icons.
  const [headerRef, headerWidth] = useMeasuredWidth<HTMLElement>(520);
  const narrow = headerWidth < 460;
  const labelled = headerWidth >= 640;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-edge bg-surface">
      {/* Browser chrome, in miniature: process status, the address, and the page controls. The
          right padding keeps the row clear of the pane's maximize button, which overlays it. */}
      <header
        ref={headerRef}
        className="flex h-10 shrink-0 items-center gap-2 border-b border-edge pl-2.5 pr-9"
      >
        <div className="flex shrink-0 items-center gap-1">
          <span data-testid="fe-status" title={`Frontend ${info?.fe_status ?? 'stopped'}`}>
            <Badge tone={TONES[info?.fe_status ?? 'stopped']} dot>
              FE{' '}
              <span className={narrow ? 'sr-only' : undefined}>{info?.fe_status ?? 'stopped'}</span>
            </Badge>
          </span>
          <span data-testid="be-status" title={`Backend ${info?.be_status ?? 'stopped'}`}>
            <Badge tone={TONES[info?.be_status ?? 'stopped']} dot>
              BE{' '}
              <span className={narrow ? 'sr-only' : undefined}>{info?.be_status ?? 'stopped'}</span>
            </Badge>
          </span>
        </div>

        <div
          className={cn(
            'h-7 min-w-0 flex-1 items-center gap-2 rounded-md bg-fg/[0.045] px-2.5',
            narrow ? 'hidden' : 'flex',
          )}
          title={info?.fe_url ?? undefined}
        >
          {statusQuery.isFetching || act.isPending ? (
            <span className="text-fg-faint">
              <Spinner size="sm" />
            </span>
          ) : (
            <span
              aria-hidden
              className={cn(
                'h-1.5 w-1.5 shrink-0 rounded-full',
                running ? 'bg-success' : 'bg-fg-faint',
              )}
            />
          )}
          <span className="truncate font-mono text-2xs text-fg-subtle">
            {info?.fe_url ?? 'preview not running'}
          </span>
        </div>

        <div className={cn('flex shrink-0 items-center gap-0.5', narrow && 'ml-auto')}>
          <ToolButton
            label="Refresh"
            labelled={labelled}
            icon={<RefreshCw aria-hidden strokeWidth={1.75} />}
            disabled={act.isPending || !running}
            onClick={() => setReloadKey((k) => k + 1)}
            data-testid="preview-refresh"
          />
          <ToolButton
            label="Logs"
            labelled={labelled}
            icon={<Logs aria-hidden strokeWidth={1.75} />}
            disabled={act.isPending}
            className={logsOpen ? 'bg-surface-raised text-fg' : undefined}
            onClick={() => setLogsOpen((open) => !open)}
            aria-pressed={logsOpen}
            data-testid="preview-logs-toggle"
          />
          {info?.fe_url ? (
            <a
              href={info.fe_url}
              target="_blank"
              rel="noreferrer"
              data-testid="preview-open-tab"
              title="Open in a new tab"
              aria-label="Open in a new tab"
              className={cn(
                'inline-flex h-7 items-center justify-center gap-1.5 rounded-md text-xs font-medium text-fg-subtle transition-colors hover:bg-surface-raised hover:text-fg',
                labelled ? 'px-2' : 'w-7',
              )}
            >
              <ExternalLink
                aria-hidden
                className={labelled ? 'h-3.5 w-3.5' : 'h-4 w-4'}
                strokeWidth={1.75}
              />
              {labelled ? <span aria-hidden>Open</span> : null}
            </a>
          ) : null}
          {/* Page controls above, process controls below — the two kinds never sit glyph to glyph. */}
          <span aria-hidden className="mx-1 h-4 w-px bg-edge" />
          {running ? (
            <>
              <ToolButton
                label="Restart"
                labelled={labelled}
                icon={<RotateCcw aria-hidden strokeWidth={1.75} />}
                disabled={act.isPending}
                onClick={() => act.mutate('restart')}
                data-testid="preview-restart"
              />
              <ToolButton
                label="Stop"
                labelled={labelled}
                // Filled: the media-control stop, unmistakable beside the outline page glyphs.
                icon={<Square aria-hidden className="fill-current" strokeWidth={0} />}
                disabled={act.isPending}
                onClick={() => act.mutate('stop')}
                data-testid="preview-stop"
                className="hover:text-danger [&_svg]:!h-3 [&_svg]:!w-3"
              />
            </>
          ) : (
            <Button
              size="xs"
              disabled={act.isPending}
              onClick={() => act.mutate('start')}
              data-testid="preview-start"
              className="ml-0.5"
            >
              Start
            </Button>
          )}
        </div>
      </header>

      {info?.warning ? (
        <p
          data-testid="preview-warning"
          className="shrink-0 border-b border-warning/25 bg-warning/[0.08] px-3 py-2 text-xs leading-relaxed text-warning"
        >
          {info.warning}
        </p>
      ) : null}

      <div className="relative min-h-0 flex-1">
        {running && info?.fe_url && loadedKey !== reloadKey ? (
          // The frame is (re)loading: a thin travelling line, cleared by the frame's own onLoad.
          <div aria-hidden className="absolute inset-x-0 top-0 z-10 h-px overflow-hidden">
            <div className="h-full w-1/4 bg-gradient-to-r from-transparent via-brand-text to-transparent motion-safe:animate-ff-sweep" />
          </div>
        ) : null}
        {running && info?.fe_url ? (
          <iframe
            key={reloadKey}
            onLoad={() => setLoadedKey(reloadKey)}
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
              icon={<MonitorPlay aria-hidden strokeWidth={1.5} />}
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
        <section
          className="flex h-44 shrink-0 flex-col border-t border-edge"
          data-testid="preview-logs"
        >
          <div className="flex h-8 shrink-0 items-center gap-0.5 px-2" role="tablist">
            {(['frontend', 'backend'] as LogTab[]).map((tab) => (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={logTab === tab}
                onClick={() => setLogTab(tab)}
                data-testid={`log-tab-${tab}`}
                className={cn(
                  'rounded-md px-2 py-0.5 text-xs capitalize transition-colors',
                  logTab === tab
                    ? 'bg-fg/[0.07] font-medium text-fg'
                    : 'text-fg-subtle hover:text-fg-muted',
                )}
              >
                {tab}
              </button>
            ))}
          </div>
          <pre
            data-testid="log-output"
            className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap px-3 pb-2 font-mono text-2xs leading-relaxed text-fg-muted"
          >
            {logs[logTab] || `No ${logTab} output yet.`}
          </pre>
        </section>
      ) : null}
    </div>
  );
}

export default Preview;
