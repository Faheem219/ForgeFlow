import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { LiveRunDto, StageStateDto, ValidationReportDto } from '../../lib/types';
import type { RealtimeEvent } from '../../lib/wsClient';
import { Validate } from './Validate';

const PROJECT = 'p1';
const LIVE_URL = 'https://forgeflow-web.vercel.app';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stage(name: string, status: string): StageStateDto {
  return { stage: name, status, updated_at: '' } as StageStateDto;
}

const VERIFIED: ValidationReportDto = {
  outcome: 'validated',
  url: LIVE_URL,
  cycles: [
    {
      index: 1,
      live_run_id: 'r1',
      failing: 1,
      green: false,
      diagnosis: 'code',
      repair_outcome: 'fixed',
      redeploy_status: 'live',
      note: 'failures name application behaviour',
    },
    {
      index: 2,
      live_run_id: 'r2',
      failing: 0,
      green: true,
      diagnosis: null,
      repair_outcome: null,
      redeploy_status: null,
      note: null,
    },
  ],
  live_run_id: 'r2',
  reason: null,
  summary: 'Live validation passed on cycle 2.',
  failing_tests: [],
  repair_escalation: null,
  wall_clock_s: 42.5,
};

const CAPPED: ValidationReportDto = {
  outcome: 'escalated',
  url: null,
  cycles: [
    {
      index: 1,
      live_run_id: 'r1',
      failing: 2,
      green: false,
      diagnosis: 'code',
      repair_outcome: 'fixed',
      redeploy_status: 'live',
      note: null,
    },
  ],
  live_run_id: 'r1',
  reason: 'cycle_cap',
  summary: 'Stopped after 1 repair→redeploy→re-validate cycle without a green live run.',
  failing_tests: [],
  repair_escalation: null,
  wall_clock_s: 60,
};

const ENV_FAILED: ValidationReportDto = {
  ...CAPPED,
  reason: 'env_config',
  summary: 'Live validation failed for a configuration reason — check the env wiring.',
  cycles: [{ ...CAPPED.cycles[0], diagnosis: 'env', repair_outcome: null, redeploy_status: null }],
};

const LIVE_RUN: LiveRunDto = {
  id: 'r1',
  total: 2,
  passed: 1,
  failed: 1,
  green: false,
  created_at: '',
  results: [
    {
      name: '[ac-list] shows todos',
      status: 'passed',
      framework: 'playwright',
      criterion_id: 'ac-list',
      file: 'e2e/todos.spec.ts',
      duration_ms: 120,
      failure: null,
    },
    {
      name: '[ac-add] adds a todo',
      status: 'failed',
      framework: 'playwright',
      criterion_id: 'ac-add',
      file: 'e2e/todos.spec.ts',
      duration_ms: 300,
      failure: {
        message: 'the new todo never rendered',
        assertion: null,
        stack: null,
        files_referenced: [],
      },
    },
  ],
};

