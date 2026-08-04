import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { RealtimeEvent } from '../../lib/wsClient';
import { BuildPanel } from './BuildPanel';
import { STEP_LABELS } from './useBuildActivity';

/**
 * Regression cover for the stuck-spinner bug.
 *
 * The build POST runs the codegen agent inline and can outlive its own HTTP connection. Previously
 * the spinner was bound to that request's `isPending`, so a dropped connection left it turning
 * forever — visibly, after the build had already finished and streamed its results. These tests
 * hold the spinner to the *stream's* lifecycle instead.
 */

vi.mock('../ide/Ide', () => ({ default: () => <div data-testid="mock-ide">ide</div> }));
vi.mock('../ide/Preview', () => ({ default: () => <div data-testid="mock-preview">preview</div> }));

const PROJECT = 'p1';

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

let seq = 0;
function emit(event: string, payload: Record<string, unknown> = {}): void {
  seq += 1;
  const realtimeEvent: RealtimeEvent = {
    event,
    project_id: PROJECT,
    stage: 'build',
    payload,
    seq,
    ts: new Date().toISOString(),
  };
  act(() => {
    useRealtimeStore.setState((s) => ({ events: [...s.events, realtimeEvent] }));
  });
}

/** Never-settling intent POST — models a connection the proxy silently dropped. */
function installHangingIntent(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/intent')) return new Promise<Response>(() => {});
      if (url.includes('/stages')) {
        return Promise.resolve(
          json([{ stage: 'build', status: 'empty', artifacts: [], updated_at: '' }]),
        );
      }
      if (url.includes('/artifacts')) return Promise.resolve(json([]));
      return Promise.resolve(json({}));
    }),
  );
}

function renderPanel(): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }): JSX.Element => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  render(<BuildPanel projectId={PROJECT} />, { wrapper });
}

beforeEach(() => {
  seq = 0;
  useRealtimeStore.setState({ events: [], channel: PROJECT, status: 'open' });
});

afterEach(() => vi.unstubAllGlobals());

describe('build streaming lifecycle', () => {
  it('clears the spinner on run.finished even when the request never settles', async () => {
    installHangingIntent();
    renderPanel();

    fireEvent.click(await screen.findByTestId('build-proceed'));
    expect(await screen.findByTestId('build-progress')).toBeInTheDocument();

    // Output streams in, then the run reports terminal state. The POST is still hanging.
    emit('agent.stream.start', { model: 'claude-sonnet-5' });
    emit('agent.token', { text: 'writing files' });
    emit('agent.stream.end', { completed: true });
    emit('run.finished', { ok: true, run_id: 'r1', stage: 'build' });

    await waitFor(() => expect(screen.queryByTestId('build-progress')).not.toBeInTheDocument());
  });

  it('shows the live progress step while work runs', async () => {
    installHangingIntent();
    renderPanel();

    fireEvent.click(await screen.findByTestId('build-proceed'));
    // Wait for the task to actually be in flight before emitting: the seq baseline is captured
    // when `pending` commits, so an event fired before that is (correctly) treated as belonging
    // to whatever came before this task.
    await screen.findByTestId('build-progress');

    // Asserted against the panel's own label map rather than hardcoded copy, so rewording a step
    // doesn't fail a test that is really about the progress *wiring*.
    emit('progress', { stage: 'build', step: 'plan' });
    await waitFor(() =>
      expect(screen.getByTestId('build-progress')).toHaveTextContent(STEP_LABELS.plan),
    );

    emit('progress', { stage: 'build', step: 'implement' });
    await waitFor(() =>
      expect(screen.getByTestId('build-progress')).toHaveTextContent(STEP_LABELS.implement),
    );
  });

  it('ignores a terminal event belonging to a previous run', async () => {
    installHangingIntent();
    renderPanel();

    // A stale terminal event sits in the buffer BEFORE this task starts.
    emit('run.finished', { ok: true, run_id: 'old', stage: 'build' });

    fireEvent.click(await screen.findByTestId('build-proceed'));

    // The spinner must survive it — the baseline excludes anything before the task began.
    expect(await screen.findByTestId('build-progress')).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId('build-progress')).toBeInTheDocument();
  });

  it('ignores a terminal event for a different stage', async () => {
    installHangingIntent();
    renderPanel();

    fireEvent.click(await screen.findByTestId('build-proceed'));
    expect(await screen.findByTestId('build-progress')).toBeInTheDocument();

    emit('run.finished', { ok: true, run_id: 'r2', stage: 'test' });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId('build-progress')).toBeInTheDocument();
  });

  it('clears the spinner when the run reports failure', async () => {
    installHangingIntent();
    renderPanel();

    fireEvent.click(await screen.findByTestId('build-proceed'));
    expect(await screen.findByTestId('build-progress')).toBeInTheDocument();

    emit('run.finished', { ok: false, error: 'budget exhausted', stage: 'build' });
    await waitFor(() => expect(screen.queryByTestId('build-progress')).not.toBeInTheDocument());
  });
});
