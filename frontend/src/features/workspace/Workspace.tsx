import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ChevronRight,
  Database,
  LayoutPanelLeft,
  MessageSquareText,
  Rows3,
  SquareTerminal,
  Trash2,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { ErrorBoundary } from '../../components/ErrorBoundary';
import { ConfirmDialog, EmptyState, IconButton, Skeleton, Spinner } from '../../components/ui';
import { cn } from '../../lib/cn';
import { ApiError } from '../../lib/apiClient';
import { advanceCursor } from '../../lib/eventCursor';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import { useUiStore } from '../../lib/stores/uiStore';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import type { IntentAction, Stage, StageStatus } from '../../lib/types';
import { useDeleteProject } from '../dashboard/useDeleteProject';
import { ChatPanel } from './ChatPanel';
import { StageNavigator } from './StageNavigator';
import { StagePanel } from './StagePanel';
import { ResetDeployDialog } from './StageReset';
import { resetNeedsConfirm, useStageReset } from './useStageReset';
import { Pane, PaneHandle, Split } from './layout/SplitPane';
import { layoutId, resetLayouts } from './layout/layoutStorage';
import { getProject, listStages, submitIntent } from './api';

// xterm is heavy and only needed once the drawer is opened — keep it out of the main chunk.
const Terminal = lazy(() => import('../ide/Terminal'));
// Data browsing is a workspace *view*, not a stage — it is available whatever stage you are on.
const DataBrowser = lazy(() => import('../data/DataBrowser'));
// Spend is always relevant while work runs, so it sits alongside the stage navigator.
const CostPanel = lazy(() => import('../cost/CostPanel'));

/**
 * A view toggle in the project bar. Pressed reads as a quiet fill, not a brand-coloured button:
 * four of these can be on at once, and four filled indigo buttons say nothing about which matters.
 */
function ToolToggle({
  label,
  icon: Icon,
  on,
  onClick,
  testId,
}: {
  label: string;
  icon: LucideIcon;
  on: boolean;
  onClick: () => void;
  testId?: string;
}): JSX.Element {
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={label}
      title={label}
      data-testid={testId}
      onClick={onClick}
      className={cn(
        'inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors duration-150',
        on ? 'bg-fg/[0.08] text-fg' : 'text-fg-subtle hover:bg-fg/[0.04] hover:text-fg-muted',
      )}
    >
      <Icon
        aria-hidden
        className={cn('h-3.5 w-3.5', on ? 'text-brand-text' : 'text-current')}
        strokeWidth={1.75}
      />
      {/* Icon-only below lg, where the bar has to share the row with a long project name. */}
      <span aria-hidden className="hidden lg:inline">
        {label}
      </span>
    </button>
  );
}

/** A rail section heading — small, sentence case, no box around it. */
function RailHeading({ children }: { children: string }): JSX.Element {
  return <h2 className="px-3 pb-1.5 pt-3 text-2xs font-medium text-fg-faint">{children}</h2>;
}

