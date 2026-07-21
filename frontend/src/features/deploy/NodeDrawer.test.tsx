import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { NodeDrawer } from './NodeDrawer';
import type { TopologyNodeView } from './topologyModel';

const PROJECT = 'p1';
const LOG = [
  'db: provisioned (platform)',
  'be: live https://api.onrender.com',
  'fe: live https://web.vercel.app',
].join('\n');

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function wrapper(): ({ children }: { children: ReactNode }) => JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const BE_NODE: TopologyNodeView = {
  id: 'be',
  kind: 'backend',
  provider: 'render',
  status: 'live',
  label: 'Backend',
  liveStatus: 'healthy',
  liveUrl: 'https://api.onrender.com',
  error: null,
  envKeys: ['MONGODB_URI', 'NODE_ENV'],
};

function renderDrawer(
  node: TopologyNodeView = BE_NODE,
  props: Partial<Parameters<typeof NodeDrawer>[0]> = {},
): { onRedeploy: ReturnType<typeof vi.fn>; onClose: ReturnType<typeof vi.fn> } {
  const onRedeploy = vi.fn();
  const onClose = vi.fn();
  render(
    <NodeDrawer
      projectId={PROJECT}
      node={node}
      onClose={onClose}
      onRedeploy={onRedeploy}
      {...props}
    />,
    { wrapper: wrapper() },
  );
  return { onRedeploy, onClose };
}

describe('NodeDrawer', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(json({ deployment_id: 'd1', log: LOG })),
    );
  });

  afterEach(() => vi.restoreAllMocks());

  it('shows the live URL and copies it', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderDrawer();

    expect(screen.getByTestId('drawer-url')).toHaveAttribute('href', 'https://api.onrender.com');

    fireEvent.click(screen.getByTestId('drawer-copy-url'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('https://api.onrender.com'));
  });

  it('lists the env keys wired into the node, and never a value', async () => {
    renderDrawer();

    const env = screen.getByTestId('drawer-env');
    expect(env).toHaveTextContent('MONGODB_URI');
    expect(env).toHaveTextContent('NODE_ENV');
    // The connection string itself never reaches the browser (§7).
    expect(env.textContent).not.toMatch(/mongodb(\+srv)?:\/\//);
    await screen.findByTestId('drawer-logs');
  });

  it("streams only this node's log lines", async () => {
    renderDrawer();

    const logs = await screen.findByTestId('drawer-logs');
    expect(logs).toHaveTextContent('be: live https://api.onrender.com');
    expect(logs).not.toHaveTextContent('fe: live');
    expect(logs).not.toHaveTextContent('db: provisioned');
  });

  it('redeploys from the drawer', async () => {
    const { onRedeploy } = renderDrawer();

    fireEvent.click(screen.getByTestId('drawer-redeploy'));
    expect(onRedeploy).toHaveBeenCalledTimes(1);
    await screen.findByTestId('drawer-logs');
  });

  it('disables redeploy with the reason when a prereq is unmet', async () => {
    const { onRedeploy } = renderDrawer(BE_NODE, {
      redeployDisabledReason: 'Deploy needs a completed build first.',
    });

    const button = screen.getByTestId('drawer-redeploy');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'Deploy needs a completed build first.');

    fireEvent.click(button);
    expect(onRedeploy).not.toHaveBeenCalled();
    await screen.findByTestId('drawer-logs');
  });

  it('surfaces a failed node’s error instead of a URL', async () => {
    renderDrawer({
      ...BE_NODE,
      id: 'fe',
      kind: 'frontend',
      provider: 'vercel',
      label: 'Frontend',
      liveStatus: 'failed',
      liveUrl: null,
      error: 'vercel: quota exceeded',
      envKeys: ['VITE_API_BASE_URL', 'NODE_ENV'],
    });

    expect(screen.getByTestId('drawer-error')).toHaveTextContent('quota exceeded');
    expect(screen.queryByTestId('drawer-url')).not.toBeInTheDocument();
    expect(screen.getByText('Not deployed yet.')).toBeInTheDocument();
    await screen.findByTestId('drawer-logs');
  });
});
