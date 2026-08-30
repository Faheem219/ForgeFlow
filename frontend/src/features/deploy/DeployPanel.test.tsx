import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { useToastStore } from '../../lib/stores/toastStore';
import type { CredentialDto, DeploymentDto, StageStateDto } from '../../lib/types';
import { DeployPanel } from './DeployPanel';

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
  be_target: 'vercel',
  db_target: 'platform',
  urls: { fe: 'https://web.vercel.app', be: 'https://api.vercel.app' },
  topology_snapshot: {
    nodes: [
      { id: 'fe', kind: 'frontend', provider: 'vercel', url: 'https://web', status: 'live' },
      { id: 'be', kind: 'backend', provider: 'vercel', url: 'https://api', status: 'live' },
    ],
    edges: [{ source: 'fe', target: 'be', label: 'VITE_API_BASE_URL' }],
    status: 'live',
  },
  created_at: '2026-08-29T10:00:00Z',
};

interface MockState {
  deployment: DeploymentDto | null;
  credentials: CredentialDto[];
  beProvider: string;
  deleted: number;
  deleteWarnings: string[];
}

let state: MockState;

function credential(kind: CredentialDto['kind'], last4 = '1234'): CredentialDto {
  return { kind, scope: 'byo', created_at: '2026-08-29T09:00:00Z', last4 };
}

function installFetch(): void {
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    const url = String(input);
    if ((init?.method ?? 'GET') === 'DELETE') {
      state.deleted += 1;
      state.deployment = null;
      return Promise.resolve(
        json({
          deployment_id: 'd1',
          destroyed: ['be', 'fe'],
          record_deleted: true,
          warnings: state.deleteWarnings,
        }),
      );
    }
    if (url.includes('/stages')) {
      return Promise.resolve(json([stage('build', 'complete'), stage('deploy', 'complete')]));
    }
    if (url.includes('/deploy/config')) {
      return Promise.resolve(
        json({
          be_provider: state.beProvider,
          byo_required_credentials:
            state.beProvider === 'render' ? ['render', 'vercel'] : ['vercel'],
          byo_optional_credentials: ['mongo_uri'],
        }),
      );
    }
    if (url.includes('/credentials')) return Promise.resolve(json(state.credentials));
    if (url.includes('/deploy/latest/logs')) {
      return Promise.resolve(json({ deployment_id: 'd1', log: '' }));
    }
    if (url.includes('/deploy/latest')) return Promise.resolve(json(state.deployment));
    return Promise.resolve(json({}));
  });
}

function wrapper(): ({ children }: { children: ReactNode }) => JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

async function renderPanel(): Promise<void> {
  render(<DeployPanel projectId={PROJECT} />, { wrapper: wrapper() });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/** Switch to BYO and let the config + credentials queries land inside act(). */
async function chooseByo(): Promise<void> {
  fireEvent.click(screen.getByTestId('deploy-mode-byo'));
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  state = {
    deployment: LIVE_DEPLOYMENT,
    credentials: [],
    beProvider: 'vercel',
    deleted: 0,
    deleteWarnings: [],
  };
  useRealtimeStore.setState({ channel: PROJECT, events: [] });
  installFetch();
});

afterEach(() => vi.restoreAllMocks());

describe('bring-your-own readiness', () => {
  it('shows nothing extra in seamless mode', async () => {
    await renderPanel();

    // Platform credentials are not the user's business.
    expect(screen.queryByTestId('byo-readiness')).toBeNull();
    expect(screen.getByTestId('deploy-run')).toBeEnabled();
  });

  it('names each required credential as Missing and blocks Deploy', async () => {
    // The failure used to arrive only after pressing Deploy, once a pipeline had started.
    await renderPanel();
    await chooseByo();

    expect(await screen.findByTestId('byo-readiness')).toBeInTheDocument();
    expect(screen.getByTestId('byo-credential-vercel')).toHaveAttribute('data-present', 'false');

    const deploy = screen.getByTestId('deploy-run');
    expect(deploy).toBeDisabled();
    expect(deploy).toHaveAttribute('title', expect.stringContaining('Vercel token'));
  });

  it('shows a stored credential with its last4 and enables Deploy', async () => {
    state.credentials = [credential('vercel', 'ab12')];
    await renderPanel();
    await chooseByo();

    const row = await screen.findByTestId('byo-credential-vercel');
    expect(row).toHaveAttribute('data-present', 'true');
    expect(row).toHaveTextContent('ab12');
    expect(screen.getByTestId('deploy-run')).toBeEnabled();
  });

  it('does not block on the optional database URI', async () => {
    // The platform's per-project database covers it — it is an override, not a prerequisite.
    state.credentials = [credential('vercel')];
    await renderPanel();
    await chooseByo();

    expect(await screen.findByTestId('byo-credential-mongo_uri')).toHaveAttribute(
      'data-present',
      'false',
    );
    expect(screen.getByTestId('deploy-run')).toBeEnabled();
  });

  it('asks for the credential the configured backend provider actually needs', async () => {
    // Asking a Render-backed instance for a Vercel token alone is the mistake this prevents.
    state.beProvider = 'render';
    await renderPanel();
    await chooseByo();

    expect(await screen.findByTestId('byo-credential-render')).toBeInTheDocument();
    expect(screen.getByTestId('byo-credential-vercel')).toBeInTheDocument();
  });

  it('does not block Deploy when the credential list cannot be read', async () => {
    // An unknown state is not a missing token. The server's own check still enforces it, with a
    // precise error — blocking here would strand a user whose credentials are perfectly fine.
    vi.restoreAllMocks();
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input);
      if (url.includes('/stages')) {
        return Promise.resolve(json([stage('build', 'complete'), stage('deploy', 'complete')]));
      }
      if (url.includes('/deploy/config')) {
        return Promise.resolve(
          json({
            be_provider: 'vercel',
            byo_required_credentials: ['vercel'],
            byo_optional_credentials: ['mongo_uri'],
          }),
        );
      }
      if (url.includes('/credentials')) return Promise.resolve(json({ error: 'boom' }, 500));
      if (url.includes('/deploy/latest')) return Promise.resolve(json(state.deployment));
      return Promise.resolve(json({}));
    });

    await renderPanel();
    await chooseByo();

    expect(screen.getByTestId('deploy-run')).toBeEnabled();
  });

  it('goes back to nothing when seamless is reselected', async () => {
    await renderPanel();
    await chooseByo();
    await screen.findByTestId('byo-readiness');

    fireEvent.click(screen.getByTestId('deploy-mode-seamless'));

    expect(screen.queryByTestId('byo-readiness')).toBeNull();
    expect(screen.getByTestId('deploy-run')).toBeEnabled();
  });
});

