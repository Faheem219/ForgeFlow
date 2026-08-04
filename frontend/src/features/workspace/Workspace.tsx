import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ErrorBoundary } from '../../components/ErrorBoundary';
import { Button, ConfirmDialog, EmptyState, Panel, Spinner } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import { useUiStore } from '../../lib/stores/uiStore';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import type { IntentAction, Stage, StageStatus } from '../../lib/types';
import { useDeleteProject } from '../dashboard/useDeleteProject';
import { ChatPanel } from './ChatPanel';
import { StageNavigator } from './StageNavigator';
import { StagePanel } from './StagePanel';
import { getProject, listStages, submitIntent } from './api';

// xterm is heavy and only needed once the drawer is opened — keep it out of the main chunk.
const Terminal = lazy(() => import('../ide/Terminal'));
// Data browsing is a workspace *view*, not a stage — it is available whatever stage you are on.
const DataBrowser = lazy(() => import('../data/DataBrowser'));
// Spend is always relevant while work runs, so it sits alongside the stage navigator.
const CostPanel = lazy(() => import('../cost/CostPanel'));

/** A compact toolbar toggle: filled when the target is showing, quiet when hidden. */
function ToolToggle({
  label,
  on,
  onClick,
  testId,
}: {
  label: string;
  on: boolean;
  onClick: () => void;
  testId?: string;
}): JSX.Element {
  return (
    <Button
      size="sm"
      variant={on ? 'primary' : 'secondary'}
      aria-pressed={on}
      data-testid={testId}
      onClick={onClick}
    >
      {label}
    </Button>
  );
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
    const fresh = events.slice(processed.current);
    processed.current = events.length;
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
      navigate('/');
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

  const statusByStage = useMemo(() => {
    const map: Partial<Record<Stage, StageStatus>> = {};
    for (const s of stagesQuery.data ?? []) {
      map[s.stage] = s.status;
    }
    return map;
  }, [stagesQuery.data]);

  if (projectQuery.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
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
    <div className="flex h-[calc(100vh-6.5rem)] flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold text-slate-100">
            {projectQuery.data.name}
          </h1>
          <p className="text-sm text-slate-400">
            Any stage is clickable — start anywhere, skip, or jump back.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ToolToggle
            label="Stages"
            on={stagesOpen}
            onClick={toggleStages}
            testId="toggle-stages"
          />
          <span className="mx-1 hidden h-5 w-px bg-slate-800 sm:block" aria-hidden />
          <ToolToggle
            label="Data"
            on={view === 'data'}
            onClick={() => setView((v) => (v === 'data' ? 'stage' : 'data'))}
            testId="toggle-data"
          />
          <ToolToggle
            label="Terminal"
            on={terminalOpen}
            onClick={() => setTerminalOpen((open) => !open)}
            testId="toggle-terminal"
          />
          <span className="mx-1 hidden h-5 w-px bg-slate-800 sm:block" aria-hidden />
          <ToolToggle
            label="Assistant"
            on={assistantOpen}
            onClick={toggleAssistant}
            testId="toggle-assistant"
          />
          <span className="mx-1 hidden h-5 w-px bg-slate-800 sm:block" aria-hidden />
          <Button
            size="sm"
            variant="ghost"
            data-testid="workspace-delete"
            className="text-red-400 hover:bg-red-500/10"
            onClick={() => setConfirmDelete(true)}
          >
            Delete
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 gap-4">
        {stagesOpen ? (
          <div className="flex w-60 shrink-0 flex-col gap-4 overflow-auto">
            <Panel title="Stages">
              <StageNavigator
                statusByStage={statusByStage}
                activeStage={activeStage}
                onSelect={setActiveStage}
                onAction={(stage, action) => navAction.mutate({ stage, action })}
                busy={navAction.isPending}
              />
            </Panel>
            <Panel title="Cost">
              <Suspense
                fallback={
                  <div className="flex justify-center py-4">
                    <Spinner />
                  </div>
                }
              >
                <CostPanel projectId={projectId} />
              </Suspense>
            </Panel>
          </div>
        ) : null}

        <div className="min-h-0 min-w-0 flex-1 overflow-auto">
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

        {assistantOpen ? (
          <section className="flex w-80 shrink-0 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40">
            <ChatPanel projectId={projectId} activeStage={activeStage} />
          </section>
        ) : null}
      </div>

      {terminalOpen ? (
        <section className="h-64 shrink-0 overflow-hidden rounded-xl border border-slate-800 bg-slate-900/60">
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center">
                <Spinner />
              </div>
            }
          >
            <Terminal projectId={projectId} />
          </Suspense>
        </section>
      ) : null}

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => remove(projectId, { onDeleted: () => navigate('/') })}
        loading={isDeleting}
        title="Delete project"
        confirmLabel="Delete forever"
        confirmPhrase={projectQuery.data.name}
        description={
          <>
            <strong className="font-medium text-slate-100">{projectQuery.data.name}</strong> and
            everything it owns will be permanently removed. This cannot be undone.
          </>
        }
      >
        <ul className="space-y-1 rounded-lg bg-slate-950/60 px-3 py-2.5 text-xs text-slate-400">
          <li>· Sandbox container and its workspace volume</li>
          <li>· The project&apos;s application database and all its data</li>
          <li>· Conversation, designs, requirements, tests and deployments</li>
        </ul>
      </ConfirmDialog>
    </div>
  );
}
