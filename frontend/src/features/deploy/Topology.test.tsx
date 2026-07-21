import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { DeploymentDto, StageStateDto } from '../../lib/types';
import type { RealtimeEvent } from '../../lib/wsClient';
import { DeployPanel } from './DeployPanel';
import { Topology } from './Topology';
import { DEFAULT_SNAPSHOT, initialLiveState } from './topologyModel';

const PROJECT = 'p1';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stage(name: string, status: string): StageStateDto {
  return { stage: name, status, updated_at: '' } as StageStateDto;
}

const LIVE_DEPLOYMENT: DeploymentDto = {
  id: 'd1',
  mode: 'seamless',
  status: 'live',
  fe_target: 'vercel',
  be_target: 'render',
  db_target: 'platform',
  urls: { fe: 'https://web.vercel.app', be: 'https://api.onrender.com' },
  topology_snapshot: {
    nodes: [
      {
        id: 'fe',
        kind: 'frontend',
        provider: 'vercel',
        url: 'https://web.vercel.app',
        status: 'live',
      },
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
    status: 'live',
  },
  created_at: '2026-07-21T10:00:00Z',
};

function wrapper(): ({ children }: { children: ReactNode }) => JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

/**
 * Mount the panel and let its hydration queries land inside act(). The graph renders synchronously
 * from the default snapshot, so a plain `findBy*` would return before the queries resolve and their
 * state updates would escape the test's act() scope.
 */
async function renderPanel(): Promise<void> {
  render(<DeployPanel projectId={PROJECT} />, { wrapper: wrapper() });
  await act(async () => {
    // Two ticks: one for the fetches to resolve, one for React Query to notify its observers.
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/** Push events onto the realtime channel the panel subscribes to, as the hub would. */
function emit(...payloads: Record<string, unknown>[]): void {
  const events: RealtimeEvent[] = payloads.map((payload, i) => ({
    event: 'deploy.status',
    project_id: PROJECT,
    stage: 'deploy',
    payload,
    seq: i + 1,
    ts: '',
  }));
  act(() => {
    useRealtimeStore.setState((s) => ({ events: [...s.events, ...events] }));
  });
}

describe('Topology', () => {
  it('draws the fixed-stack shape (frontend, backend, database) before any deploy', () => {
    render(
      <Topology snapshot={DEFAULT_SNAPSHOT} state={initialLiveState(null)} onSelect={() => {}} />,
      { wrapper: wrapper() },
    );

    expect(screen.getByTestId('topology-node-fe')).toBeInTheDocument();
    expect(screen.getByTestId('topology-node-be')).toBeInTheDocument();
    expect(screen.getByTestId('topology-node-db')).toBeInTheDocument();
    // Nothing is deployed yet, so nothing claims to be healthy.
    expect(screen.getByTestId('topology-status-fe')).toHaveTextContent('Not deployed');
  });

  it('hydrates each node from the last deployment snapshot', () => {
    render(
      <Topology
        snapshot={LIVE_DEPLOYMENT.topology_snapshot}
        state={initialLiveState(LIVE_DEPLOYMENT)}
        onSelect={() => {}}
      />,
      { wrapper: wrapper() },
    );

    expect(screen.getByTestId('topology-status-fe')).toHaveTextContent('Healthy');
    expect(screen.getByTestId('topology-status-be')).toHaveTextContent('Healthy');
    expect(screen.getByText('https://api.onrender.com')).toBeInTheDocument();
  });

  it('reports the node the user clicked', () => {
    const onSelect = vi.fn();
    render(
      <Topology snapshot={DEFAULT_SNAPSHOT} state={initialLiveState(null)} onSelect={onSelect} />,
      { wrapper: wrapper() },
    );

    fireEvent.click(screen.getByTestId('topology-node-be'));
    expect(onSelect).toHaveBeenCalledWith('be');
  });
});

describe('DeployPanel graph', () => {
  // One spy for the suite reading a per-test build status, so the test that needs an unmet prereq
  // changes the fixture rather than re-spying on fetch.
  let buildStatus = 'complete';

  beforeEach(() => {
    buildStatus = 'complete';
    useRealtimeStore.setState({ events: [] });
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input);
      if (url.includes('/stages')) {
        return Promise.resolve(json([stage('build', buildStatus), stage('deploy', 'empty')]));
      }
      if (url.includes('/deploy/latest/logs')) {
        return Promise.resolve(json({ deployment_id: 'd1', log: '' }));
      }
      if (url.includes('/deploy/latest')) return Promise.resolve(json(null));
      return Promise.resolve(json({}));
    });
  });

  // The channel is reset in beforeEach: clearing it here would push a store update into a panel
  // that is still mounted, outside act().
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('moves nodes through deploying → healthy as deploy.status events arrive', async () => {
    await renderPanel();

    emit({ step: 'db', status: 'provisioning' });
    expect(screen.getByTestId('topology-status-db')).toHaveTextContent('Deploying');

    emit({ step: 'db', status: 'ready' }, { step: 'be', status: 'deploying' });
    expect(screen.getByTestId('topology-status-db')).toHaveTextContent('Healthy');
    expect(screen.getByTestId('topology-status-be')).toHaveTextContent('Deploying');

    // The backend goes live before the frontend is even built — the ordering phase-37 enforces.
    emit({ step: 'be', status: 'live', url: 'https://api.onrender.com' });
    expect(screen.getByTestId('topology-status-be')).toHaveTextContent('Healthy');
    expect(screen.getByText('https://api.onrender.com')).toBeInTheDocument();

    emit({ step: 'fe', status: 'live', url: 'https://web.vercel.app' });
    expect(screen.getByTestId('topology-status-fe')).toHaveTextContent('Healthy');
  });

  it('shows a failed node when its step fails, without losing the healthy ones', async () => {
    await renderPanel();

    emit(
      { step: 'be', status: 'live', url: 'https://api.onrender.com' },
      { step: 'fe', status: 'failed', error: 'vercel: quota exceeded' },
      { step: 'health', status: 'degraded' },
    );

    expect(screen.getByTestId('topology-status-fe')).toHaveTextContent('Failed');
    expect(screen.getByTestId('topology-status-be')).toHaveTextContent('Healthy');
    expect(screen.getByTestId('deploy-overall-status')).toHaveTextContent('degraded');
  });

  it('blocks deploying with a reason until the build is complete', async () => {
    buildStatus = 'empty';

    await renderPanel();

    const button = screen.getByTestId('deploy-run');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'Deploy needs a completed build first.');
    expect(screen.getByTestId('deploy-prereq-warning')).toHaveTextContent('completed build');
  });

  it('deploys in the selected mode when the prereq is met', async () => {
    await renderPanel();

    const button = screen.getByTestId('deploy-run');
    expect(button).toBeEnabled();

    fireEvent.click(screen.getByTestId('deploy-mode-byo'));
    fireEvent.click(button);

    await waitFor(() => {
      const call = vi
        .mocked(globalThis.fetch)
        .mock.calls.find(([input]) => String(input).endsWith('/intent'));
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        stage: 'deploy',
        action: 'proceed',
        payload: { mode: 'byo' },
      });
    });
  });
});
