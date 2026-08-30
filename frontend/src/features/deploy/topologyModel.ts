/**
 * The topology view-model (phase-38) — pure, so the graph's whole behaviour is testable without
 * mounting React Flow.
 *
 * Two sources feed it, in this order:
 *  1. the persisted `topology_snapshot` of the last deployment (phase-37), so reopening the
 *     workspace shows the true last-known topology immediately; then
 *  2. live `deploy.status` events, which move nodes through deploying → healthy/failed as a deploy
 *     progresses.
 *
 * Nothing here handles secrets: a deployment records URLs and wiring **keys** (`MONGODB_URI`,
 * `VITE_API_BASE_URL`), never their values, so the env summary is secret-safe by construction (§7).
 */

import type {
  DeploymentDto,
  DeploymentStatus,
  TopologyEdgeDto,
  TopologyNodeDto,
  TopologyNodeId,
  TopologySnapshotDto,
} from '../../lib/types';

export type NodeStatus = 'idle' | 'deploying' | 'healthy' | 'degraded' | 'failed';

export const NODE_LABELS: Record<TopologyNodeId, string> = {
  fe: 'Frontend',
  be: 'Backend',
  db: 'Database',
};

export const NODE_STATUS_LABELS: Record<NodeStatus, string> = {
  idle: 'Not deployed',
  deploying: 'Deploying',
  healthy: 'Healthy',
  degraded: 'Degraded',
  failed: 'Failed',
};

export const NODE_STATUS_TONES: Record<NodeStatus, 'neutral' | 'brand' | 'success' | 'danger'> = {
  idle: 'neutral',
  deploying: 'brand',
  healthy: 'success',
  degraded: 'danger',
  failed: 'danger',
};

/**
 * The fixed generated-app stack (a Vite SPA and an Express API on Vercel, Mongo behind them),
 * so the graph shows the shape a deploy *will* take before one has ever run.
 */
export const DEFAULT_SNAPSHOT: TopologySnapshotDto = {
  nodes: [
    { id: 'fe', kind: 'frontend', provider: 'vercel', status: 'idle' },
    { id: 'be', kind: 'backend', provider: 'vercel', status: 'idle' },
    { id: 'db', kind: 'database', provider: 'platform', status: 'idle' },
  ],
  edges: [
    { source: 'fe', target: 'be', label: 'VITE_API_BASE_URL' },
    { source: 'be', target: 'db', label: 'MONGODB_URI' },
  ],
};

/** Provider/step words (from `DeployState` and the orchestrator's step events) → a node status. */
const STATUS_WORDS: Record<string, NodeStatus> = {
  idle: 'idle',
  queued: 'deploying',
  building: 'deploying',
  deploying: 'deploying',
  provisioning: 'deploying',
  live: 'healthy',
  ready: 'healthy',
  healthy: 'healthy',
  degraded: 'degraded',
  failed: 'failed',
  canceled: 'idle',
};

export function toNodeStatus(word: string | undefined): NodeStatus {
  return STATUS_WORDS[String(word ?? '').toLowerCase()] ?? 'idle';
}

export interface LiveDeployState {
  statuses: Record<TopologyNodeId, NodeStatus>;
  urls: Partial<Record<TopologyNodeId, string>>;
  errors: Partial<Record<TopologyNodeId, string>>;
  /** Set when the terminal `health` step lands; `null` while a deploy is still in flight. */
  overall: DeploymentStatus | null;
  running: boolean;
}

const IDLE_STATUSES: Record<TopologyNodeId, NodeStatus> = { fe: 'idle', be: 'idle', db: 'idle' };

/**
 * Whether a record describes nothing that is actually deployed.
 *
 * A torn-down deployment is history, not a topology: teardown clears every URL and marks every node
 * `deleted`. `DeployPanel` already reads one this way for its buttons (it offers Deploy, not
 * Redeploy), and the graph has to agree. Drawing a retired record's shape pins the graph to
 * whatever that deploy happened to contain — so a deploy that shipped no frontend leaves a
 * permanently frontend-less graph, which reads as the node having been lost rather than as the
 * deployment being gone.
 */
function isRetired(deployment: DeploymentDto | null | undefined): boolean {
  return !deployment || deployment.status === 'deleted';
}

/** Seed the graph from the last deployment (or an all-idle default before the first one). */
export function initialLiveState(deployment: DeploymentDto | null | undefined): LiveDeployState {
  if (isRetired(deployment) || !deployment) {
    return { statuses: { ...IDLE_STATUSES }, urls: {}, errors: {}, overall: null, running: false };
  }
  const statuses = { ...IDLE_STATUSES };
  for (const node of deployment.topology_snapshot?.nodes ?? []) {
    statuses[node.id] = toNodeStatus(node.status);
  }
  return {
    statuses,
    urls: { ...deployment.urls },
    errors: {},
    overall: deployment.status,
    running: false,
  };
}

const NODE_STEPS: TopologyNodeId[] = ['fe', 'be', 'db'];

