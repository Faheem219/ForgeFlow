import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { TestRunDto, TestRunSummaryDto } from '../../lib/types';
import { ResultsPanel } from './ResultsPanel';

const PROJECT = 'p1';
const TEST_FILE = 'backend/src/features/todos/todos.test.ts';
const SRC = 'backend/src/features/todos/todos.controller.ts';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const RUN: TestRunDto = {
  id: 'run1',
  total: 2,
  passed: 1,
  failed: 1,
  skipped: 0,
  green: false,
  env: 'sandbox',
  created_at: '',
  suite_refs: [],
  stdout_ref: 'fs:out',
  results: [
    {
      name: 'adds a todo',
      status: 'passed',
      framework: 'jest',
      criterion_id: 'ac-add',
      file: TEST_FILE,
      duration_ms: 4,
      failure: null,
    },
    {
      name: 'rejects an empty title',
      status: 'failed',
      framework: 'jest',
      criterion_id: 'ac-empty',
      file: TEST_FILE,
      duration_ms: 3,
      failure: {
        message: 'expected 400, received 201',
        assertion: 'rejects an empty title',
        stack: `at Object.<anonymous> (${SRC}:22:18)`,
        files_referenced: [SRC],
      },
    },
  ],
  failures: [],
};

/**
 * A live-validation run (phase-39): the same collection, but only the E2E subset against the
 * deployed URL. It is newer than any sandbox run once Validate has been used.
 */
const LIVE_RUN: TestRunDto = {
  id: 'live1',
  total: 1,
  passed: 1,
  failed: 0,
  skipped: 0,
  green: true,
  env: 'live',
  created_at: '',
  suite_refs: [],
  stdout_ref: null,
  results: [
    {
      name: 'home page renders the todo list',
      status: 'passed',
      framework: 'playwright',
      criterion_id: null,
      file: 'frontend/e2e/home.spec.ts',
      duration_ms: 900,
      failure: null,
    },
  ],
  failures: [],
};

interface MockState {
  runs: TestRunDto[];
  postCalls: Array<Record<string, unknown>>;
  listUrls: string[];
}

/** The list endpoint returns counts only — mirror that, or the mock hides the hydration path. */
function summary(run: TestRunDto): TestRunSummaryDto {
  const { results, failures, suite_refs, stdout_ref, ...rest } = run;
  void results;
  void failures;
  void suite_refs;
  void stdout_ref;
  return rest;
}

function installFetch(state: MockState): void {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      if (url.includes('/stdout')) {
        return Promise.resolve(json({ id: 'run1', stdout: 'PASS todos.test.ts' }));
      }
      if (url.includes('/tests/run') && init?.method === 'POST') {
        state.postCalls.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return Promise.resolve(json(RUN));
      }
      const detail = /\/tests\/runs\/([^/?]+)$/.exec(url);
      if (detail) {
        const found = state.runs.find((r) => r.id === detail[1]);
        return Promise.resolve(found ? json(found) : json({ detail: 'not found' }, 404));
      }
      if (url.includes('/tests/runs')) {
        // Mirror the server: the listing is scoped by `env` (default sandbox, `all` for both).
        state.listUrls.push(url);
        const env = new URL(url, 'http://test').searchParams.get('env') ?? 'sandbox';
        const scoped = env === 'all' ? state.runs : state.runs.filter((r) => r.env === env);
        return Promise.resolve(json(scoped.map(summary)));
      }
      return Promise.resolve(json({}));
    }),
  );
}

function renderPanel(run: TestRunSummaryDto | null = summary(RUN)) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<ResultsPanel projectId={PROJECT} run={run} />, { wrapper });
}

let state: MockState;

beforeEach(() => {
  state = { runs: [RUN], postCalls: [], listUrls: [] };
});
afterEach(() => vi.unstubAllGlobals());

describe('ResultsPanel', () => {
  it('renders per-test results with their criterion and summary', async () => {
    installFetch(state);
    renderPanel();

    expect(await screen.findByTestId('test-summary')).toHaveTextContent('1/2 passing');
    // The counts come from the run list; the per-test rows are hydrated by a second request.
    expect(await screen.findByText('adds a todo')).toBeInTheDocument();
    expect(screen.getByText('rejects an empty title')).toBeInTheDocument();
    // Traceability back to the acceptance criterion (phase-25 → 27 → 28).
    expect(screen.getByText('ac-empty')).toBeInTheDocument();
    expect(screen.getByTestId('test-failed')).toBeInTheDocument();
  });

  it('expands a failure to show its message, stack and implicated files', async () => {
    installFetch(state);
    renderPanel();

    fireEvent.click(await screen.findByTestId('test-failed'));

    expect(screen.getByText('expected 400, received 201')).toBeInTheDocument();
    // The stack and the implicated-files line both point at the source the repair loop will patch.
    expect(screen.getByText(/at Object\.<anonymous>/)).toBeInTheDocument();
    expect(screen.getByText(/Implicated:/)).toHaveTextContent(SRC);
  });

  it('runs the suites with the chosen scope and filter', async () => {
    installFetch(state);
    renderPanel();

    fireEvent.change(await screen.findByTestId('test-scope'), { target: { value: 'unit' } });
    fireEvent.change(screen.getByTestId('test-filter'), { target: { value: 'todos' } });
    fireEvent.click(screen.getByTestId('run-tests'));

    await waitFor(() =>
      expect(state.postCalls.at(-1)).toMatchObject({ scope: 'unit', filter: 'todos' }),
    );
  });

  it('shows the reporter output on demand', async () => {
    installFetch(state);
    renderPanel();

    fireEvent.click(await screen.findByTestId('toggle-stdout'));
    await waitFor(() =>
      expect(screen.getByTestId('test-stdout')).toHaveTextContent('PASS todos.test.ts'),
    );
  });

  it('lists sandbox runs only, so a live-validation run cannot replace the suite', async () => {
    // Regression: an unscoped listing put the newest *live* run first, and the panel reads the
    // head of that list — so after running Validate the Test stage showed the E2E smoke test as if
    // the suite had shrunk to one, until the suites were re-run.
    state.runs = [LIVE_RUN, RUN];
    installFetch(state);
    renderPanel(null);

    expect(await screen.findByTestId('test-summary')).toHaveTextContent('1/2 passing');
    expect(await screen.findByText('adds a todo')).toBeInTheDocument();
    expect(screen.queryByText('home page renders the todo list')).not.toBeInTheDocument();
    expect(state.listUrls.length).toBeGreaterThan(0);
    expect(state.listUrls.every((u) => u.includes('env=sandbox'))).toBe(true);
  });

  it('invites a first run when there are none', async () => {
    state.runs = [];
    installFetch(state);
    renderPanel(null);

    expect(await screen.findByText('No test runs yet')).toBeInTheDocument();
  });
});
