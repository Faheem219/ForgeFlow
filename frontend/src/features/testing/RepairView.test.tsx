import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { RealtimeEvent } from '../../lib/wsClient';
import type { RepairLoopDto } from '../../lib/types';
import { RepairView, Sparkline } from './RepairView';

const PROJECT = 'p1';
const SRC = 'backend/src/features/todos/todos.controller.ts';

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const REPORT: RepairLoopDto = {
  outcome: 'fixed',
  metrics: {
    initial_failing: 3,
    final_failing: 0,
    failing_by_iteration: [2, 1, 0],
    regressions_introduced: 1,
    iterations: 3,
    tokens_spent: 4200,
    cost_inr: 3.5,
    wall_clock_s: 42.5,
  },
  attempts: [
    { id: 'a1', iteration: 1, target_files: [SRC], diff_ref: 'fs:d1', outcome: 'fixed' },
    { id: 'a2', iteration: 2, target_files: [SRC], diff_ref: 'fs:d2', outcome: 'regressed' },
    { id: 'a3', iteration: 3, target_files: [SRC], diff_ref: 'fs:d3', outcome: 'fixed' },
  ],
  final_run_id: 'run9',
  escalation: null,
};

const DIFF = '@@ -1,3 +1,4 @@\n-  return res.status(201)\n+  if (!title) return res.status(400)\n';

function iterationEvent(i: number, failing: number): RealtimeEvent {
  return {
    event: 'repair.iteration',
    project_id: PROJECT,
    stage: 'test',
    payload: { i, failing_count: failing, regressions: 0, tokens: i * 100 },
    seq: i,
    ts: '',
  };
}

function installFetch(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>((input) => {
      if (String(input).includes('/diff')) {
        return Promise.resolve(json({ attempt_id: 'a1', iteration: 1, diff: DIFF }));
      }
      return Promise.resolve(json({}));
    }),
  );
}

function renderView(props: Partial<Parameters<typeof RepairView>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(
    <RepairView
      projectId={PROJECT}
      report={REPORT}
      running={false}
      canRepair
      onRepair={vi.fn()}
      onCancel={vi.fn()}
      {...props}
    />,
    { wrapper },
  );
}

beforeEach(() => {
  useRealtimeStore.setState({ events: [] });
  installFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe('Sparkline', () => {
  it('renders the failing-count trail', () => {
    render(<Sparkline values={[3, 2, 0]} />);
    expect(screen.getByTestId('failing-trail')).toHaveTextContent('3 → 2 → 0');
  });

  it('says so when there is nothing yet', () => {
    render(<Sparkline values={[]} />);
    expect(screen.getByText('no iterations yet')).toBeInTheDocument();
  });
});

describe('RepairView', () => {
  it('shows the shrinking failing set, cost and outcome from the report', () => {
    renderView();

    expect(screen.getByTestId('failing-trail')).toHaveTextContent('2 → 1 → 0');
    expect(screen.getByTestId('repair-outcome')).toHaveTextContent('fixed');
    expect(screen.getByTestId('repair-iterations')).toHaveTextContent('3');
    expect(screen.getByTestId('repair-regressions')).toHaveTextContent('1');
    expect(screen.getByTestId('repair-tokens')).toHaveTextContent('4200');
    expect(screen.getByTestId('repair-cost')).toHaveTextContent('₹3.50');
  });

  it('streams live iterations while the loop runs', () => {
    useRealtimeStore.setState({
      events: [iterationEvent(1, 3), iterationEvent(2, 2), iterationEvent(3, 1)],
    });
    renderView({ running: true, report: null });

    // Live events drive the trail while running — the loop is watchable as it happens.
    expect(screen.getByTestId('failing-trail')).toHaveTextContent('3 → 2 → 1');
    expect(screen.getByTestId('repair-running')).toBeInTheDocument();
  });

  it('starts a fresh trail when a second loop begins', () => {
    // The event ring outlives a loop: "Repair again" must not graft onto the previous trail.
    useRealtimeStore.setState({
      events: [
        iterationEvent(1, 5),
        iterationEvent(2, 4),
        iterationEvent(1, 3),
        iterationEvent(2, 1),
      ],
    });
    renderView({ running: true, report: null });

    expect(screen.getByTestId('failing-trail')).toHaveTextContent('3 → 1');
    expect(screen.getByTestId('repair-iterations')).toHaveTextContent('2');
  });

  it('ignores iteration events from other projects', () => {
    useRealtimeStore.setState({
      events: [{ ...iterationEvent(1, 9), project_id: 'other' }, iterationEvent(2, 4)],
    });
    renderView({ running: true, report: null });

    expect(screen.getByTestId('failing-trail')).toHaveTextContent('4');
    expect(screen.getByTestId('failing-trail')).not.toHaveTextContent('9');
  });

  it('marks an attempt the loop undid, so its diff is not read as applied code', () => {
    renderView({
      report: {
        ...REPORT,
        attempts: [{ ...REPORT.attempts[0], outcome: 'regressed', reverted: true }],
      },
    });

    expect(screen.getByTestId('attempt-reverted')).toHaveTextContent('reverted');
  });

  it('lists each attempt with its verdict and loads its diff on demand', async () => {
    renderView();

    expect(screen.getAllByTestId('repair-attempt')).toHaveLength(3);
    expect(screen.getByText('regressed')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('toggle-diff-1'));
    await waitFor(() => expect(screen.getByTestId('attempt-diff')).toBeInTheDocument());
    expect(screen.getByTestId('attempt-diff')).toHaveTextContent(
      'if (!title) return res.status(400)',
    );
  });

  it('exposes repair and cancel controls', () => {
    const onRepair = vi.fn();
    const onCancel = vi.fn();

    const { rerender } = renderView({ onRepair, onCancel });
    fireEvent.click(screen.getByTestId('run-repair'));
    expect(onRepair).toHaveBeenCalled();

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      <QueryClientProvider client={client}>
        <RepairView
          projectId={PROJECT}
          report={REPORT}
          running
          canRepair
          onRepair={onRepair}
          onCancel={onCancel}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId('cancel-repair'));
    expect(onCancel).toHaveBeenCalled();
  });

  it('disables repair when there is no failing run to fix', () => {
    renderView({ canRepair: false, report: null });
    expect(screen.getByTestId('run-repair')).toBeDisabled();
  });
});
