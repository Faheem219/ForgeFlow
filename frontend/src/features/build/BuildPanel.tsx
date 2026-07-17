import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Suspense, lazy, useMemo, useState } from 'react';

import { Badge, Button, EmptyState, Spinner } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import type { IntentRequest } from '../../lib/types';
import { listStages, submitIntent } from '../workspace/api';
import { STATUS_LABELS, STATUS_TONES } from '../workspace/stageMeta';
import { BUILD_REPORT_KIND, getArtifact, listBuildReports, parseBuildReport } from './api';

// Monaco / xterm-backed IDE + preview are heavy — pull them in only when Build is shown.
const Ide = lazy(() => import('../ide/Ide'));
const Preview = lazy(() => import('../ide/Preview'));

const BOOT_TONES: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
  healthy: 'success',
  degraded: 'warning',
  unhealthy: 'danger',
  not_started: 'neutral',
};

/**
 * Build stage: live code generation (the streamed IDE + preview), the structured build report, a
 * "request changes" chat that regenerates incrementally, and proceed/skip. Drives the phase-24
 * build handler via the conductor; the report renders from the persisted build_report artifact.
 */
export function BuildPanel({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [instruction, setInstruction] = useState('');
  const [showPreview, setShowPreview] = useState(false);

  const stagesQuery = useQuery({
    queryKey: ['stages', projectId],
    queryFn: () => listStages(projectId),
  });
  const reportsQuery = useQuery({
    queryKey: ['build-reports', projectId],
    queryFn: () => listBuildReports(projectId),
  });

  const status = stagesQuery.data?.find((s) => s.stage === 'build')?.status ?? 'empty';

  // Newest build_report artifact (the codegen agent versions these; others may be plain diffs).
  const latestReportId = useMemo(() => {
    const reports = (reportsQuery.data ?? []).filter((a) => a.meta.kind === BUILD_REPORT_KIND);
    return reports.at(-1)?.id ?? null;
  }, [reportsQuery.data]);

  const reportQuery = useQuery({
    queryKey: ['build-report', latestReportId],
    queryFn: () => getArtifact(latestReportId as string),
    enabled: Boolean(latestReportId),
  });
  const report = parseBuildReport(reportQuery.data?.content);

  // Live progress: the latest build progress step streamed over the realtime channel.
  const events = useRealtimeStore((s) => s.events);
  const latestStep = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const e = events[i];
      if (e.event === 'progress' && e.project_id === projectId && e.payload.stage === 'build') {
        return String(e.payload.step ?? '');
      }
    }
    return '';
  }, [events, projectId]);

  const intent = useMutation({
    mutationFn: (body: IntentRequest) => submitIntent(projectId, body),
    onSuccess: () => {
      setInstruction('');
      void queryClient.invalidateQueries({ queryKey: ['build-reports', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['artifacts', projectId] });
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Build action failed';
      toast({ title: 'Build action failed', description, variant: 'error' });
    },
  });

  const busy = intent.isPending;
  const hasBuild = Boolean(latestReportId);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-slate-200">
          Build
          <span data-testid="build-status">
            <Badge tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</Badge>
          </span>
          {busy ? (
            <span className="flex items-center gap-1 text-xs text-slate-400" data-testid="build-progress">
              <Spinner />
              {latestStep ? `Generating — ${latestStep}…` : 'Generating…'}
            </span>
          ) : null}
        </span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setShowPreview((open) => !open)}
            aria-pressed={showPreview}
            data-testid="toggle-preview"
          >
            {showPreview ? 'Hide preview' : 'Preview'}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            data-testid="build-skip"
            onClick={() => intent.mutate({ stage: 'build', action: 'skip' })}
          >
            Skip
          </Button>
          <Button
            size="sm"
            disabled={busy}
            data-testid="build-proceed"
            onClick={() => intent.mutate({ stage: 'build', action: 'proceed' })}
          >
            {hasBuild ? 'Rebuild' : 'Build'}
          </Button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,1fr)_20rem]">
        {/* Live generation (IDE stream) + optional preview */}
        <div
          className={`grid min-h-0 gap-3 ${showPreview ? 'grid-rows-2 xl:grid-cols-2 xl:grid-rows-1' : 'grid-rows-1'}`}
        >
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center">
                <Spinner />
              </div>
            }
          >
            <Ide projectId={projectId} />
          </Suspense>
          {showPreview ? (
            <Suspense
              fallback={
                <div className="flex h-full items-center justify-center">
                  <Spinner />
                </div>
              }
            >
              <Preview projectId={projectId} />
            </Suspense>
          ) : null}
        </div>

        {/* Build report + request-changes */}
        <div className="flex min-h-0 flex-col gap-3 overflow-auto">
          <section className="space-y-2 rounded-xl border border-slate-800 p-3" data-testid="build-report">
            <h4 className="text-xs uppercase tracking-wide text-slate-500">Build report</h4>
            {reportQuery.isFetching && !report ? (
              <div className="flex justify-center py-4">
                <Spinner />
              </div>
            ) : report ? (
              <div className="space-y-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className="text-slate-400">Boot</span>
                  <span data-testid="build-boot-status">
                    <Badge tone={BOOT_TONES[report.boot_status] ?? 'neutral'}>
                      {report.boot_status}
                    </Badge>
                  </span>
                  {report.tests_passed !== null ? (
                    <Badge tone={report.tests_passed ? 'success' : 'danger'}>
                      tests {report.tests_passed ? 'passed' : 'failed'}
                    </Badge>
                  ) : null}
                </div>
                {report.summary ? <p className="text-slate-300">{report.summary}</p> : null}
                {report.features_built.length > 0 ? (
                  <p className="text-slate-400">
                    <span className="text-slate-500">Features:</span>{' '}
                    {report.features_built.join(', ')}
                  </p>
                ) : null}
                <p className="text-slate-400">
                  <span className="text-slate-500">Files changed:</span>{' '}
                  {report.files_changed.length}
                  {report.commit ? (
                    <span className="ml-2 font-mono text-xs text-slate-500">
                      {report.commit.slice(0, 8)}
                    </span>
                  ) : null}
                </p>
                {report.follow_ups.length > 0 ? (
                  <ul className="list-disc space-y-0.5 pl-4 text-xs text-amber-300/80">
                    {report.follow_ups.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                  </ul>
                ) : null}
                {report.notes.length > 0 ? (
                  <ul className="list-disc space-y-0.5 pl-4 text-xs text-slate-500">
                    {report.notes.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : (
              <EmptyState
                title="No build yet"
                description='Press "Build" to generate the app onto the skeleton.'
              />
            )}
          </section>

          <section className="space-y-2 rounded-xl border border-slate-800 p-3">
            <h4 className="text-xs uppercase tracking-wide text-slate-500">Request changes</h4>
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (instruction.trim())
                  intent.mutate({ stage: 'build', action: 'refine', message: instruction.trim() });
              }}
            >
              <textarea
                aria-label="Change request"
                data-testid="build-request-input"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder={
                  hasBuild ? 'e.g. "add a delete button to each todo"' : 'Build the app first'
                }
                disabled={busy}
                rows={3}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-brand-500 disabled:opacity-50"
              />
              <Button
                type="submit"
                size="sm"
                className="w-full"
                disabled={busy || !instruction.trim()}
                data-testid="build-request-submit"
              >
                Request changes
              </Button>
            </form>
            <p className="text-xs text-slate-500">
              A change request regenerates incrementally and marks downstream stages stale.
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}

export default BuildPanel;
