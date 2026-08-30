import { describe, expect, it } from 'vitest';

import type { DeploymentDto } from '../../lib/types';
import {
  DEFAULT_SNAPSHOT,
  applyDeployEvent,
  buildFlowGraph,
  envKeysFor,
  initialLiveState,
  logLinesFor,
  snapshotOf,
} from './topologyModel';

const DEGRADED: DeploymentDto = {
  id: 'd1',
  mode: 'seamless',
  status: 'degraded',
  fe_target: 'vercel',
  be_target: 'render',
  db_target: 'platform',
  urls: { be: 'https://api.onrender.com' },
  topology_snapshot: {
    nodes: [
      { id: 'fe', kind: 'frontend', provider: 'vercel', status: 'failed' },
      {
        id: 'be',
        kind: 'backend',
        provider: 'render',
        url: 'https://api.onrender.com',
        status: 'live',
      },
      { id: 'db', kind: 'database', provider: 'platform', status: 'ready' },
    ],
    edges: [
      { source: 'fe', target: 'be', label: 'VITE_API_BASE_URL' },
      { source: 'be', target: 'db', label: 'MONGODB_URI' },
    ],
    status: 'degraded',
  },
  created_at: '',
};

describe('hydration', () => {
  it('starts every node idle when nothing has been deployed', () => {
    const state = initialLiveState(null);
    expect(state.statuses).toEqual({ fe: 'idle', be: 'idle', db: 'idle' });
    expect(state.overall).toBeNull();
  });

  it('reproduces the last deployment, degraded parts included', () => {
    const state = initialLiveState(DEGRADED);
    expect(state.statuses).toEqual({ fe: 'failed', be: 'healthy', db: 'healthy' });
    expect(state.urls.be).toBe('https://api.onrender.com');
    expect(state.overall).toBe('degraded');
  });

  it('falls back to the fixed-stack shape before the first deploy', () => {
    expect(snapshotOf(null)).toBe(DEFAULT_SNAPSHOT);
    expect(snapshotOf(DEGRADED)).toBe(DEGRADED.topology_snapshot);
  });
});

describe('a deleted deployment', () => {
  // Teardown clears the URLs and marks every node `deleted`, so the record describes nothing that
  // exists. Keeping its shape froze the graph: a deploy that shipped no frontend (because the
  // analyzer failed to detect one) left the frontend node missing for good, long after the
  // deployment itself was gone.
  const DELETED: DeploymentDto = {
    ...DEGRADED,
    status: 'deleted',
    urls: {},
    topology_snapshot: {
      nodes: [
        { id: 'be', kind: 'backend', provider: 'vercel', url: null, status: 'deleted' },
        { id: 'db', kind: 'database', provider: 'platform', url: null, status: 'deleted' },
      ],
      edges: [{ source: 'be', target: 'db', label: 'MONGODB_URI' }],
    },
  };

  it('draws the shape a deploy would create, not the one that was torn down', () => {
    expect(snapshotOf(DELETED)).toBe(DEFAULT_SNAPSHOT);
    expect(DEFAULT_SNAPSHOT.nodes.map((n) => n.id)).toContain('fe');
  });

  it('shows every node as not deployed', () => {
    const state = initialLiveState(DELETED);
    expect(state.statuses).toEqual({ fe: 'idle', be: 'idle', db: 'idle' });
    expect(state.urls).toEqual({});
  });

  it('leaves the overall verdict to the record, so the panel can still badge it deleted', () => {
    // `DeployPanel` reads `live.overall ?? deployment.status`; a null here keeps that badge honest.
    expect(initialLiveState(DELETED).overall).toBeNull();
  });
});

describe('deploy.status fold', () => {
  it('marks the previous verdict stale as soon as a new deploy starts', () => {
    const after = applyDeployEvent(initialLiveState(DEGRADED), { step: 'be', status: 'deploying' });
    expect(after.statuses.be).toBe('deploying');
    expect(after.overall).toBeNull(); // the old "degraded" no longer describes reality
    expect(after.running).toBe(true);
  });

  it('records a URL and an error against the node that reported them', () => {
    let state = initialLiveState(null);
    state = applyDeployEvent(state, { step: 'be', status: 'live', url: 'https://api' });
    state = applyDeployEvent(state, { step: 'fe', status: 'failed', error: 'quota exceeded' });

    expect(state.urls.be).toBe('https://api');
    expect(state.statuses.fe).toBe('failed');
    expect(state.errors.fe).toBe('quota exceeded');
  });

  it('ends the run on the terminal health step', () => {
    let state = applyDeployEvent(initialLiveState(null), { step: 'fe', status: 'building' });
    state = applyDeployEvent(state, { step: 'health', status: 'live' });
    expect(state.overall).toBe('live');
    expect(state.running).toBe(false);
  });

  it('ignores events it does not understand', () => {
    const state = initialLiveState(null);
    expect(applyDeployEvent(state, { step: 'something-else' })).toBe(state);
  });
});

describe('graph', () => {
  it('animates a wire while either end is still deploying', () => {
    const state = applyDeployEvent(initialLiveState(null), { step: 'be', status: 'deploying' });
    const { edges } = buildFlowGraph(DEFAULT_SNAPSHOT, state);

    expect(edges.find((e) => e.id === 'fe-be')?.animated).toBe(true);
    expect(edges.find((e) => e.id === 'be-db')?.animated).toBe(true);

    const settled = applyDeployEvent(state, { step: 'be', status: 'live' });
    expect(buildFlowGraph(DEFAULT_SNAPSHOT, settled).edges.every((e) => !e.animated)).toBe(true);
  });
});

describe('secret-safe env summary', () => {
  it('names the keys a node is configured with, taken from the wiring edges', () => {
    expect(envKeysFor('be', DEFAULT_SNAPSHOT)).toEqual(['MONGODB_URI', 'NODE_ENV']);
    // The SPA is a static bundle: its NODE_ENV is a build input in the sandbox, never a variable on
    // the host, so listing it would claim a provider var that is not there (phase-62).
    expect(envKeysFor('fe', DEFAULT_SNAPSHOT)).toEqual(['VITE_API_BASE_URL']);
    // The database is wired *into* the backend; it receives nothing itself.
    expect(envKeysFor('db', DEFAULT_SNAPSHOT)).toEqual([]);
  });
});

describe('log filtering', () => {
  it('keeps only the requested node’s lines', () => {
    const log = 'db: provisioned (platform)\nbe: live https://api\nbe: FAILED — boom\nfe: live';
    expect(logLinesFor('be', log)).toEqual(['be: live https://api', 'be: FAILED — boom']);
    expect(logLinesFor('fe', log)).toEqual(['fe: live']);
    expect(logLinesFor('be', '')).toEqual([]);
  });
});
