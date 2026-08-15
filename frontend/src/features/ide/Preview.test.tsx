import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { PreviewInfo, PreviewStatus } from '../../lib/types';
import type { RealtimeEvent } from '../../lib/wsClient';
import { Preview } from './Preview';

const api = vi.hoisted(() => ({
  getPreview: vi.fn<(projectId: string) => Promise<PreviewInfo>>(),
  previewAction:
    vi.fn<(projectId: string, action: 'start' | 'stop' | 'restart') => Promise<PreviewInfo>>(),
}));
vi.mock('./api', () => api);

const { getPreview, previewAction } = api;

function info(fe: PreviewStatus, be: PreviewStatus = fe): PreviewInfo {
  const live = fe !== 'stopped';
  return {
    project_id: 'p1',
    fe_url: live ? 'http://p1.preview.localhost' : null,
    be_url: live ? 'http://p1.api.preview.localhost' : null,
    fe_status: fe,
    be_status: be,
  };
}

function wrap(node: ReactNode): JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>;
}

/**
 * Mount and wait for the *rendered* status to settle. Waiting on the mock's call count instead
 * would race the query's first paint and make these tests flaky.
 */
async function renderPreview(expected: PreviewStatus = 'stopped'): Promise<void> {
  render(wrap(<Preview projectId="p1" />));
  await waitFor(() => expect(screen.getByTestId('fe-status')).toHaveTextContent(`FE ${expected}`));
}

function logEvent(process: string, chunk: string, seq: number): RealtimeEvent {
  return {
    event: 'terminal.output',
    project_id: 'p1',
    stage: 'build',
    payload: { source: 'preview', process, stream: 'stdout', chunk },
    seq,
    ts: '',
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useRealtimeStore.setState({ events: [] });
  getPreview.mockResolvedValue(info('stopped'));
});

afterEach(() => {
  useRealtimeStore.setState({ events: [] });
});

