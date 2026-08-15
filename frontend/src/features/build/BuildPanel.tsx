import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronRight, MoreHorizontal } from 'lucide-react';
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';

import { AgentWorking } from '../../components/AgentWorking';
import { Badge, Button, Spinner } from '../../components/ui';
import { cn } from '../../lib/cn';
import { ApiError } from '../../lib/apiClient';
import { advanceCursor } from '../../lib/eventCursor';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import { toast } from '../../lib/stores/toastStore';
import { useTaskStream } from '../../lib/useTaskStream';
import type { IntentRequest } from '../../lib/types';
// Neither import pulls Monaco in — the heavy editor stays behind the lazy `Ide` boundary.
import { readFile } from '../ide/api';
import { useIdeStore } from '../ide/ideStore';
import { listStages, submitIntent } from '../workspace/api';
import { nextStage, STATUS_LABELS, STATUS_TONES } from '../workspace/stageMeta';
import {
  BUILD_REPORT_KIND,
  getActivity,
  getArtifact,
  listBuildReports,
  parseBuildReport,
} from './api';
import { BUILD_STEPS, STEP_LABELS, useBuildActivity } from './useBuildActivity';

/** Open a generated file in the IDE from the live list (focus it if it is already open). */
async function openGeneratedFile(projectId: string, path: string): Promise<void> {
  const store = useIdeStore.getState();
  if (store.files[path]) {
    store.setActive(path);
    return;
  }
  try {
    const file = await readFile(projectId, path);
    useIdeStore.getState().openFile(path, file.content);
  } catch {
    toast({ title: 'Could not open file', description: path, variant: 'error' });
  }
}

// Monaco / xterm-backed IDE + preview are heavy — pull them in only when Build is shown.
const Ide = lazy(() => import('../ide/Ide'));
const Preview = lazy(() => import('../ide/Preview'));

const BOOT_TONES: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
  healthy: 'success',
  degraded: 'warning',
  unhealthy: 'danger',
  not_started: 'neutral',
};

/** One row of the phase rail (phase-56) — live events, durable Run progress, or a finished report. */
interface PhaseRow {
  id: string;
  title: string;
  kind: string;
  status: string;
  index: number;
}

/** The backend's fixed stop_reason vocabulary (build_verify.py + build.py), in plain English. */
const STOP_REASON_LABELS: Record<string, string> = {
  typecheck_failed: 'the generated code does not typecheck',
  placeholder_left: 'a template placeholder is still in the app',
  boot_unhealthy: 'a dev server is not running',
  repair_escalated: 'the repair loop could not fix it',
  no_repair_target: 'nothing editable could be identified to repair',
  phases_incomplete: 'some planned phases did not finish',
  environment: 'the sandbox environment, not the code',
  provider: 'a provider error',
  budget: 'the budget cap was reached',
  cancelled: 'it was cancelled',
  wall_clock: 'it ran past its time budget',
  failed: 'the build failed part-way',
};

const PHASE_GLYPH: Record<string, string> = { done: '✓', failed: '✗', pending: '·' };

const PHASE_TONE: Record<string, string> = {
  done: 'text-success',
  failed: 'text-danger',
  running: 'text-brand',
  pending: 'text-fg-subtle',
};

/**
 * Build stage: the streamed IDE + preview are the hero (full width), with a compact, collapsible
 * build-report strip above them. Proceed/skip live in the header; incremental change requests go
 * through the shared Assistant conversation (which sends `refine` for the active stage), so there is
 * a single conversational surface rather than two. Drives the phase-24 build handler via the
 * conductor; the report renders from the persisted build_report artifact.
 */
