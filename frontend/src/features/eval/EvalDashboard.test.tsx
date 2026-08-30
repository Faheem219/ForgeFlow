import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { EvalRecordDto, EvalSummary, EvalSummaryDto } from '../../lib/types';
import { EvalDashboard } from './EvalDashboard';
import { VIZ, pct } from './viz';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function record(
  spec_id: string,
  first: number | null,
  post: number | null,
  overrides: Partial<EvalRecordDto> = {},
): EvalRecordDto {
  const block = (rate: number | null) =>
    rate === null
      ? { total: 0, passed: 0, failed: 0, rate: null }
      : { total: 4, passed: Math.round(rate * 4), failed: 4 - Math.round(rate * 4), rate };
  return {
    spec_id,
    title: spec_id,
    difficulty: 'simple',
    outcome: 'delivered',
    first_pass: block(first),
    post_repair: block(post),
    repair_delta: first === null || post === null ? null : Number((post - first).toFixed(4)),
    repair_iterations: 2,
    repair_outcome: 'fixed',
    regressions: 0,
    tokens: 1500,
    inr_cost: 3,
    screenshot_to_url_seconds: null,
    wall_clock_seconds: 20,
    live_url: null,
    error: null,
    ...overrides,
  };
}

const RECORDS = [
  record('todo-list', 0.25, 1),
  record('notes-crud', 0.75, 1),
  record('broken', null, null, { outcome: 'failed', repair_iterations: 0, error: 'boom' }),
];

const dist = (mean: number | null, median: number | null, count = 2) => ({
  count,
  mean,
  median,
  min: mean,
  max: mean,
  values: [],
});

const SUMMARY: EvalSummary = {
  specs: 3,
  scored: 2,
  outcomes: { delivered: 2, escalated: 0, failed: 1 },
  first_pass: dist(0.5, 0.5),
  post_repair: dist(1, 1),
  repair_delta: dist(0.5, 0.5),
  repair_iterations: dist(2, 2, 3),
  tokens: dist(1500, 1500, 3),
  inr_cost: dist(3, 3, 3),
  screenshot_to_url_seconds: dist(null, null, 0),
  wall_clock_seconds: dist(20, 20, 3),
  specs_improved_by_repair: 2,
  specs_unchanged_by_repair: 0,
  green_first_pass: 0,
  green_post_repair: 2,
  total_regressions: 0,
  by_difficulty: { simple: { specs: 2, first_pass_mean: 0.5, post_repair_mean: 1 } },
};

const AVAILABLE: EvalSummaryDto = {
  available: true,
  summary: SUMMARY,
  records: RECORDS,
  source: 'eval-1.json',
};

