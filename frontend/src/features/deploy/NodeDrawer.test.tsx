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

/** What the provider-logs endpoint returns for this test run; per-test overridable. */
let providerPayload: Record<string, unknown>;

/** Route by URL: the drawer now reads two different logs, and they must not be confused. */
function installFetch(): void {
  vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = String(input);
    if (/\/logs\/(fe|be|db)$/.test(url)) return Promise.resolve(json(providerPayload));
    return Promise.resolve(json({ deployment_id: 'd1', log: LOG }));
  });
}

describe('NodeDrawer', () => {
  beforeEach(() => {
    providerPayload = {
      deployment_id: 'd1',
      target: 'be',
      provider: 'vercel',
      lines: [{ message: 'Build completed', ts: '1' }],
      error: null,
    };
    installFetch();
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

  it("streams only this node's lines from the pipeline log", async () => {
    renderDrawer();
    fireEvent.click(screen.getByTestId('drawer-log-source-pipeline'));

    const logs = await screen.findByTestId('drawer-logs');
    expect(logs).toHaveTextContent('be: live https://api.onrender.com');
    expect(logs).not.toHaveTextContent('fe: live');
    expect(logs).not.toHaveTextContent('db: provisioned');
  });

  // ------------------------------------------------------------------ provider logs (phase-62)

  it("shows the provider's own build log by default", async () => {
    // The complaint this answers: the tab said "Logs" on a Vercel node and showed ForgeFlow's
    // step list, which is a different thing entirely.
    renderDrawer();

    const logs = await screen.findByTestId('drawer-logs');
    expect(logs).toHaveTextContent('Build completed');
    expect(logs).not.toHaveTextContent('be: live');
  });

  it('switches between the provider log and the pipeline log', async () => {
    renderDrawer();
    expect(await screen.findByTestId('drawer-logs')).toHaveTextContent('Build completed');

    fireEvent.click(screen.getByTestId('drawer-log-source-pipeline'));
    await waitFor(() =>
      expect(screen.getByTestId('drawer-logs')).toHaveTextContent('be: live https://api'),
    );

    fireEvent.click(screen.getByTestId('drawer-log-source-provider'));
    await waitFor(() =>
      expect(screen.getByTestId('drawer-logs')).toHaveTextContent('Build completed'),
    );
  });

  it("shows the provider's reason when it has no log to give", async () => {
    // A record written before deployment refs were persisted — normal, not an error.
    providerPayload = {
      deployment_id: 'd1',
      target: 'be',
      provider: null,
      lines: [],
      error: 'This deployment predates provider log support. Redeploy to get provider logs.',
    };
    renderDrawer();

    expect(await screen.findByTestId('drawer-provider-log-error')).toHaveTextContent('Redeploy');
    // …and the pipeline log is still one click away.
    fireEvent.click(screen.getByTestId('drawer-log-source-pipeline'));
    await waitFor(() =>
      expect(screen.getByTestId('drawer-logs')).toHaveTextContent('be: live https://api'),
    );
  });

  it('offers no provider log for the database node', async () => {
    // The database is not a provider deployment — there is no build log to switch to.
    renderDrawer({
      ...BE_NODE,
      id: 'db',
      kind: 'database',
      provider: 'platform',
      label: 'Database',
      liveUrl: null,
      envKeys: [],
    });

    expect(screen.queryByTestId('drawer-log-source-provider')).toBeNull();
    expect(await screen.findByTestId('drawer-logs')).toHaveTextContent('db: provisioned');
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