export default function Workspace(): JSX.Element {
  const { id } = useParams();
  const projectId = id ?? '';
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const activeStage = useWorkspaceStore((s) => s.activeStage);
  const setActiveStage = useWorkspaceStore((s) => s.setActiveStage);
  const openProject = useWorkspaceStore((s) => s.openProject);
  const storeProjectId = useWorkspaceStore((s) => s.projectId);

  // Rail visibility is persisted (uiStore) so a user's "focus the editor" choice sticks.
  const stagesOpen = useUiStore((s) => s.stagesOpen);
  const assistantOpen = useUiStore((s) => s.assistantOpen);
  const toggleStages = useUiStore((s) => s.toggleStages);
  const toggleAssistant = useUiStore((s) => s.toggleAssistant);

  const [terminalOpen, setTerminalOpen] = useState(false);
  const [view, setView] = useState<'stage' | 'data'>('stage');
  const [confirmDelete, setConfirmDelete] = useState(false);
  // The stage whose reset is waiting on confirmation (only deploy's destroys anything).
  const [confirmReset, setConfirmReset] = useState<Stage | null>(null);
  // Bumped by "Reset layout" to force the panel groups to remount and re-read their defaults.
  const [layoutNonce, setLayoutNonce] = useState(0);
  const { remove, isDeleting } = useDeleteProject();

  const projectQuery = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => getProject(projectId),
    enabled: Boolean(projectId),
  });

  const stagesQuery = useQuery({
    queryKey: ['stages', projectId],
    queryFn: () => listStages(projectId),
    enabled: Boolean(projectId),
  });

  // Seed the active stage from the project's current stage on first entry.
  useEffect(() => {
    if (projectQuery.data && storeProjectId !== projectId) {
      openProject(projectId, projectQuery.data.current_stage);
    }
  }, [projectQuery.data, storeProjectId, projectId, openProject]);

  // Subscribe to the project's realtime channel; the client resumes via last_seq on reconnect.
  const connect = useRealtimeStore((s) => s.connect);
  const disconnect = useRealtimeStore((s) => s.disconnect);
  const events = useRealtimeStore((s) => s.events);
  const processed = useRef(0);
  useEffect(() => {
    processed.current = 0;
    connect(projectId);
    return () => disconnect();
  }, [projectId, connect, disconnect]);

  // Any stage.transition (from this client or elsewhere) refreshes the navigator + chat.
  // `project.deleted` means the project was removed from another tab or session — leave, rather
  // than sit on a workspace whose every subsequent request will 404.
  useEffect(() => {
    // `seq`-cursored, not index-cursored: the event ring is bounded, so a build's traffic saturates
    // it and an index cursor would stop advancing — stage transitions arriving after that point
    // would never refresh the navigator. See lib/eventCursor.ts.
    const { fresh, nextSeq } = advanceCursor(events, processed.current);
    processed.current = nextSeq;
    const mine = fresh.filter((e) => e.project_id === projectId);
    if (mine.some((e) => e.event === 'stage.transition')) {
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
    }
    if (mine.some((e) => e.event === 'project.deleted')) {
      toast({
        title: 'Project deleted',
        description: 'This project no longer exists.',
        variant: 'warning',
      });
      navigate('/dashboard');
    }
  }, [events, projectId, queryClient, navigate]);

  const navAction = useMutation({
    mutationFn: (vars: { stage: Stage; action: IntentAction }) =>
      submitIntent(projectId, { stage: vars.stage, action: vars.action }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['artifacts', projectId] });
    },
    onError: (err) => {
      const message = err instanceof ApiError ? err.message : 'Action failed';
      toast({ title: 'Stage action rejected', description: message, variant: 'error' });
    },
  });

  // A reset is answered by its own hook: it refreshes every panel it can change (a deploy reset
  // blanks three of them), which the generic stage action does not.
  const reset = useStageReset(projectId);
  function onStageAction(stage: Stage, action: IntentAction): void {
    if (action !== 'reset') {
      navAction.mutate({ stage, action });
    } else if (resetNeedsConfirm(stage)) {
      setConfirmReset(stage);
    } else {
      reset.mutate(stage);
    }
  }

  /*
    The panel library keys a saved layout by the ids it is told are mounted, and warns that they
    must match the Panels actually rendered. Passing one fixed list meant every combination of open
    rails shared a single storage slot — so dragging a seam with the assistant hidden rewrote the
    arrangement for when it was showing. Derive the list instead: each combination then remembers
    its own sizes.
  */
  const columnIds = useMemo(() => {
    const ids: string[] = [];
    if (stagesOpen) ids.push('rail');
    ids.push('main');
    if (assistantOpen) ids.push('assistant');
    return ids;
  }, [stagesOpen, assistantOpen]);
  const shellIds = useMemo(() => (terminalOpen ? ['body', 'terminal'] : ['body']), [terminalOpen]);

  const statusByStage = useMemo(() => {
    const map: Partial<Record<Stage, StageStatus>> = {};
    for (const s of stagesQuery.data ?? []) {
      map[s.stage] = s.status;
    }
    return map;
  }, [stagesQuery.data]);

  if (projectQuery.isLoading) {
    // The workbench's own bones — bar, rail, centre, assistant — so the load never jumps layout.
    return (
      <div className="flex h-full flex-col gap-2" aria-hidden>
        <div className="flex h-10 items-center gap-3 px-1">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="ml-auto h-7 w-72" />
        </div>
        <div className="grid min-h-0 flex-1 grid-cols-[13rem_minmax(0,1fr)_18rem] gap-2">
          <Skeleton className="h-full rounded-xl" />
          <Skeleton className="h-full rounded-xl" />
          <Skeleton className="h-full rounded-xl" />
        </div>
      </div>
    );
  }

  if (projectQuery.isError || !projectQuery.data) {
    return (
      <EmptyState
        title="Project unavailable"
        description="This project could not be loaded. It may have been deleted."
      />
    );
  }

  return (
    <div className="flex h-full flex-col gap-2">
      <header className="flex h-10 shrink-0 items-center justify-between gap-3 pl-1.5">
        <div className="flex min-w-0 items-center gap-1.5 text-[13px]">
          <Link
            to="/dashboard"
            className="shrink-0 rounded px-1 text-fg-subtle transition-colors hover:text-fg"
          >
            Projects
          </Link>
          <ChevronRight aria-hidden className="h-3.5 w-3.5 shrink-0 text-fg-faint" />
          <h1 className="truncate px-1 text-[13px] font-semibold text-fg">
            {projectQuery.data.name}
          </h1>
          <p className="ml-2 hidden truncate text-xs text-fg-faint 2xl:block">
            Any stage is clickable — start anywhere, skip, or jump back.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <ToolToggle
            label="Stages"
            icon={Rows3}
            on={stagesOpen}
            onClick={toggleStages}
            testId="toggle-stages"
          />
          <ToolToggle
            label="Data"
            icon={Database}
            on={view === 'data'}
            onClick={() => setView((v) => (v === 'data' ? 'stage' : 'data'))}
            testId="toggle-data"
          />
          <ToolToggle
            label="Terminal"
            icon={SquareTerminal}
            on={terminalOpen}
            onClick={() => setTerminalOpen((open) => !open)}
            testId="toggle-terminal"
          />
          <ToolToggle
            label="Assistant"
            icon={MessageSquareText}
            on={assistantOpen}
            onClick={toggleAssistant}
            testId="toggle-assistant"
          />
          <span className="mx-1.5 h-4 w-px bg-edge" aria-hidden />
          <IconButton
            label="Reset layout"
            data-testid="workspace-reset-layout"
            onClick={() => {
              resetLayouts(projectId);
              setLayoutNonce((n) => n + 1);
            }}
          >
            <LayoutPanelLeft aria-hidden strokeWidth={1.75} />
          </IconButton>
          <IconButton
            label="Delete project"
            tooltipAlign="end"
            data-testid="workspace-delete"
            className="hover:bg-danger/10 hover:text-danger"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 aria-hidden strokeWidth={1.75} />
          </IconButton>
        </div>
      </header>

      {/*
        Resizable, not fixed (phase-61). Every region here used to have a hard-coded size, which
        left the centre column as the only elastic one — and it subdivides again for the IDE and
        the preview, so with both open neither was readable. Sizes persist per project.
      */}
      <Split
        // Remounting on reset is what makes the panels re-read (now-empty) storage and fall back
        // to their default sizes; the library reads defaultLayout once, on mount.
        key={layoutNonce}
        id={layoutId(projectId, 'shell')}
        direction="vertical"
        panelIds={shellIds}
        className="flex-1"
      >
        <Pane id="body" minSize={30}>
          <Split
            id={layoutId(projectId, 'columns')}
            direction="horizontal"
            panelIds={columnIds}
            className="flex-1 gap-0"
          >
            {stagesOpen ? (
              <>
                <Pane id="rail" defaultSize={17} minSize="188px" maxSize={34}>
                  {/* One flat column with headed sections — not two cards stacked in a card. */}
                  <aside className="flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden rounded-xl border border-edge bg-surface">
                    <RailHeading>Pipeline</RailHeading>
                    <div className="px-1.5 pb-2">
                      <StageNavigator
                        statusByStage={statusByStage}
                        activeStage={activeStage}
                        onSelect={setActiveStage}
                        onAction={onStageAction}
                        busy={navAction.isPending || reset.isPending}
                      />
                    </div>
                    <div className="mx-3 border-t border-edge" />
                    <RailHeading>Cost</RailHeading>
                    <div className="px-3 pb-3">
                      <Suspense
                        fallback={
                          <div className="flex justify-center py-4 text-fg-faint">
                            <Spinner />
                          </div>
                        }
                      >
                        <CostPanel projectId={projectId} />
                      </Suspense>
                    </div>
                  </aside>
                </Pane>
                <PaneHandle direction="horizontal" />
              </>
            ) : null}

            <Pane id="main" minSize={30}>
              <div className="min-h-0 flex-1 overflow-auto px-1">
                {view === 'data' ? (
                  <Suspense
                    fallback={
                      <div className="flex h-full items-center justify-center">
                        <Spinner />
                      </div>
                    }
                  >
                    <DataBrowser projectId={projectId} />
                  </Suspense>
                ) : (
                  // A crash in one stage panel stays contained + clears when you switch stages.
                  <ErrorBoundary resetKey={activeStage}>
                    <StagePanel projectId={projectId} stage={activeStage} />
                  </ErrorBoundary>
                )}
              </div>
            </Pane>

            {assistantOpen ? (
              <>
                <PaneHandle direction="horizontal" />
                <Pane id="assistant" defaultSize={23} minSize="248px" maxSize={45}>
                  <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-edge bg-surface">
                    <ChatPanel projectId={projectId} activeStage={activeStage} />
                  </section>
                </Pane>
              </>
            ) : null}
          </Split>
        </Pane>

        {terminalOpen ? (
          <>
            <PaneHandle direction="vertical" />
            <Pane id="terminal" defaultSize={28} minSize={10}>
              {/* Fades in on open, and its frame lights while the terminal has the keyboard — the
                  one pane where knowing where your keystrokes go matters most. */}
              <section className="flex min-h-0 flex-1 animate-ff-fade flex-col overflow-hidden rounded-xl border border-edge bg-surface transition-colors duration-150 focus-within:border-brand/40">
                <Suspense
                  fallback={
                    <div className="flex h-full items-center justify-center text-fg-faint">
                      <Spinner />
                    </div>
                  }
                >
                  <Terminal projectId={projectId} />
                </Suspense>
              </section>
            </Pane>
          </>
        ) : null}
      </Split>

      <ResetDeployDialog
        open={confirmReset !== null}
        loading={reset.isPending}
        onClose={() => setConfirmReset(null)}
        onConfirm={() => {
          if (confirmReset) reset.mutate(confirmReset, { onSettled: () => setConfirmReset(null) });
        }}
      />

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => remove(projectId, { onDeleted: () => navigate('/dashboard') })}
        loading={isDeleting}
        title="Delete project"
        confirmLabel="Delete forever"
        confirmPhrase={projectQuery.data.name}
        description={
          <>
            <strong className="font-medium text-fg">{projectQuery.data.name}</strong> and everything
            it owns will be permanently removed. This cannot be undone.
          </>
        }
      >
        <ul className="list-disc space-y-1 rounded-lg bg-fg/[0.04] py-2.5 pl-7 pr-3 text-xs text-fg-muted marker:text-fg-faint">
          <li>Sandbox container and its workspace volume</li>
          <li>The project&apos;s application database and all its data</li>
          <li>Conversation, designs, requirements, tests and deployments</li>
        </ul>
      </ConfirmDialog>
    </div>
  );
}