function wrapper(): ({ children }: { children: ReactNode }) => JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('Validate', () => {
  let deployStatus = 'complete';
  let report: ValidationReportDto | null = null;
  let liveRun: LiveRunDto | null = null;

  beforeEach(() => {
    deployStatus = 'complete';
    report = null;
    liveRun = null;
    useRealtimeStore.setState({ events: [] });
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input);
      if (url.includes('/stages')) {
        return Promise.resolve(json([stage('deploy', deployStatus), stage('validate', 'empty')]));
      }
      if (url.includes('/validate/live-run')) return Promise.resolve(json(liveRun));
      if (url.includes('/validate/latest')) return Promise.resolve(json(report));
      return Promise.resolve(json({}));
    });
  });

  afterEach(() => vi.restoreAllMocks());

  async function renderPanel(): Promise<void> {
    render(<Validate projectId={PROJECT} />, { wrapper: wrapper() });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  function emit(...payloads: Record<string, unknown>[]): void {
    const events: RealtimeEvent[] = payloads.map((payload, i) => ({
      event: 'validate.status',
      project_id: PROJECT,
      stage: 'validate',
      payload,
      seq: i + 1,
      ts: '',
    }));
    act(() => {
      useRealtimeStore.setState((s) => ({ events: [...s.events, ...events] }));
    });
  }

  it('puts the final live link in front of the user when verified', async () => {
    report = VERIFIED;
    await renderPanel();

    const link = screen.getByTestId('final-live-link');
    expect(link).toHaveAttribute('href', LIVE_URL);
    expect(screen.getByTestId('validate-success')).toHaveTextContent('Verified');
    // A verified run is not an escalation.
    expect(screen.queryByTestId('validate-escalation')).not.toBeInTheDocument();
  });

  it('draws the repair→redeploy→re-validate timeline', async () => {
    report = VERIFIED;
    await renderPanel();

    const timeline = screen.getByTestId('validate-timeline');
    expect(timeline).toHaveTextContent('1 live test failed');
    expect(timeline).toHaveTextContent('Diagnosed: code bug');
    expect(timeline).toHaveTextContent('Repair fixed');
    expect(timeline).toHaveTextContent('Redeploy live');
    expect(screen.getByTestId('cycle-2')).toHaveTextContent('Live tests passed');
  });

  it('shows per-test live results with their criteria', async () => {
    liveRun = LIVE_RUN;
    await renderPanel();

    const results = screen.getByTestId('live-results');
    expect(results).toHaveTextContent('[ac-add] adds a todo');
    expect(results).toHaveTextContent('ac-list');
    expect(results).toHaveTextContent('the new todo never rendered');
  });

  it('explains a bounded escalation with its reason', async () => {
    report = CAPPED;
    await renderPanel();

    expect(screen.getByTestId('validate-reason')).toHaveTextContent(
      'Hit the repair/redeploy cycle cap',
    );
    expect(screen.getByTestId('validate-summary')).toHaveTextContent('Stopped after 1');
    expect(screen.queryByTestId('final-live-link')).not.toBeInTheDocument();
  });

  it('distinguishes a config problem from a code bug', async () => {
    report = ENV_FAILED;
    await renderPanel();

    expect(screen.getByTestId('validate-reason')).toHaveTextContent('configuration problem');
    expect(screen.getByTestId('validate-timeline')).toHaveTextContent('Diagnosed: configuration');
  });

  it('reuses the repair escalation UI when the inner loop gave up', async () => {
    report = {
      ...CAPPED,
      reason: 'repair_escalated',
      repair_escalation: {
        reason: 'stalled',
        summary: 'no progress across iterations',
        failing_tests: [
          {
            name: '[ac-add] adds a todo',
            criterion_id: 'ac-add',
            file: 'e2e/t.spec.ts',
            message: 'x',
          },
        ],
        diffs_tried: [],
        metrics: {
          iterations: 3,
          initial_failing: 2,
          final_failing: 2,
          failing_by_iteration: [2, 2, 2],
          regressions_introduced: 0,
          tokens_spent: 100,
          cost_inr: 1,
          wall_clock_s: 30,
        },
        resume: { stage: 'build', action: 'refine', hint: 'Describe what to change' },
      },
    } as ValidationReportDto;
    await renderPanel();

    expect(screen.getByTestId('repair-escalation')).toBeInTheDocument();
    expect(screen.getByTestId('escalation-reason')).toHaveTextContent('Stopped making progress');
  });

  it('blocks validating with a reason until a deployment is live', async () => {
    deployStatus = 'empty';
    await renderPanel();

    const button = screen.getByTestId('validate-run');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'Validation needs a live deployment first.');
    expect(screen.getByTestId('validate-prereq-warning')).toBeInTheDocument();
  });

  it('runs validation through the conductor', async () => {
    await renderPanel();

    fireEvent.click(screen.getByTestId('validate-run'));

    await waitFor(() => {
      const call = vi
        .mocked(globalThis.fetch)
        .mock.calls.find(([input]) => String(input).endsWith('/intent'));
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        stage: 'validate',
        action: 'proceed',
      });
    });
  });

  it('follows the cycle live as validate.status events arrive', async () => {
    await renderPanel();

    fireEvent.click(screen.getByTestId('validate-run'));
    emit({ step: 'validating' });
    expect(screen.getByTestId('validate-progress')).toHaveTextContent('Running tests against');

    emit({ step: 'diagnosed', diagnosis: 'code' }, { step: 'repairing' });
    expect(screen.getByTestId('validate-progress')).toHaveTextContent('Repairing in Build');

    emit({ step: 'redeploying' });
    expect(screen.getByTestId('validate-progress')).toHaveTextContent('Redeploying the fix');
  });
});
