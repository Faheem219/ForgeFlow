import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { act } from 'react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { BudgetDto, ProjectCostDto, RunDto } from '../../lib/types';
import type { RealtimeEvent } from '../../lib/wsClient';
import { CostPanel } from './CostPanel';
import { inr } from './format';

const PROJECT = 'p1';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function budget(over: Partial<BudgetDto> = {}): BudgetDto {
  return {
    spent_inr: 12.5,
    cap_inr: 100,
    headroom_inr: 87.5,
    used_ratio: 0.125,
    warn_ratio: 0.8,
    warning: false,
    halted: false,
    ...over,
  };
}

const COST: ProjectCostDto = {
  project_id: PROJECT,
  tokens: 15000,
  inr: 12.5,
  runs: 3,
  by_kind: {
    'codegen:build': { runs: 2, tokens: 12000, inr: 10 },
    'repair:loop': { runs: 1, tokens: 3000, inr: 2.5 },
  },
  project: budget(),
  global_budget: budget({ spent_inr: 40, cap_inr: null, headroom_inr: null, used_ratio: null }),
};

const RUNS: RunDto[] = [
  {
    id: 'r1',
    kind: 'repair:loop',
    tokens: 3000,
    inr: 2.5,
    outcome: 'fixed',
    tool_calls: 4,
    started_at: '2026-07-21T10:00:00Z',
    finished_at: '2026-07-21T10:00:30Z',
    duration_s: 30,
  },
  {
    id: 'r2',
    kind: 'codegen:build',
    tokens: 12000,
    inr: 10,
    outcome: null,
    tool_calls: 9,
    started_at: '2026-07-21T09:00:00Z',
    finished_at: null,
    duration_s: null,
  },
];

function wrapper(): ({ children }: { children: ReactNode }) => JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('CostPanel', () => {
  let cost: ProjectCostDto = COST;

  beforeEach(() => {
    cost = COST;
    useRealtimeStore.setState({ events: [] });
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input);
      if (url.includes('/runs')) return Promise.resolve(json(RUNS));
      if (url.includes('/cost')) return Promise.resolve(json(cost));
      return Promise.resolve(json({}));
    });
  });

  afterEach(() => vi.restoreAllMocks());

  async function renderPanel(): Promise<void> {
    render(<CostPanel projectId={PROJECT} />, { wrapper: wrapper() });
    await act(async () => {
      for (let i = 0; i < 3; i += 1) await new Promise((r) => setTimeout(r, 0));
    });
  }

  it('shows spend, tokens and run count', async () => {
    await renderPanel();

    expect(screen.getByTestId('cost-total')).toHaveTextContent('₹12.50');
    expect(screen.getByTestId('cost-tokens')).toHaveTextContent('15K');
    expect(screen.getByTestId('cost-runs')).toHaveTextContent('3');
  });

  it('shows headroom against a cap', async () => {
    await renderPanel();

    const bar = screen.getByTestId('budget-project');
    expect(bar).toHaveTextContent('₹12.50');
    expect(bar).toHaveTextContent('of ₹100.00');
  });

  it('says "no cap" rather than implying an exhausted budget', async () => {
    /* An uncapped budget has unlimited headroom — a 0-width bar would read as "stop". */
    await renderPanel();

    const bar = screen.getByTestId('budget-global');
    expect(bar).toHaveTextContent('no cap');
    expect(bar).not.toHaveTextContent('of ₹');
  });

  it('warns in words before the cap is reached, not just in colour', async () => {
    cost = {
      ...COST,
      project: budget({ spent_inr: 85, headroom_inr: 15, used_ratio: 0.85, warning: true }),
    };
    await renderPanel();

    const bar = screen.getByTestId('budget-project');
    expect(bar).toHaveTextContent('Approaching cap');
    expect(bar).toHaveTextContent('₹15.00 left before work halts');
  });

  it('explains a halt, since that is when the user most needs to know why work stopped', async () => {
    cost = {
      ...COST,
      project: budget({ spent_inr: 120, headroom_inr: -20, used_ratio: 1.2, halted: true }),
    };
    await renderPanel();

    const bar = screen.getByTestId('budget-project');
    expect(bar).toHaveTextContent('Halted');
    expect(bar).toHaveTextContent('Runs are blocked until the cap is raised');
  });

  it('breaks spend down by the kind of work, most expensive first', async () => {
    await renderPanel();

    const kinds = screen.getByTestId('cost-by-kind');
    expect(kinds).toHaveTextContent('codegen:build');
    expect(kinds).toHaveTextContent('₹10.00');
    // The costliest kind leads.
    expect(kinds.textContent?.indexOf('codegen:build')).toBeLessThan(
      kinds.textContent?.indexOf('repair:loop') ?? 0,
    );
  });

  it('lists runs with their outcome and duration', async () => {
    await renderPanel();

    expect(screen.getByTestId('run-r1')).toHaveTextContent('fixed');
    expect(screen.getByTestId('run-r1')).toHaveTextContent('30.0s');
  });

  it('shows an unfinished run as running rather than as instant', async () => {
    await renderPanel();
    expect(screen.getByTestId('run-r2')).toHaveTextContent('running');
  });

  it('refreshes when a budget event arrives mid-run', async () => {
    await renderPanel();
    const before = vi.mocked(globalThis.fetch).mock.calls.length;

    const event: RealtimeEvent = {
      event: 'budget.warning',
      project_id: PROJECT,
      stage: null,
      payload: { scope: 'project' },
      seq: 1,
      ts: '',
    };
    act(() => {
      useRealtimeStore.setState((s) => ({ events: [...s.events, event] }));
    });
    await act(async () => {
      for (let i = 0; i < 3; i += 1) await new Promise((r) => setTimeout(r, 0));
    });

    expect(vi.mocked(globalThis.fetch).mock.calls.length).toBeGreaterThan(before);
  });
});

describe('currency formatting', () => {
  it('always shows paise, so small spends are not rounded away', () => {
    expect(inr(0)).toBe('₹0.00');
    expect(inr(0.004)).toBe('₹0.00');
    expect(inr(12.5)).toBe('₹12.50');
    expect(inr(-20)).toBe('₹-20.00');
  });
});