describe('deleting a deployment', () => {
  it('confirms before destroying anything', async () => {
    await renderPanel();

    fireEvent.click(screen.getByTestId('deploy-delete'));

    // The dialog states the two things a person needs to decide: what breaks, what survives.
    expect(await screen.findByText(/Delete this deployment\?/)).toBeInTheDocument();
    expect(screen.getByText(/Your data is not touched\./)).toBeInTheDocument();
    expect(state.deleted).toBe(0);
  });

  it('cancels without calling the API', async () => {
    await renderPanel();

    fireEvent.click(screen.getByTestId('deploy-delete'));
    fireEvent.click(await screen.findByTestId('deploy-delete-cancel'));

    await waitFor(() => expect(screen.queryByTestId('deploy-delete-confirm')).toBeNull());
    expect(state.deleted).toBe(0);
  });

  it('deletes on confirm and returns the panel to its un-deployed state', async () => {
    await renderPanel();
    expect(screen.getByTestId('deploy-run')).toHaveTextContent('Redeploy');

    fireEvent.click(screen.getByTestId('deploy-delete'));
    fireEvent.click(await screen.findByTestId('deploy-delete-confirm'));

    await waitFor(() => expect(state.deleted).toBe(1));
    await waitFor(() => expect(screen.getByTestId('deploy-run')).toHaveTextContent('Deploy'));
    expect(screen.queryByTestId('deploy-delete')).toBeNull();
  });

  it('surfaces what the provider refused to reclaim', async () => {
    // Fail-soft does not mean silent: a deployment left running is the user's to clean up. Asserted
    // on the toast store rather than the DOM — the Toaster lives in the app shell, not this panel.
    state.deleteWarnings = ['be: vercel is unreachable'];
    useToastStore.setState({ toasts: [] });
    await renderPanel();

    fireEvent.click(screen.getByTestId('deploy-delete'));
    fireEvent.click(await screen.findByTestId('deploy-delete-confirm'));

    await waitFor(() => {
      const latest = useToastStore.getState().toasts.at(-1);
      expect(latest?.variant).toBe('warning');
      expect(latest?.description).toContain('vercel is unreachable');
    });
  });

  it('offers no delete before anything has been deployed', async () => {
    state.deployment = null;
    await renderPanel();

    expect(screen.queryByTestId('deploy-delete')).toBeNull();
    expect(screen.getByTestId('deploy-run')).toHaveTextContent('Deploy');
  });

  it('offers no delete for an already-deleted record', async () => {
    // The row survives as history; it is not a live deployment any more.
    state.deployment = { ...LIVE_DEPLOYMENT, status: 'deleted', urls: {} };
    await renderPanel();

    expect(screen.queryByTestId('deploy-delete')).toBeNull();
    expect(screen.getByTestId('deploy-run')).toHaveTextContent('Deploy');
  });
});

describe('the map and the details share the row', () => {
  /*
   * A provider build log has lines hundreds of characters wide, and a grid track's automatic
   * minimum size is its content's min-content width — so the drawer column grew to fit its widest
   * log line and squeezed the topology to zero. The map vanished and the logs that replaced it
   * were themselves clipped at the panel edge.
   *
   * jsdom has no layout engine, so the width cannot be measured here; the constraint is asserted
   * where it actually lives, on the track definition and the elements that must not contribute a
   * width of their own.
   */
  it('caps both grid tracks at a zero minimum so a wide log cannot evict the topology', async () => {
    await renderPanel();

    const topology = screen.getByTestId('deploy-topology');
    const row = topology.closest('.grid');

    expect(row?.className).toContain('minmax(0,2fr)_minmax(0,1fr)');
    // Both children need a floor of their own; the track cap alone is not enough.
    expect(topology.parentElement?.className).toContain('min-w-0');
  });

  it('keeps the topology mounted while a node’s details are open', async () => {
    await renderPanel();

    expect(screen.getByTestId('deploy-topology')).toBeInTheDocument();
    expect(screen.queryByTestId('deploy-node-drawer')).toBeNull();
  });
});
