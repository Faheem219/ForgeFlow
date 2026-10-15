import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check,
  ChevronDown,
  Circle,
  FileCheck2,
  Hammer,
  History,
  MoreHorizontal,
  PanelRight,
  X,
} from 'lucide-react';
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';

import { AgentWorking } from '../../components/AgentWorking';
import { Badge, Button, Spinner } from '../../components/ui';
import { fileIcon } from '../ide/fileIcons';
import { cn } from '../../lib/cn';
import { useNarrow } from '../../lib/useNarrow';
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
import { ResetStageButton } from '../workspace/StageReset';
import { nextStage, STATUS_LABELS, STATUS_TONES } from '../workspace/stageMeta';
import {
  BUILD_REPORT_KIND,
  getActivity,
  getArtifact,
  listBuildReports,
  parseBuildReport,
} from './api';
import { BUILD_STEPS, STEP_LABELS, useBuildActivity } from './useBuildActivity';

/**
 * Whether a build intent starts an actual agent run. `skip`/`unskip` only move the stage marker,
 * and a `proceed` carrying `approve` is the user accepting the build that already exists — none of
 * them generate code, so none of them should reset the live run view.
 */
function startsBuild(body: IntentRequest): boolean {
  if (body.action === 'refine') return true;
  return body.action === 'proceed' && !body.payload?.approve;
}

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
import { FocusButton, FocusOverlay } from '../workspace/layout/FocusOverlay';
import { Pane, PaneHandle, Split } from '../workspace/layout/SplitPane';
import { layoutId } from '../workspace/layout/layoutStorage';

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
  /** phase-64: what the phase actually did — the evidence the report now records. Absent for a
   *  live row (the event stream carries status only) and for a pre-phase-64 report. */
  fileCount?: number;
  note?: string;
  /** phase-67: an earlier build's phase a change request kept as history — shown, not re-run. */
  earlier?: boolean;
}

