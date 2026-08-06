import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { AgentWorking } from '../../components/AgentWorking';
import { Badge, Button } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import type {
  DeployMode,
  IntentRequest,
  Stage,
  StageStatus,
  TopologyNodeId,
} from '../../lib/types';
import { listStages, submitIntent } from '../workspace/api';
import { STATUS_LABELS, STATUS_TONES, prereqUnmet } from '../workspace/stageMeta';
import { NodeDrawer } from './NodeDrawer';
import { Topology } from './Topology';
import { getLatestDeployment } from './api';
import {
  applyDeployEvent,
  initialLiveState,
  nodeViews,
  snapshotOf,
  type LiveDeployState,
} from './topologyModel';

const OVERALL_TONES = {
  live: 'success',
  degraded: 'warning',
  failed: 'danger',
  pending: 'neutral',
} as const;

const MODES: { value: DeployMode; label: string; hint: string }[] = [
  { value: 'seamless', label: 'Seamless', hint: "Deploy with ForgeFlow's provider accounts." },
  { value: 'byo', label: 'Bring your own', hint: 'Deploy into your own Vercel/Render accounts.' },
];

/**
 * The Deploy stage: an n8n-style map of what this project actually runs on.
 *
 * It hydrates from the last `Deployment` (so a reload shows the true topology immediately) and then
 * follows live `deploy.status` events as a deploy progresses. Deploying itself goes through the
 * conductor, which owns the deploy⇐build prereq — the disabled buttons here are only a hint.
 */
export function DeployPanel({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<DeployMode>('seamless');
  const [selected, setSelected] = useState<TopologyNodeId | null>(null);

  const stagesQuery = useQuery({
    queryKey: ['stages', projectId],
    queryFn: () => listStages(projectId),
  });
  const deploymentQuery = useQuery({
    queryKey: ['deployment-latest', projectId],
    queryFn: () => getLatestDeployment(projectId),
  });

  const deployment = deploymentQuery.data ?? null;
  const stageStatus = stagesQuery.data?.find((s) => s.stage === 'deploy')?.status ?? 'empty';
  const statusByStage = useMemo(() => {
    const map: Partial<Record<Stage, StageStatus>> = {};
    for (const s of stagesQuery.data ?? []) map[s.stage] = s.status;
    return map;
  }, [stagesQuery.data]);
  const missingPrereq = prereqUnmet('deploy', statusByStage);

  // --- live graph state: seed from the record, then fold in deploy.status events ----------------
  const events = useRealtimeStore((s) => s.events);
  const [live, setLive] = useState<LiveDeployState>(() => initialLiveState(deployment));
  const consumed = useRef(0);

  useEffect(() => {
    // A fresh record (first load, or a deploy that just finished) re-seeds the graph.
    setLive(initialLiveState(deployment));
  }, [deployment]);

  useEffect(() => {
    if (events.length <= consumed.current) {
      consumed.current = events.length; // the channel reset (project switch / reconnect)
      return;
    }
    const fresh = events.slice(consumed.current);
    consumed.current = events.length;
    const payloads = fresh.filter((e) => e.event === 'deploy.status').map((e) => e.payload);
    if (payloads.length === 0) return;

    setLive((current) => payloads.reduce(applyDeployEvent, current));
    if (payloads.some((p) => p.step === 'health')) {
      void queryClient.invalidateQueries({ queryKey: ['deployment-latest', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['deploy-logs', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
    }
  }, [events, projectId, queryClient]);

  const snapshot = useMemo(() => snapshotOf(deployment), [deployment]);
  const views = useMemo(() => nodeViews(snapshot, live), [snapshot, live]);
  const selectedView = views.find((v) => v.id === selected) ?? null;

  const intent = useMutation({
    mutationFn: (body: IntentRequest) => submitIntent(projectId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['deployment-latest', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['deploy-logs', projectId] });
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Deploy failed';
      toast({ title: 'Deploy rejected', description, variant: 'error' });
    },
  });

  const deployDisabledReason = missingPrereq
    ? `Deploy needs a completed ${missingPrereq} first.`
    : null;
  const busy = intent.isPending || live.running;

  function runDeploy(): void {
    intent.mutate({ stage: 'deploy', action: 'proceed', payload: { mode } });
  }

  const overall = live.overall ?? deployment?.status ?? null;
  const hasDeployed = Boolean(deployment);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-fg">
          Deploy
          <span data-testid="deploy-stage-status">
            <Badge tone={STATUS_TONES[stageStatus]}>{STATUS_LABELS[stageStatus]}</Badge>
          </span>
          {overall ? (
            <span data-testid="deploy-overall-status">
              <Badge tone={OVERALL_TONES[overall]}>{overall}</Badge>
            </span>
          ) : null}
        </span>

        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-lg border border-edge-strong p-0.5" role="group">
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                title={m.hint}
                data-testid={`deploy-mode-${m.value}`}
                aria-pressed={mode === m.value}
                onClick={() => setMode(m.value)}
                className={`rounded-md px-2.5 py-1 text-xs transition ${
                  mode === m.value ? 'bg-brand text-white' : 'text-fg-muted hover:bg-surface-raised'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <Button
            size="sm"
            variant="secondary"
            disabled={intent.isPending}
            data-testid="deploy-skip"
            onClick={() => intent.mutate({ stage: 'deploy', action: 'skip' })}
          >
            Skip
          </Button>
          <Button
            size="sm"
            disabled={Boolean(deployDisabledReason) || busy}
            title={deployDisabledReason ?? undefined}
            data-testid="deploy-run"
            onClick={runDeploy}
          >
            {busy ? 'Deploying…' : hasDeployed ? 'Redeploy' : 'Deploy'}
          </Button>
        </div>
      </header>

      {deployDisabledReason ? (
        <p
          className="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning"
          data-testid="deploy-prereq-warning"
        >
          {deployDisabledReason} It is the one ordering ForgeFlow enforces — everything else is
          optional.
        </p>
      ) : null}

      {/* Above the grid, so it survives selecting a node mid-deploy — the indicator has to answer
          "is it still working?" for the whole run, whatever else you are looking at. */}
      {busy ? (
        <section
          className="flex shrink-0 justify-center rounded-xl border border-edge bg-surface py-5"
          data-testid="deploy-progress"
        >
          <AgentWorking label="Deploying" />
        </section>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[2fr_1fr]">
        <div className="min-h-0 overflow-hidden rounded-xl border border-edge bg-surface">
          <Topology snapshot={snapshot} state={live} onSelect={setSelected} />
        </div>

        <div className="min-h-0">
          {selectedView ? (
            <NodeDrawer
              projectId={projectId}
              node={selectedView}
              onClose={() => setSelected(null)}
              onRedeploy={runDeploy}
              redeployDisabledReason={deployDisabledReason}
              redeploying={busy}
            />
          ) : (
            <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-edge p-4 text-center text-xs text-fg-subtle">
              Select a component to see its URL, wiring and logs.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default DeployPanel;
