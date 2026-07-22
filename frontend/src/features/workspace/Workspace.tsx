import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';

import { ErrorBoundary } from '../../components/ErrorBoundary';
import { Button, EmptyState, Panel, Spinner } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import type { IntentAction, Stage, StageStatus } from '../../lib/types';
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

export default function Workspace(): JSX.Element {
  const { id } = useParams();
  const projectId = id ?? '';
  const queryClient = useQueryClient();

  const activeStage = useWorkspaceStore((s) => s.activeStage);
  const setActiveStage = useWorkspaceStore((s) => s.setActiveStage);
  const openProject = useWorkspaceStore((s) => s.openProject);
  const storeProjectId = useWorkspaceStore((s) => s.projectId);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [view, setView] = useState<'stage' | 'data'>('stage');

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
  useEffect(() => {
    const fresh = events.slice(processed.current);
    processed.current = events.length;
    if (fresh.some((e) => e.event === 'stage.transition' && e.project_id === projectId)) {
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
    }
  }, [events, projectId, queryClient]);

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
    <div className="flex h-[calc(100vh-7rem)] flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">{projectQuery.data.name}</h1>
          <p className="text-sm text-slate-400">
            Any stage is clickable — start anywhere, skip, or jump back.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant={view === 'data' ? 'primary' : 'secondary'}
            data-testid="toggle-data"
            aria-pressed={view === 'data'}
            onClick={() => setView((v) => (v === 'data' ? 'stage' : 'data'))}
          >
            Data
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setTerminalOpen((open) => !open)}
            aria-pressed={terminalOpen}
          >
            {terminalOpen ? 'Hide terminal' : 'Terminal'}
          </Button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[16rem_minmax(0,1fr)_22rem]">
        <div className="flex min-h-0 flex-col gap-4 overflow-auto">
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

        <div className="min-h-0 overflow-auto">
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

        <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40">
          <ChatPanel projectId={projectId} activeStage={activeStage} />
        </section>
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
    </div>
  );
}