/** The backend's fixed stop_reason vocabulary (build_verify.py + build.py), in plain English. */
const STOP_REASON_LABELS: Record<string, string> = {
  typecheck_failed: 'the generated code does not typecheck',
  placeholder_left: 'a template placeholder is still in the app',
  no_feature_code: 'the app is still the template — no feature code was written',
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

/**
 * A phase's state as a glyph. Still, on purpose: the run strip already has one moving mark, and a
 * spinner per running phase on top of it made a build feel frantic. A finished phase's check pops in
 * once — the one moment in the list that deserves a beat.
 */
function PhaseGlyph({ status }: { status: string }): JSX.Element {
  if (status === 'running') {
    return <span className="h-2.5 w-2.5 rounded-full border-2 border-brand-text" />;
  }
  if (status === 'done') {
    return (
      <Check className="h-3.5 w-3.5 text-success motion-safe:animate-ff-pop" strokeWidth={2.25} />
    );
  }
  if (status === 'failed') return <X className="h-3.5 w-3.5 text-danger" strokeWidth={2.25} />;
  return <Circle className="h-2 w-2 text-fg-faint" strokeWidth={3} />;
}

/** A heading inside the run strip — small, quiet, with an optional count. */
function StripHeading({ children, count }: { children: string; count?: string }): JSX.Element {
  return (
    <p className="mb-1 flex items-center gap-1.5 text-2xs font-medium text-fg-faint">
      {children}
      {count ? <span className="tabular-nums text-fg-faint/80">{count}</span> : null}
    </p>
  );
}

/**
 * Build stage: the streamed IDE + preview are the hero (full width), with a compact, collapsible
 * build-report strip above them. Build/approve/skip live in the header; incremental change requests go
 * through the shared Assistant conversation (which sends `refine` for the active stage), so there is
 * a single conversational surface rather than two. Drives the phase-24 build handler via the
 * conductor; the report renders from the persisted build_report artifact.
 */
export function BuildPanel({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [showPreview, setShowPreview] = useState(false);
  // Which pane, if any, is maximized. Momentary intent, so it is not persisted across reloads.
  const [focused, setFocused] = useState<'ide' | 'preview' | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [runKey, setRunKey] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  // The last run's phases and files, folded away while idle (always shown during a run).
  const [runDetailsOpen, setRunDetailsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // The overflow menu dismisses like a real menu: click anywhere else, or Escape.
  useEffect(() => {
    if (!menuOpen) return;
    function onPointerDown(event: PointerEvent): void {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    function onKey(event: KeyboardEvent): void {
      if (event.key === 'Escape') setMenuOpen(false);
    }
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

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
  // An un-skip restores the stage to whatever it was — often `complete`. That is a transition the
  // effect below would otherwise read as "the build just finished", and bounce the user to Test
  // one click after they asked to come back. Set per intent, so the next real build still advances.
  const undoingSkipRef = useRef(false);
  useEffect(() => {
    if (stagesQuery.data === undefined) return; // still loading — nothing real to compare yet
    if (
      hasLoadedRef.current &&
      prevStatusRef.current !== 'complete' &&
      persistedStatus === 'complete' &&
      !undoingSkipRef.current
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
  // Which path the last build took. Empty for reports written before scope routing (phase-60), and
  // for those the badge is simply absent rather than guessing. A planned *change* (phase-67) counts
  // only the phases it added — the earlier ones were kept, not rebuilt.
  const addedPhases = report ? report.phases.length - report.prior_phases : 0;
  const scopeLabel =
    report?.scope === 'small'
      ? 'Incremental change'
      : report?.scope === 'large' && report.prior_phases > 0
        ? `Planned change · ${addedPhases} new phase${addedPhases === 1 ? '' : 's'}`
        : report?.scope === 'large'
          ? `Planned build · ${report.phases.length} phase${report.phases.length === 1 ? '' : 's'}`
          : '';

  const intent = useMutation({
    mutationFn: (body: IntentRequest) => submitIntent(projectId, body),
    onMutate: (body) => {
      undoingSkipRef.current = body.action === 'unskip';
      // A new run starts a fresh file list rather than appending to the previous build's.
      if (startsBuild(body)) setRunKey((k) => k + 1);
    },
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
      fileCount: Array.isArray(p.files) ? p.files.length : undefined,
      note: p.note || undefined,
      earlier: i < (report?.prior_phases ?? 0),
    }));
  }, [
    live.phases,
    serverBuild?.phase_index,
    serverBuild?.phase_id,
    serverBuild?.label,
    report?.phases,
    report?.prior_phases,
    busy,
  ]);

  const donePhases = phaseRows.filter((p) => p.status === 'done').length;
  const stepIndex = BUILD_STEPS.indexOf(latestStep as (typeof BUILD_STEPS)[number]);
  // Phases and files sit side by side only when the pane can give each a readable width.
  const [summaryRef, narrowSummary] = useNarrow<HTMLDivElement>(640);

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {/* The left group truncates rather than wrapping the actions onto a second row: `flex-1`'s
          zero basis means only the actions' own width can force a wrap, and `ml-auto` keeps them
          right-aligned if it ever does. */}
      <header className="flex min-h-[2.25rem] flex-wrap items-center gap-x-3 gap-y-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
          <h2 className="shrink-0 text-[13px] font-semibold text-fg">Build</h2>
          <span data-testid="build-status">
            <Badge tone={STATUS_TONES[status]} dot={status === 'in_progress'}>
              {STATUS_LABELS[status]}
            </Badge>
          </span>
          {busy ? (
            <span
              className="flex min-w-0 items-center gap-1.5 text-xs text-fg-muted"
              data-testid="build-progress"
            >
              <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-text" />
              <span className="truncate">
                {activityLabel ? `${activityLabel}…` : 'Generating…'}
              </span>
            </span>
          ) : null}
          {scopeLabel ? (
            <span
              className="min-w-0 truncate text-xs text-fg-subtle"
              data-testid="build-scope"
              title={report?.scope_reason || undefined}
            >
              {scopeLabel}
            </span>
          ) : null}
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={() => setShowPreview((open) => !open)}
            aria-pressed={showPreview}
            data-testid="toggle-preview"
            className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors duration-150 ${
              showPreview
                ? 'bg-fg/[0.08] text-fg'
                : 'text-fg-subtle hover:bg-fg/[0.04] hover:text-fg-muted'
            }`}
          >
            <PanelRight
              aria-hidden
              className={`h-3.5 w-3.5 ${showPreview ? 'text-brand-text' : ''}`}
              strokeWidth={1.75}
            />
            {showPreview ? 'Hide preview' : 'Preview'}
          </button>
          <span className="mx-0.5 h-4 w-px bg-edge" aria-hidden />
          {/* Approving is the human half of the completion gate: the automated one is strict, and
              when it holds a build the user can see is fine, this is what unblocks deploy without
              paying for another run. Hidden once the stage is already complete. */}
          {hasBuild && status !== 'complete' ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              data-testid="build-approve"
              title="Mark the build complete yourself — no agent run. Unblocks deploy."
              onClick={() =>
                intent.mutate({ stage: 'build', action: 'proceed', payload: { approve: true } })
              }
            >
              Approve
            </Button>
          ) : null}
          {/* The inverse of Approve: take back a "done" without touching the code on disk — what a
              demo project kept built to this stage needs before each showing. */}
          <ResetStageButton projectId={projectId} stage="build" status={status} disabled={busy} />
          {status === 'skipped' ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              data-testid="build-unskip"
              title="Undo the skip and restore the stage to what it was. Nothing is re-run."
              onClick={() => intent.mutate({ stage: 'build', action: 'unskip' })}
            >
              Un-skip
            </Button>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              data-testid="build-skip"
              onClick={() => intent.mutate({ stage: 'build', action: 'skip' })}
            >
              Skip
            </Button>
          )}
          <div className="flex items-center" ref={menuRef}>
            <Button
              size="sm"
              disabled={busy}
              data-testid="build-proceed"
              className={hasBuild ? 'rounded-r-none' : undefined}
              onClick={() => intent.mutate({ stage: 'build', action: 'proceed' })}
            >
              <Hammer aria-hidden className="h-3.5 w-3.5" strokeWidth={1.75} />
              {hasBuild ? 'Rebuild' : 'Build'}
            </Button>
            {hasBuild ? (
              <div className="relative">
                {/* A split button: the build variants hang off the primary action they modify. */}
                <Button
                  size="sm"
                  disabled={busy}
                  aria-label="More build options"
                  aria-expanded={menuOpen}
                  aria-haspopup="menu"
                  data-testid="build-menu"
                  className="rounded-l-none border-l border-l-fg-inverted/20 px-1.5"
                  onClick={() => setMenuOpen((v) => !v)}
                >
                  <MoreHorizontal aria-hidden className="h-3.5 w-3.5" strokeWidth={2} />
                </Button>
                {menuOpen ? (
                  <div
                    role="menu"
                    className="absolute right-0 z-30 mt-1.5 w-64 origin-top-right animate-ff-pop rounded-lg bg-surface-overlay p-1 shadow-pop"
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
                      className="block w-full rounded-md px-2.5 py-2 text-left text-[13px] text-fg transition-colors hover:bg-surface-raised"
                    >
                      Rebuild from scratch
                      <span className="mt-0.5 block text-2xs leading-snug text-fg-subtle">
                        Ignore existing feature code and regenerate it.
                      </span>
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      data-testid="build-force-plan"
                      onClick={() => {
                        setMenuOpen(false);
                        intent.mutate({
                          stage: 'build',
                          action: 'proceed',
                          payload: { scope: 'large' },
                        });
                      }}
                      className="block w-full rounded-md px-2.5 py-2 text-left text-[13px] text-fg transition-colors hover:bg-surface-raised"
                    >
                      Force a planned build
                      <span className="mt-0.5 block text-2xs leading-snug text-fg-subtle">
                        Plan the work in phases even if the change looks small.
                      </span>
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      </header>

      {/*
        The run summary — live telemetry, then the report — is capped to a share of the panel and
        scrolls inside it. It used to stack at full height above the editor, so a phased build in
        flight left the IDE and preview a strip at the bottom of the screen: the summary may grow,
        but never at the editor's expense.
      */}
      <div ref={summaryRef} className="flex max-h-[42%] shrink-0 flex-col gap-2 overflow-y-auto">
        {/* Live run telemetry. The plan step writes no files, so without this the panel looks
            frozen for a long time and then dumps a dozen tabs at once. */}
        {busy || liveFiles.length > 0 ? (
          <section
            className="relative shrink-0 overflow-hidden rounded-xl border border-edge bg-surface"
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
              <div aria-hidden className="absolute inset-x-0 top-0 h-px overflow-hidden">
                <div className="h-full w-1/4 bg-gradient-to-r from-transparent via-brand-text to-transparent motion-safe:animate-ff-sweep" />
              </div>
            ) : null}

            <div className={cn('flex items-center gap-3 px-3', busy ? 'py-2' : 'h-9')}>
              {busy ? (
                <AgentWorking compact label={activityLabel || 'Starting the build'} />
              ) : (
                <History
                  aria-hidden
                  className="h-3.5 w-3.5 shrink-0 text-fg-subtle"
                  strokeWidth={1.75}
                />
              )}
              <div className="min-w-0 flex-1">
                <p
                  className={cn(
                    'truncate font-medium',
                    busy ? 'text-[13px] text-fg' : 'text-xs text-fg-muted',
                  )}
                  data-testid="build-activity-label"
                >
                  {busy ? `${activityLabel || 'Starting the build'}…` : 'Last run'}
                </p>
                {busy && activityTarget ? (
                  <p className="truncate font-mono text-2xs text-fg-subtle">{activityTarget}</p>
                ) : null}
              </div>
              <span
                className="shrink-0 text-xs tabular-nums text-fg-subtle"
                data-testid="build-file-count"
              >
                {liveFiles.length} file{liveFiles.length === 1 ? '' : 's'} written
              </span>
              {busy && stepIndex >= 0 ? (
                // Filled up to the step in flight, so the bar reads as progress, not as a pointer.
                <span className="hidden shrink-0 items-center gap-1 sm:flex" aria-hidden>
                  {BUILD_STEPS.map((s, i) => (
                    <span
                      key={s}
                      title={STEP_LABELS[s]}
                      className={`h-1 w-5 rounded-full transition-colors duration-300 ${
                        i < stepIndex
                          ? 'bg-brand-text/50'
                          : i === stepIndex
                            ? 'bg-brand-text'
                            : 'bg-fg/[0.1]'
                      }`}
                    />
                  ))}
                </span>
              ) : null}
              {!busy && (phaseRows.length > 0 || liveFiles.length > 0) ? (
                <button
                  type="button"
                  aria-expanded={runDetailsOpen}
                  aria-label={runDetailsOpen ? 'Hide run details' : 'Show run details'}
                  title={runDetailsOpen ? 'Hide run details' : 'Show run details'}
                  onClick={() => setRunDetailsOpen((v) => !v)}
                  className="-mr-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-fg-faint transition-colors hover:bg-fg/[0.06] hover:text-fg"
                >
                  <ChevronDown
                    aria-hidden
                    className={cn(
                      'h-4 w-4 transition-transform duration-200',
                      runDetailsOpen && 'rotate-180',
                    )}
                    strokeWidth={1.75}
                  />
                </button>
              ) : null}
            </div>

            {phaseRows.length > 0 || liveFiles.length > 0 ? (
              <div
                className={cn(
                  'grid border-t border-edge',
                  // Folded while idle: still mounted (nothing about the run is lost), one click away.
                  !busy && !runDetailsOpen && 'hidden',
                  phaseRows.length > 0 && liveFiles.length > 0
                    ? narrowSummary
                      ? 'divide-y divide-edge'
                      : 'grid-cols-2 divide-x divide-edge'
                    : '',
                )}
              >
                {/* The phase rail. Rendered only when there are phases, so a pre-phase-56 report
                    leaves this section exactly as it was. Height-capped like the file list, so it
                    can never steal editor height. */}
                {phaseRows.length > 0 ? (
                  <div className="min-w-0 px-3 pb-2 pt-2">
                    <StripHeading count={`${donePhases}/${phaseRows.length}`}>Phases</StripHeading>
                    <ul
                      className="max-h-28 space-y-px overflow-auto"
                      data-testid="build-phase-list"
                    >
                      {phaseRows.map((phase, i) => (
                        <li
                          key={phase.id}
                          className={cn(
                            'flex h-6 items-center gap-2 text-xs',
                            // The first phase of a change sits under a hairline, below the history
                            // it kept.
                            i > 0 && phaseRows[i - 1].earlier && !phase.earlier
                              ? 'mt-1 border-t border-dashed border-edge pt-1'
                              : '',
                            phase.earlier ? 'opacity-55' : '',
                          )}
                          title={
                            phase.earlier ? 'From an earlier build — kept, not re-run' : undefined
                          }
                          data-testid="build-phase-row"
                          data-status={phase.status}
                          data-earlier={phase.earlier ? 'true' : undefined}
                        >
                          <span className="flex w-3.5 shrink-0 justify-center" aria-hidden>
                            <PhaseGlyph status={phase.status} />
                          </span>
                          <span
                            className={cn(
                              'truncate',
                              phase.status === 'running' ? 'text-fg' : 'text-fg-muted',
                            )}
                          >
                            {phase.title}
                          </span>
                          {phase.kind ? <Badge tone="neutral">{phase.kind}</Badge> : null}
                          {/* phase-64: a phase's evidence, on screen. "0 files · wrote nothing after
                              2 attempt(s)" is the line that was invisible when every phase read
                              `done`. */}
                          {phase.fileCount !== undefined ? (
                            <span
                              className={cn(
                                'ml-auto shrink-0 truncate text-2xs tabular-nums',
                                phase.fileCount === 0 ? 'text-danger' : 'text-fg-faint',
                              )}
                              data-testid="build-phase-evidence"
                            >
                              {phase.fileCount} file{phase.fileCount === 1 ? '' : 's'}
                              {phase.note ? ` · ${phase.note}` : ''}
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {liveFiles.length > 0 ? (
                  <div className="min-w-0 px-3 pb-2 pt-2">
                    <StripHeading>Files written</StripHeading>
                    <ul className="max-h-28 space-y-px overflow-auto" data-testid="build-file-list">
                      {liveFiles.map((path) => {
                        const { icon: Glyph, tone } = fileIcon(path);
                        return (
                          <li
                            key={path}
                            className={cn(
                              '-mx-1.5 flex h-6 min-w-0 items-center gap-1.5 rounded px-1.5',
                              // A file that lands mid-run glows once, so the eye catches the new
                              // arrival in a list that is otherwise only growing.
                              busy && 'motion-safe:animate-ff-flash',
                            )}
                          >
                            <span className="font-mono text-2xs text-success" aria-hidden>
                              +
                            </span>
                            <Glyph
                              aria-hidden
                              className={cn('h-3 w-3 shrink-0', tone)}
                              strokeWidth={1.75}
                            />
                            <button
                              type="button"
                              onClick={() => void openGeneratedFile(projectId, path)}
                              title={`Open ${path}`}
                              className="truncate font-mono text-2xs text-fg-muted transition-colors hover:text-fg hover:underline"
                            >
                              {path}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ) : null}
              </div>
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
              className="flex h-9 w-full items-center gap-2 px-3 text-left transition-colors hover:bg-fg/[0.025]"
            >
              <FileCheck2
                aria-hidden
                className="h-3.5 w-3.5 shrink-0 text-fg-subtle"
                strokeWidth={1.75}
              />
              <span className="text-xs font-medium text-fg-muted">Report</span>
              <span data-testid="build-boot-status">
                <Badge tone={BOOT_TONES[report.boot_status] ?? 'neutral'} dot>
                  {report.boot_status}
                </Badge>
              </span>
              {report.tests_passed !== null ? (
                <Badge tone={report.tests_passed ? 'success' : 'danger'}>
                  tests {report.tests_passed ? 'passed' : 'failed'}
                </Badge>
              ) : null}
              <span className="truncate text-xs text-fg-subtle">
                {report.files_changed.length} file{report.files_changed.length === 1 ? '' : 's'}{' '}
                changed
                {report.commit ? (
                  <span className="ml-2 font-mono text-2xs text-fg-faint">
                    {report.commit.slice(0, 8)}
                  </span>
                ) : null}
              </span>
              <span className="ml-auto text-fg-faint" aria-hidden>
                <ChevronDown
                  className={cn(
                    'h-4 w-4 transition-transform duration-200',
                    reportOpen && 'rotate-180',
                  )}
                  strokeWidth={1.75}
                />
              </span>
            </button>
            {reportOpen ? (
              <div className="space-y-2.5 border-t border-edge px-3 py-3 text-[13px] leading-relaxed">
                {report.summary ? <p className="text-fg-muted">{report.summary}</p> : null}
                {/* Why the build stopped short, in the report's own words (phase-55's vocabulary,
                    plus phase-56's `phases_incomplete`). Absent on pre-rework reports. */}
                {report.outcome && report.outcome !== 'complete' && report.stop_reason ? (
                  <p
                    className="rounded-md bg-warning/[0.08] px-2.5 py-1.5 text-xs text-warning"
                    data-testid="build-stop-reason"
                  >
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
                  <ul className="list-disc space-y-0.5 pl-4 text-xs text-warning marker:text-warning/60">
                    {report.follow_ups.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                  </ul>
                ) : null}
                {report.notes.length > 0 ? (
                  <ul className="list-disc space-y-0.5 pl-4 text-xs text-fg-subtle marker:text-fg-faint">
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
            className="flex shrink-0 items-center gap-2 rounded-xl border border-dashed border-edge-strong px-3 py-2.5 text-xs text-fg-subtle"
            data-testid="build-empty"
          >
            <Hammer aria-hidden className="h-3.5 w-3.5 shrink-0 text-fg-faint" strokeWidth={1.75} />
            <span>
              No build yet — press <span className="font-medium text-fg-muted">Build</span> to
              generate the app onto the skeleton, then refine it from the Assistant.
            </span>
          </p>
        )}
      </div>

      {/*
        Live generation (IDE stream) and the preview, side by side and DRAGGABLE (phase-61). This
        used to be `grid-rows-2 xl:grid-cols-2`, i.e. a fixed 50/50 inside a column that was already
        the narrowest region on screen — which is how both ended up ~200px wide and unreadable.
        Either pane can also take the whole window; the subtree stays mounted through that, so the
        preview iframe does not reload and terminal/editor state survives.
      */}
      <Split
        id={layoutId(projectId, 'build-center')}
        direction="horizontal"
        // Must match the panes actually rendered — the library keys saved layouts by this list.
        panelIds={showPreview ? ['ide', 'preview'] : ['ide']}
        className="min-h-0 flex-1"
      >
        <Pane id="ide" minSize={25}>
          <FocusOverlay
            open={focused === 'ide'}
            title="Files & editor"
            onClose={() => setFocused(null)}
          >
            <div className="relative flex min-h-0 flex-1 flex-col">
              <div className="absolute right-1.5 top-1.5 z-10">
                <FocusButton
                  label="Files & editor"
                  testId="focus-ide"
                  onClick={() => setFocused((f) => (f === 'ide' ? null : 'ide'))}
                />
              </div>
              <Suspense
                fallback={
                  <div className="flex h-full items-center justify-center rounded-xl border border-edge bg-surface text-fg-faint">
                    <Spinner />
                  </div>
                }
              >
                <Ide projectId={projectId} />
              </Suspense>
            </div>
          </FocusOverlay>
        </Pane>
        {showPreview ? (
          <>
            <PaneHandle direction="horizontal" />
            <Pane id="preview" defaultSize={42} minSize={20}>
              <FocusOverlay
                open={focused === 'preview'}
                title="Preview"
                onClose={() => setFocused(null)}
              >
                <div className="relative flex min-h-0 flex-1 flex-col">
                  <div className="absolute right-1.5 top-2 z-10">
                    <FocusButton
                      label="Preview"
                      testId="focus-preview"
                      onClick={() => setFocused((f) => (f === 'preview' ? null : 'preview'))}
                    />
                  </div>
                  <Suspense
                    fallback={
                      <div className="flex h-full items-center justify-center rounded-xl border border-edge bg-surface text-fg-faint">
                        <Spinner />
                      </div>
                    }
                  >
                    <Preview projectId={projectId} />
                  </Suspense>
                </div>
              </FocusOverlay>
            </Pane>
          </>
        ) : null}
      </Split>
    </div>
  );
}

export default BuildPanel;
