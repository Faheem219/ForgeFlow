import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { AgentWorking } from '../../components/AgentWorking';
import { Badge, Button, Modal } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import { toast } from '../../lib/stores/toastStore';
import type {
  CredentialKind,
  DeployMode,
  IntentRequest,
  Stage,
  StageStatus,
  TopologyNodeId,
} from '../../lib/types';
import { listCredentials } from '../settings/api';
import { listStages, submitIntent } from '../workspace/api';
import { nextStage, STATUS_LABELS, STATUS_TONES, prereqUnmet } from '../workspace/stageMeta';
import { NodeDrawer } from './NodeDrawer';
import { Topology } from './Topology';
import { deleteDeployment, getDeployConfig, getLatestDeployment } from './api';
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
  deleted: 'neutral',
} as const;

/** Provider-credential names as a person would say them, for the BYO readiness rows. */
const CREDENTIAL_LABELS: Record<CredentialKind, string> = {
  vercel: 'Vercel token',
  render: 'Render token',
  mongo_uri: 'MongoDB URI',
  stitch: 'Stitch',
  figma: 'Figma',
};

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

  const setActiveStage = useWorkspaceStore((s) => s.setActiveStage);
  const intent = useMutation({
    mutationFn: (body: IntentRequest) => submitIntent(projectId, body),
    onSuccess: (data, body) => {
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['deployment-latest', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['deploy-logs', projectId] });
      // `skip` always advances; `proceed` only when the deploy actually landed — a rejected/failed
      // attempt (still an HTTP 200, see `to_status`) leaves you here to see why and retry, rather
      // than bouncing forward to a Validate stage whose hard-prereq isn't even met yet.
      if (body.action === 'skip' || (body.action === 'proceed' && data.to_status === 'complete')) {
        const next = nextStage('deploy');
        if (next) setActiveStage(next);
      }
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Deploy failed';
      toast({ title: 'Deploy rejected', description, variant: 'error' });
    },
  });

  // --- BYO readiness -------------------------------------------------------------------------
  // Which credentials a BYO deploy needs is a server fact: the backend provider is configurable, so
  // asking a Render-backed instance for a Vercel token would be wrong in exactly the case this
  // check exists to catch. Only fetched while BYO is selected — seamless uses platform credentials
  // and none of this is the user's business there.
  const isByo = mode === 'byo';
  const configQuery = useQuery({
    queryKey: ['deploy-config', projectId],
    queryFn: () => getDeployConfig(projectId),
    enabled: isByo,
  });
  const credentialsQuery = useQuery({
    queryKey: ['credentials'],
    queryFn: listCredentials,
    enabled: isByo,
  });

  const readiness = useMemo(() => {
    const config = configQuery.data;
    if (!config) return null;
    // Anything that is not a list of credentials means "we could not tell" — show every row as
    // unknown-but-present rather than letting a malformed response take the whole panel down.
    const credentials = Array.isArray(credentialsQuery.data) ? credentialsQuery.data : [];
    const stored = new Map(credentials.filter((c) => c.scope === 'byo').map((c) => [c.kind, c]));
    const row = (kind: CredentialKind, required: boolean) => ({
      kind,
      required,
      last4: stored.get(kind)?.last4 ?? null,
      present: stored.has(kind),
    });
    // Same posture for the config itself: a malformed payload yields an empty strip, never a crash
    // — this panel is the one place a user goes to find out why a deploy will not start.
    const required = config.byo_required_credentials ?? [];
    const optional = config.byo_optional_credentials ?? [];
    const rows = [...required.map((k) => row(k, true)), ...optional.map((k) => row(k, false))];
    return { rows, missing: rows.filter((r) => r.required && !r.present) };
  }, [configQuery.data, credentialsQuery.data]);

  // Only a *successful* read can say a credential is missing. Loading or a failed fetch means "we
  // do not know", and the panel must not answer an unknown by blocking the button — the server's
  // own check is the real enforcement, and it gives a precise error.
  const credentialsKnown = configQuery.isSuccess && credentialsQuery.isSuccess;
  const missingCredentials = isByo && credentialsKnown ? (readiness?.missing ?? []) : [];

  const deployDisabledReason = missingPrereq
    ? `Deploy needs a completed ${missingPrereq} first.`
    : missingCredentials.length > 0
      ? `Add your ${missingCredentials
          .map((c) => CREDENTIAL_LABELS[c.kind])
          .join(' and ')} in Settings to deploy into your own account.`
      : null;
  const busy = intent.isPending || live.running;

  function runDeploy(): void {
    intent.mutate({ stage: 'deploy', action: 'proceed', payload: { mode } });
  }

  // --- teardown ------------------------------------------------------------------------------
  const [confirmDelete, setConfirmDelete] = useState(false);
  const remove = useMutation({
    mutationFn: () => deleteDeployment(projectId),
    onSuccess: (report) => {
      setConfirmDelete(false);
      setSelected(null);
      void queryClient.invalidateQueries({ queryKey: ['deployment-latest', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['deploy-logs', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      // Anything the provider refused is named rather than swallowed — it has to be cleaned up by
      // hand, and the user is the only one who can do it.
      if (report.warnings.length > 0) {
        toast({
          title: 'Deployment deleted, with warnings',
          description: report.warnings.join(' · '),
          variant: 'warning',
        });
      } else {
        toast({ title: 'Deployment deleted', variant: 'success' });
      }
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Could not delete the deployment';
      toast({ title: 'Delete failed', description, variant: 'error' });
    },
  });

  const overall = live.overall ?? deployment?.status ?? null;
  // A deleted record is history, not a live deployment: the panel must offer Deploy, not Redeploy.
  const hasDeployed = Boolean(deployment) && deployment?.status !== 'deleted';

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
          {hasDeployed ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={busy || remove.isPending}
              data-testid="deploy-delete"
              title="Take the deployment down at the provider. Your data is not touched."
              onClick={() => setConfirmDelete(true)}
            >
              Delete
            </Button>
          ) : null}
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

      {/* BYO needs the user's own credentials, and the server can only tell them so *after* they
          press Deploy and a pipeline has started. This says it before. */}
      {isByo && readiness ? (
        <section
          className="shrink-0 rounded-xl border border-edge bg-surface px-3 py-2"
          data-testid="byo-readiness"
        >
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-fg-muted">
              Deploying into your own accounts
            </span>
            <a href="/settings" className="text-[11px] text-brand-text hover:underline">
              Manage in Settings
            </a>
          </div>
          <ul className="space-y-1">
            {readiness.rows.map((row) => (
              <li
                key={row.kind}
                data-testid={`byo-credential-${row.kind}`}
                data-present={row.present}
                className="flex items-center justify-between gap-2 text-xs"
              >
                <span className="text-fg-muted">
                  {CREDENTIAL_LABELS[row.kind]}
                  {row.required ? null : <span className="ml-1 text-fg-faint">(optional)</span>}
                </span>
                {row.present ? (
                  <Badge tone="success">Stored{row.last4 ? ` ····${row.last4}` : ''}</Badge>
                ) : (
                  <Badge tone={row.required ? 'warning' : 'neutral'}>Missing</Badge>
                )}
              </li>
            ))}
          </ul>
          {readiness.rows.some((r) => !r.required && !r.present) ? (
            <p className="mt-1.5 text-[11px] text-fg-faint">
              Without your own MongoDB URI the app uses ForgeFlow&apos;s per-project database.
            </p>
          ) : null}
        </section>
      ) : null}

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

      <Modal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete this deployment?"
      >
        <div className="space-y-3 text-sm text-fg-muted">
          <p>
            The live sites are destroyed at the provider and the URLs stop working:
            {Object.values(deployment?.urls ?? {}).length > 0 ? (
              <span className="mt-1 block font-mono text-xs text-fg-subtle">
                {Object.values(deployment?.urls ?? {}).join('\n')}
              </span>
            ) : null}
          </p>
          <p>
            <span className="text-fg">Your data is not touched.</span> The project database and
            everything in it survive — this removes the hosting only, and you can deploy again at
            any time.
          </p>
          <div className="flex justify-end gap-2 pt-1">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setConfirmDelete(false)}
              data-testid="deploy-delete-cancel"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              variant="danger"
              disabled={remove.isPending}
              data-testid="deploy-delete-confirm"
              onClick={() => remove.mutate()}
            >
              {remove.isPending ? 'Deleting…' : 'Delete deployment'}
            </Button>
          </div>
        </div>
      </Modal>

      {/* `minmax(0,…)` rather than a bare `2fr_1fr`, and `min-w-0` on both children.
          A grid track's automatic minimum size is its content's min-content width, and a provider
          build log has lines hundreds of characters long — so the drawer column grew to fit its
          widest line and squeezed the topology down to nothing. The map disappeared entirely and
          the logs it was replaced by were themselves clipped at the panel edge. Capping both
          tracks at zero minimum makes the fractions authoritative and moves the overflow inside
          the log box, which is the one element equipped to scroll it. */}
      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="min-h-0 min-w-0 overflow-hidden rounded-xl border border-edge bg-surface">
          <Topology snapshot={snapshot} state={live} onSelect={setSelected} />
        </div>

        <div className="min-h-0 min-w-0">
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