export function BuildPanel({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [showPreview, setShowPreview] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [runKey, setRunKey] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);

  const stagesQuery = useQuery({
    queryKey: ['stages', projectId],
    queryFn: () => listStages(projectId),
  });
  // Server-side truth for "is a build running", so a page reload reattaches instead of showing an
  // idle panel while the agent is still working. Polls only while something is in flight.
  const activityQuery = useQuery({
    queryKey: ['activity', projectId],
    queryFn: () => getActivity(projectId),
    refetchInterval: (query) => (query.state.data?.build ? 2500 : false),
    refetchOnWindowFocus: true,
  });
  const serverBuild = activityQuery.data?.build ?? null;

  const reportsQuery = useQuery({
    queryKey: ['build-reports', projectId],
    queryFn: () => listBuildReports(projectId),
  });

  const persistedStatus = stagesQuery.data?.find((s) => s.stage === 'build')?.status ?? 'empty';

  // A `proceed` here just kicks off a detached codegen run — its HTTP response says nothing about
  // whether the build actually finished, so (unlike the other stages) we can't advance on the
  // mutation's onSuccess. Instead, watch the polled/realtime-driven status itself and advance the
  // moment it genuinely transitions to `complete`.
  //
  // Never on mount, so reopening an already-finished build to review it doesn't immediately bounce
  // you away again — `persistedStatus` defaults to `'empty'` before `stagesQuery` resolves, so a
  // naive prev/current comparison would misread an already-complete project's first real data
  // arrival ('empty' -> 'complete') as a live transition. `hasLoadedRef` gates the very first
  // successful load out of the comparison; only changes observed *after* that count.
  const setActiveStage = useWorkspaceStore((s) => s.setActiveStage);
  const prevStatusRef = useRef(persistedStatus);
  const hasLoadedRef = useRef(false);
  useEffect(() => {
    if (stagesQuery.data === undefined) return; // still loading — nothing real to compare yet
    if (
      hasLoadedRef.current &&
      prevStatusRef.current !== 'complete' &&
      persistedStatus === 'complete'
    ) {
      const next = nextStage('build');
      if (next) setActiveStage(next);
    }
    hasLoadedRef.current = true;
    prevStatusRef.current = persistedStatus;
  }, [persistedStatus, setActiveStage, stagesQuery.data]);

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

  const intent = useMutation({
    mutationFn: (body: IntentRequest) => submitIntent(projectId, body),
    // A new run starts a fresh file list rather than appending to the previous build's.
    onMutate: () => setRunKey((k) => k + 1),
    onSuccess: (_data, body) => {
      void queryClient.invalidateQueries({ queryKey: ['build-reports', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['artifacts', projectId] });
      // Unlike `proceed` (a detached run — see the status-watching effect below), `skip` resolves
      // synchronously, so it's safe to advance right here.
      if (body.action === 'skip') {
        const next = nextStage('build');
        if (next) setActiveStage(next);
      }
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Build action failed';
      toast({ title: 'Build action failed', description, variant: 'error' });
    },
  });

  // "Building" is whatever the SERVER says is in flight, falling back to the in-flight request for
  // the moment before the first poll lands. Deriving it from `intent.isPending` alone is what made a
  // refresh look idle while the agent kept working.
  //
  // The fallback is bounded by the run's terminal event rather than by the request alone: a POST
  // whose connection a proxy dropped never settles, so `isPending` on its own could hold the
  // spinner on forever *after* the build had finished and its report had already rendered.
  // `useTaskStream` clears on `run.finished` (emitted from the conductor's `finally`, detached or
  // not), so whichever signal lands first wins.
  const task = useTaskStream(projectId, { pending: intent.isPending, stage: 'build' });
  const busy = Boolean(serverBuild) || task.busy;
  const hasBuild = Boolean(latestReportId);

  // Live telemetry from the realtime channel (re-baselined per run), merged with the durable list
  // the server holds — the latter is what survives a reload and ring-buffer eviction.
  const live = useBuildActivity(projectId, runKey);
  const liveFiles = useMemo(() => {
    const merged = [...(serverBuild?.files ?? [])];
    for (const path of live.files) if (!merged.includes(path)) merged.push(path);
    // With no build running, fall back to the last report so past generations stay visible.
    if (merged.length === 0 && !busy) return report?.files_changed ?? [];
    return merged;
  }, [serverBuild?.files, live.files, busy, report?.files_changed]);

  // A finished build announces itself over the channel; refresh at once rather than waiting for
  // the next poll, so the spinner clears the moment the report lands.
  // Cursored by `seq`, not by array index — the ring evicts once a build fills it, at which point
  // an index cursor stops advancing and this never fires again. See lib/eventCursor.ts.
  const events = useRealtimeStore((s) => s.events);
  const seen = useRef(0);
  useEffect(() => {
    const { fresh, nextSeq } = advanceCursor(events, seen.current);
    seen.current = nextSeq;
    const done = fresh.some(
      (e) =>
        e.project_id === projectId &&
        (e.event === 'build.report' ||
          (e.event === 'stage.transition' && e.payload.stage === 'build')),
    );
    if (done) {
      void queryClient.invalidateQueries({ queryKey: ['activity', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['build-reports', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
    }
  }, [events, projectId, queryClient]);

  // A refine-driven build legitimately leaves the stage `complete` in the DB (marking it
  // in_progress would make the closing transition illegal), so show the running build directly
  // rather than a badge that reads "Complete" while the agent is mid-flight.
  const status = busy ? 'in_progress' : persistedStatus;

  const latestStep = live.step || serverBuild?.step || '';
  // Prefer the fine-grained tool label ("Installing dependencies") over the coarse phase.
  const activityLabel = live.label || serverBuild?.label || STEP_LABELS[latestStep] || '';
  const activityTarget = live.target || serverBuild?.target || '';

  // The phase rail (phase-56), in precedence order: live events → the server's durable Run
  // progress (which survives a reload) → the finished report, so a completed build still shows
  // what it did. Empty for a pre-phase-56 report, which is what keeps that panel byte-identical.
  const phaseRows = useMemo((): PhaseRow[] => {
    if (live.phases.length > 0) return live.phases;
    const serverIndex = serverBuild?.phase_index ?? 0;
    const reportPhases = report?.phases ?? [];
    if (busy && serverIndex > 0 && reportPhases.length === 0) {
      // Mid-build after a reload with no report yet: all we know is which phase is running.
      return [
        {
          id: serverBuild?.phase_id ?? 'current',
          title: serverBuild?.label || `Phase ${serverIndex}`,
          kind: '',
          status: 'running',
          index: serverIndex,
        },
      ];
    }
    return reportPhases.map((p, i) => ({
      id: p.id,
      title: p.title,
      kind: p.kind,
      status: serverIndex === i + 1 && busy ? 'running' : p.status,
      index: i + 1,
    }));
  }, [
    live.phases,
    serverBuild?.phase_index,
    serverBuild?.phase_id,
    serverBuild?.label,
    report?.phases,
    busy,
  ]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-fg">
          Build
          <span data-testid="build-status">
            <Badge tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</Badge>
          </span>
          {busy ? (
            <span
              className="flex items-center gap-1 text-xs text-fg-muted"
              data-testid="build-progress"
            >
              <Spinner />
              {activityLabel ? `${activityLabel}…` : 'Generating…'}
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
          {hasBuild ? (
            <div className="relative">
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                aria-label="More build options"
                aria-expanded={menuOpen}
                data-testid="build-menu"
                onClick={() => setMenuOpen((v) => !v)}
              >
                <MoreHorizontal aria-hidden className="h-4 w-4" strokeWidth={1.75} />
              </Button>
              {menuOpen ? (
                <div
                  role="menu"
                  className="absolute right-0 z-20 mt-1 w-56 rounded-lg border border-edge-strong bg-surface-overlay p-1 shadow-xl"
                >
                  <button
                    type="button"
                    role="menuitem"
                    data-testid="build-fresh"
                    onClick={() => {
                      setMenuOpen(false);
                      intent.mutate({
                        stage: 'build',
                        action: 'proceed',
                        payload: { fresh: true },
                      });
                    }}
                    className="block w-full rounded px-2 py-1.5 text-left text-sm text-fg hover:bg-surface-raised"
                  >
                    Rebuild from scratch
                    <span className="mt-0.5 block text-[11px] text-fg-subtle">
                      Ignore existing feature code and regenerate it.
                    </span>
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </header>

      {/* Live run telemetry. The plan step writes no files, so without this the panel looks frozen
          for a long time and then dumps a dozen tabs at once. */}
      {busy || liveFiles.length > 0 ? (
        <section
          className="shrink-0 overflow-hidden rounded-xl border border-edge bg-surface"
          data-testid="build-activity"
        >
          {/*
            Shown for the WHOLE run, not just until the first file lands.

            It previously collapsed once output appeared, on the reasoning that the file list is
            more informative. That was wrong: files streaming in do not tell you whether the agent
            is still working or has stopped, so the indicator disappearing mid-run reads as "done"
            when it is not. It stays until the run's terminal event clears `busy`.
          */}
          {busy ? (
            <div className="flex justify-center border-b border-edge py-6">
              <AgentWorking label={activityLabel || 'Starting the build'} />
            </div>
          ) : null}

          <div className="flex items-center gap-2 px-4 py-2">
            {busy ? <Spinner size="sm" /> : null}
            <span className="text-xs font-medium text-fg-muted" data-testid="build-activity-label">
              {busy ? `${activityLabel || 'Starting the build'}…` : 'Last run'}
            </span>
            {busy && activityTarget ? (
              <span className="truncate font-mono text-[11px] text-fg-subtle">
                {activityTarget}
              </span>
            ) : null}
            <span className="text-xs text-fg-subtle" data-testid="build-file-count">
              {liveFiles.length} file{liveFiles.length === 1 ? '' : 's'} written
            </span>
            {busy ? (
              <span className="ml-auto flex items-center gap-1">
                {BUILD_STEPS.map((s) => (
                  <span
                    key={s}
                    title={STEP_LABELS[s]}
                    className={`h-1.5 w-6 rounded-full ${
                      s === latestStep ? 'bg-brand' : 'bg-surface-raised'
                    }`}
                  />
                ))}
              </span>
            ) : null}
          </div>
          {/* The phase rail. Rendered only when there are phases, so a pre-phase-56 report leaves
              this section exactly as it was. Height-capped like the file list below, so it can
              never steal editor width. */}
          {phaseRows.length > 0 ? (
            <ul
              className="max-h-24 space-y-0.5 overflow-auto border-t border-edge px-4 py-2"
              data-testid="build-phase-list"
            >
              {phaseRows.map((phase) => (
                <li
                  key={phase.id}
                  className="flex items-center gap-2 text-[11px]"
                  data-testid="build-phase-row"
                  data-status={phase.status}
                >
                  <span
                    className={cn('w-3 shrink-0 text-center', PHASE_TONE[phase.status])}
                    aria-hidden
                  >
                    {phase.status === 'running' ? (
                      <Spinner size="sm" />
                    ) : (
                      (PHASE_GLYPH[phase.status] ?? '·')
                    )}
                  </span>
                  <span className="truncate text-fg-muted">{phase.title}</span>
                  {phase.kind ? <Badge tone="neutral">{phase.kind}</Badge> : null}
                </li>
              ))}
            </ul>
          ) : null}
          {liveFiles.length > 0 ? (
            <ul
              className="max-h-24 space-y-0.5 overflow-auto border-t border-edge px-4 py-2"
              data-testid="build-file-list"
            >
              {liveFiles.map((path) => (
                <li key={path} className="flex items-center gap-2 font-mono text-[11px]">
                  <span className="text-success" aria-hidden>
                    +
                  </span>
                  <button
                    type="button"
                    onClick={() => void openGeneratedFile(projectId, path)}
                    className="truncate text-fg-muted hover:text-fg hover:underline"
                  >
                    {path}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      {/* Compact, collapsible build report — a horizontal strip, so it never steals editor width. */}
      {report ? (
        <section
          className="shrink-0 overflow-hidden rounded-xl border border-edge bg-surface"
          data-testid="build-report"
        >
          <button
            type="button"
            aria-expanded={reportOpen}
            data-testid="build-report-toggle"
            onClick={() => setReportOpen((v) => !v)}
            className="flex w-full items-center gap-2 px-4 py-2 text-left hover:bg-surface-raised"
          >
            <span className="text-xs uppercase tracking-wide text-fg-subtle">Report</span>
            <span data-testid="build-boot-status">
              <Badge tone={BOOT_TONES[report.boot_status] ?? 'neutral'}>{report.boot_status}</Badge>
            </span>
            {report.tests_passed !== null ? (
              <Badge tone={report.tests_passed ? 'success' : 'danger'}>
                tests {report.tests_passed ? 'passed' : 'failed'}
              </Badge>
            ) : null}
            <span className="truncate text-xs text-fg-muted">
              {report.files_changed.length} file{report.files_changed.length === 1 ? '' : 's'}{' '}
              changed
              {report.commit ? (
                <span className="ml-2 font-mono text-fg-subtle">{report.commit.slice(0, 8)}</span>
              ) : null}
            </span>
            <span className="ml-auto text-fg-subtle" aria-hidden>
              <ChevronRight
                className={cn(
                  'h-4 w-4 transition-transform duration-150',
                  reportOpen && 'rotate-90',
                )}
                strokeWidth={1.75}
              />
            </span>
          </button>
          {reportOpen ? (
            <div className="space-y-2 border-t border-edge px-4 py-3 text-sm">
              {report.summary ? <p className="text-fg-muted">{report.summary}</p> : null}
              {/* Why the build stopped short, in the report's own words (phase-55's vocabulary,
                  plus phase-56's `phases_incomplete`). Absent on pre-rework reports. */}
              {report.outcome && report.outcome !== 'complete' && report.stop_reason ? (
                <p className="text-xs text-warning" data-testid="build-stop-reason">
                  Stopped: {STOP_REASON_LABELS[report.stop_reason] ?? report.stop_reason}
                </p>
              ) : null}
              {report.features_built.length > 0 ? (
                <p className="text-fg-muted">
                  <span className="text-fg-subtle">Features:</span>{' '}
                  {report.features_built.join(', ')}
                </p>
              ) : null}
              {report.follow_ups.length > 0 ? (
                <ul className="list-disc space-y-0.5 pl-4 text-xs text-warning">
                  {report.follow_ups.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
              ) : null}
              {report.notes.length > 0 ? (
                <ul className="list-disc space-y-0.5 pl-4 text-xs text-fg-subtle">
                  {report.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : (
        <p
          className="shrink-0 rounded-xl border border-dashed border-edge px-4 py-2 text-xs text-fg-subtle"
          data-testid="build-empty"
        >
          No build yet — press <span className="font-medium text-fg-muted">Build</span> to generate
          the app onto the skeleton, then refine it from the Assistant.
        </p>
      )}

      {/* Live generation (IDE stream) fills the panel; preview splits in beside it on demand. */}
      <div
        className={`grid min-h-0 flex-1 gap-3 ${
          showPreview ? 'grid-rows-2 xl:grid-cols-2 xl:grid-rows-1' : 'grid-rows-1'
        }`}
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
    </div>
  );
}

export default BuildPanel;
