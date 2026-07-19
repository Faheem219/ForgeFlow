import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { TestRunDto } from '../../lib/types';
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

interface MockState {
  runs: TestRunDto[];
  postCalls: Array<Record<string, unknown>>;
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
      if (url.includes('/tests/runs')) return Promise.resolve(json(state.runs));
      return Promise.resolve(json({}));
    }),
  );
}

function renderPanel(run: TestRunDto | null = RUN) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<ResultsPanel projectId={PROJECT} run={run} />, { wrapper });
}

let state: MockState;

beforeEach(() => {
  state = { runs: [RUN], postCalls: [] };
});
afterEach(() => vi.unstubAllGlobals());

describe('ResultsPanel', () => {
  it('renders per-test results with their criterion and summary', async () => {
    installFetch(state);
    renderPanel();

    expect(await screen.findByTestId('test-summary')).toHaveTextContent('1/2 passing');
    expect(screen.getByText('adds a todo')).toBeInTheDocument();
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

  it('invites a first run when there are none', async () => {
    state.runs = [];
    installFetch(state);
    renderPanel(null);

    expect(await screen.findByText('No test runs yet')).toBeInTheDocument();
  });
});