function isNodeStep(step: unknown): step is TopologyNodeId {
  return NODE_STEPS.includes(step as TopologyNodeId);
}

/**
 * Fold one `deploy.status` payload into the live state. Per-node steps (`db`/`be`/`fe`) move that
 * node; the terminal `health` step carries the overall result and ends the run.
 */
export function applyDeployEvent(
  state: LiveDeployState,
  payload: Record<string, unknown>,
): LiveDeployState {
  const step = payload.step;
  const status = typeof payload.status === 'string' ? payload.status : undefined;

  if (step === 'health') {
    return {
      ...state,
      overall: (status as DeploymentStatus) ?? state.overall,
      running: false,
    };
  }
  if (!isNodeStep(step)) {
    return state;
  }

  const url = typeof payload.url === 'string' ? payload.url : undefined;
  const error = typeof payload.error === 'string' ? payload.error : undefined;
  return {
    ...state,
    statuses: { ...state.statuses, [step]: toNodeStatus(status) },
    urls: url ? { ...state.urls, [step]: url } : state.urls,
    errors: error ? { ...state.errors, [step]: error } : state.errors,
    overall: null, // a step event means a deploy is in flight; the old verdict is stale
    running: true,
  };
}

/** A node as the graph draws it: the recorded shape plus its live status/URL. */
export interface TopologyNodeView extends TopologyNodeDto {
  label: string;
  liveStatus: NodeStatus;
  liveUrl: string | null;
  error: string | null;
  /** Env **keys** wired into this node — names only, never values. */
  envKeys: string[];
}

/**
 * The env keys a node receives, read off the wiring edges: an edge `a → b` labelled `KEY` means
 * *`a` is configured with `KEY` pointing at `b`*. Only key names exist client-side.
 *
 * `NODE_ENV` is listed for the **backend** only. The backend is a Node function and reads it at
 * request time, so it is a real deployed variable. The frontend is a static bundle: its `NODE_ENV`
 * is consumed by the Vite build inside ForgeFlow's sandbox and never reaches the host — listing it
 * here would claim a provider variable that does not exist (phase-62).
 */
export function envKeysFor(nodeId: TopologyNodeId, snapshot: TopologySnapshotDto): string[] {
  const keys = (snapshot.edges ?? [])
    .filter((edge) => edge.source === nodeId && edge.label)
    .map((edge) => edge.label as string);
  if (nodeId === 'be') {
    keys.push('NODE_ENV');
  }
  return keys;
}

export function nodeViews(
  snapshot: TopologySnapshotDto,
  state: LiveDeployState,
): TopologyNodeView[] {
  return (snapshot.nodes ?? []).map((node) => ({
    ...node,
    label: NODE_LABELS[node.id] ?? node.id,
    liveStatus: state.statuses[node.id] ?? toNodeStatus(node.status),
    liveUrl: state.urls[node.id] ?? node.url ?? null,
    error: state.errors[node.id] ?? null,
    envKeys: envKeysFor(node.id, snapshot),
  }));
}

/** Left-to-right chain: frontend → backend → database, in the order the wiring flows. */
const POSITIONS: Record<TopologyNodeId, { x: number; y: number }> = {
  fe: { x: 0, y: 0 },
  be: { x: 260, y: 0 },
  db: { x: 520, y: 0 },
};

export interface FlowNode {
  id: string;
  type: 'topologyNode';
  position: { x: number; y: number };
  data: TopologyNodeView;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  animated: boolean;
}

export function buildFlowGraph(
  snapshot: TopologySnapshotDto,
  state: LiveDeployState,
): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const views = nodeViews(snapshot, state);
  const nodes: FlowNode[] = views.map((view, index) => ({
    id: view.id,
    type: 'topologyNode',
    position: POSITIONS[view.id] ?? { x: index * 260, y: 0 },
    data: view,
  }));

  const statusOf = (id: TopologyNodeId): NodeStatus => state.statuses[id] ?? 'idle';
  const edges: FlowEdge[] = (snapshot.edges ?? []).map((edge: TopologyEdgeDto) => ({
    id: `${edge.source}-${edge.target}`,
    source: edge.source,
    target: edge.target,
    label: edge.label,
    // A wire "flows" while either end is still being deployed — the visual cue that work is live.
    animated: statusOf(edge.source) === 'deploying' || statusOf(edge.target) === 'deploying',
  }));
  return { nodes, edges };
}

/** The snapshot to draw: a *live* deployment's shape, else the fixed-stack default. */
export function snapshotOf(deployment: DeploymentDto | null | undefined): TopologySnapshotDto {
  const snapshot = isRetired(deployment) ? undefined : deployment?.topology_snapshot;
  return snapshot && snapshot.nodes?.length ? snapshot : DEFAULT_SNAPSHOT;
}

/** One node's lines from the deploy step log (the orchestrator prefixes each line with the step). */
export function logLinesFor(nodeId: TopologyNodeId, log: string): string[] {
  return log
    .split('\n')
    .filter((line) => line.trim().toLowerCase().startsWith(`${nodeId}:`))
    .map((line) => line.trim());
}