describe('Preview', () => {
  it('shows stopped state with a start button and no iframe', async () => {
    await renderPreview();

    expect(screen.getByTestId('fe-status')).toHaveTextContent('FE stopped');
    expect(screen.getByTestId('be-status')).toHaveTextContent('BE stopped');
    expect(screen.getByTestId('preview-start')).toBeInTheDocument();
    expect(screen.queryByTestId('preview-frame')).not.toBeInTheDocument();
  });

  it('renders the iframe at fe_url once running', async () => {
    getPreview.mockResolvedValue(info('running'));
    await renderPreview('running');

    const frame = screen.getByTestId('preview-frame');
    expect(frame).toHaveAttribute('src', 'http://p1.preview.localhost');
    // Sandboxed, but `allow-same-origin` must be present: the frame is served from its own
    // `*.preview.*` subdomain (never the control plane's origin), and without this flag every
    // subresource request the framed dev server makes carries an opaque `Origin: null`, which
    // Vite (and CORS-checking backends) reject outright — the iframe would render blank no
    // matter how healthy the generated app is.
    expect(frame).toHaveAttribute('sandbox');
    expect(frame.getAttribute('sandbox')).toContain('allow-same-origin');
  });

  it('starts the preview and reflects the returned status', async () => {
    await renderPreview();
    previewAction.mockResolvedValue(info('running'));

    fireEvent.click(screen.getByTestId('preview-start'));

    await waitFor(() => expect(previewAction).toHaveBeenCalledWith('p1', 'start'));
    await waitFor(() => expect(screen.getByTestId('fe-status')).toHaveTextContent('FE running'));
    expect(screen.getByTestId('preview-frame')).toBeInTheDocument();
  });

  it('offers restart and stop while running, and calls them', async () => {
    getPreview.mockResolvedValue(info('running'));
    await renderPreview('running');
    previewAction.mockResolvedValue(info('running'));

    fireEvent.click(screen.getByTestId('preview-restart'));
    await waitFor(() => expect(previewAction).toHaveBeenCalledWith('p1', 'restart'));

    previewAction.mockResolvedValue(info('stopped'));
    fireEvent.click(screen.getByTestId('preview-stop'));
    await waitFor(() => expect(previewAction).toHaveBeenCalledWith('p1', 'stop'));
    await waitFor(() => expect(screen.queryByTestId('preview-frame')).not.toBeInTheDocument());
  });

  it('refresh remounts the iframe without re-calling the API', async () => {
    getPreview.mockResolvedValue(info('running'));
    await renderPreview('running');

    const before = screen.getByTestId('preview-frame');
    fireEvent.click(screen.getByTestId('preview-refresh'));
    const after = screen.getByTestId('preview-frame');

    // A remount (new element) is the only cross-origin way to force a reload.
    expect(after).not.toBe(before);
    expect(previewAction).not.toHaveBeenCalled();
  });

  it('exposes an open-in-new-tab link to the preview URL', async () => {
    getPreview.mockResolvedValue(info('running'));
    await renderPreview('running');

    const link = screen.getByTestId('preview-open-tab');
    expect(link).toHaveAttribute('href', 'http://p1.preview.localhost');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('streams FE/BE logs into their tabs', async () => {
    getPreview.mockResolvedValue(info('running'));
    await renderPreview('running');

    act(() => {
      useRealtimeStore.setState({
        events: [
          logEvent('frontend', 'vite ready in 300ms\n', 1),
          logEvent('backend', 'express listening on 3001\n', 2),
        ],
      });
    });

    fireEvent.click(screen.getByTestId('preview-logs-toggle'));
    expect(screen.getByTestId('log-output')).toHaveTextContent('vite ready in 300ms');

    fireEvent.click(screen.getByTestId('log-tab-backend'));
    expect(screen.getByTestId('log-output')).toHaveTextContent('express listening on 3001');
    expect(screen.getByTestId('log-output')).not.toHaveTextContent('vite ready');
  });

  it('ignores non-preview terminal output in the logs drawer', async () => {
    getPreview.mockResolvedValue(info('running'));
    await renderPreview('running');

    act(() => {
      useRealtimeStore.setState({
        events: [
          {
            event: 'terminal.output',
            project_id: 'p1',
            stage: 'build',
            payload: { exec_id: 'x', stream: 'stdout', chunk: 'pnpm install output' },
            seq: 1,
            ts: '',
          },
        ],
      });
    });

    fireEvent.click(screen.getByTestId('preview-logs-toggle'));
    expect(screen.getByTestId('log-output')).not.toHaveTextContent('pnpm install output');
  });

  it('surfaces a crashed dev server', async () => {
    getPreview.mockResolvedValue(info('failed', 'failed'));
    await renderPreview('failed');

    expect(screen.getByTestId('fe-status')).toHaveTextContent('FE failed');
    expect(screen.getByText('Preview crashed')).toBeInTheDocument();
    expect(screen.getByTestId('preview-start')).toBeInTheDocument(); // recoverable
  });

  it('shows an infrastructure warning above the frame', async () => {
    // A "running" preview whose URLs cannot load: without this the iframe just renders the
    // browser's own "refused to connect", which blames the generated app for a missing proxy.
    getPreview.mockResolvedValue({
      ...info('running'),
      warning: 'The dev servers are running â€¦ Start it with `make proxy`.',
    });
    await renderPreview('running');

    expect(screen.getByTestId('preview-warning')).toHaveTextContent('make proxy');
    // The frame is still shown â€” the servers really are up, so a reload may be all that is needed.
    expect(screen.getByTestId('preview-frame')).toBeInTheDocument();
  });

  it('shows no warning banner for a healthy preview', async () => {
    getPreview.mockResolvedValue(info('running'));
    await renderPreview('running');

    expect(screen.queryByTestId('preview-warning')).not.toBeInTheDocument();
  });
});