function wrapper(): ({ children }: { children: ReactNode }) => JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('EvalDashboard', () => {
  let payload: EvalSummaryDto = AVAILABLE;
  let runs: unknown[] = [{ source: 'eval-1.json', legs: ['build'], specs: 3 }];

  beforeEach(() => {
    payload = AVAILABLE;
    runs = [{ source: 'eval-1.json', legs: ['build'], specs: 3 }];
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input);
      if (url.includes('/eval/runs')) return Promise.resolve(json(runs));
      if (url.includes('/eval/summary')) return Promise.resolve(json(payload));
      if (url.includes('/eval/report.csv')) {
        return Promise.resolve(new Response('spec_id,title\ntodo-list,Todo', { status: 200 }));
      }
      if (url.includes('/eval/report.md')) {
        return Promise.resolve(new Response('# ForgeFlow evaluation', { status: 200 }));
      }
      return Promise.resolve(json({}));
    });
  });

  afterEach(() => vi.restoreAllMocks());

  async function renderDash(): Promise<void> {
    render(<EvalDashboard />, { wrapper: wrapper() });
    await act(async () => {
      for (let i = 0; i < 3; i += 1) await new Promise((r) => setTimeout(r, 0));
    });
  }

  it('leads with the repair delta as the headline number', async () => {
    await renderDash();

    expect(screen.getByTestId('stat-repair-delta')).toHaveTextContent('+50%');
    expect(screen.getByTestId('stat-first-pass')).toHaveTextContent('50%');
    expect(screen.getByTestId('stat-post-repair')).toHaveTextContent('100%');
    expect(screen.getByTestId('stat-delivered')).toHaveTextContent('2/3');
  });

  it('charts first-pass → post-repair per spec', async () => {
    await renderDash();

    expect(screen.getByTestId('dumbbell-chart')).toBeInTheDocument();
    expect(screen.getByTestId('dumbbell-row-todo-list')).toBeInTheDocument();
    expect(screen.getByTestId('dumbbell-row-notes-crud')).toBeInTheDocument();
  });

  it('names both series in a legend, so identity is never colour alone', async () => {
    await renderDash();

    const legend = screen.getAllByTestId('viz-legend')[0];
    expect(legend).toHaveTextContent('First-pass');
    expect(legend).toHaveTextContent('Post-repair');
  });

  it('shows a spec that produced no measurement as unmeasured, never as zero', async () => {
    await renderDash();

    const row = screen.getByTestId('dumbbell-row-broken');
    expect(row).toHaveTextContent('not measured');

    const tableRow = screen.getByTestId('eval-row-broken');
    expect(tableRow).toHaveTextContent('—');
    expect(tableRow).not.toHaveTextContent('0%');
  });

  it('renders the iteration histogram and the cost chart', async () => {
    await renderDash();

    expect(screen.getByTestId('histogram')).toBeInTheDocument();
    expect(screen.getByTestId('histogram-bucket-2')).toHaveTextContent('2');
    expect(screen.getByTestId('bar-chart')).toBeInTheDocument();
    expect(screen.getByTestId('bar-row-todo-list')).toHaveTextContent('1.5K');
  });

  it('omits the time-to-URL chart when no spec reached a live URL', async () => {
    await renderDash();
    expect(screen.queryByText('Idea → live URL')).not.toBeInTheDocument();
  });

  it('shows the time-to-URL chart once deploy legs have run', async () => {
    payload = {
      ...AVAILABLE,
      summary: { ...SUMMARY, screenshot_to_url_seconds: dist(90, 90, 1) },
      records: [record('todo-list', 0.5, 1, { screenshot_to_url_seconds: 90 })],
    };
    await renderDash();

    expect(screen.getByText(/Idea → live URL/)).toBeInTheDocument();
  });

  it('carries every value in a table, so nothing is reachable only by hovering', async () => {
    await renderDash();

    const table = screen.getByTestId('eval-table');
    expect(table).toHaveTextContent('todo-list');
    expect(table).toHaveTextContent('+75%');
    expect(screen.getByTestId('eval-row-todo-list')).toHaveTextContent('delivered');
  });

  it('shows a tooltip on hover with the value leading', async () => {
    await renderDash();

    fireEvent.mouseEnter(screen.getByTestId('dumbbell-row-todo-list'));

    const tip = screen.getByTestId('viz-tooltip');
    expect(tip).toHaveTextContent('100%');
    expect(tip).toHaveTextContent('Post-repair');
  });

  it('exports CSV through the authenticated endpoint', async () => {
    const click = vi.fn();
    vi.spyOn(document, 'createElement').mockImplementation(((tag: string) => {
      if (tag === 'a') return { href: '', download: '', click } as unknown as HTMLElement;
      return document.createElementNS('http://www.w3.org/1999/xhtml', tag) as HTMLElement;
    }) as typeof document.createElement);
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:x');
    globalThis.URL.revokeObjectURL = vi.fn();

    await renderDash();
    fireEvent.click(screen.getByTestId('export-csv'));

    await waitFor(() => {
      const called = vi
        .mocked(globalThis.fetch)
        .mock.calls.some(([input]) => String(input).includes('/eval/report.csv'));
      expect(called).toBe(true);
      expect(click).toHaveBeenCalled();
    });
  });

  it('explains how to produce evidence when the harness has never run', async () => {
    payload = { available: false };
    await renderDash();

    expect(screen.getByText('No evaluation runs yet')).toBeInTheDocument();
    expect(screen.getByText(/app.eval.runner/)).toBeInTheDocument();
    expect(screen.queryByTestId('eval-dashboard')).not.toBeInTheDocument();
  });
});

describe('chart formatting', () => {
  it('renders a missing value as an em dash, never a zero', () => {
    expect(pct(null)).toBe('—');
    expect(pct(null, true)).toBe('—');
    expect(pct(0)).toBe('0%');
  });

  it('signs a delta so an improvement reads as one', () => {
    expect(pct(0.42, true)).toBe('+42%');
    expect(pct(-0.1, true)).toBe('-10%');
  });

  it('keeps the accent and de-emphasis colours distinct', () => {
    // The validated pair: accent carries the story, gray carries the context.
    expect(VIZ.accent).toBe('#6366f1');
    expect(VIZ.context).toBe('#64748b');
    expect(VIZ.accent).not.toBe(VIZ.context);
  });
});
