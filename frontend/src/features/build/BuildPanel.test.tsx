import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BuildPanel } from './BuildPanel';

// The IDE + preview are Monaco/xterm-backed — stub them so the panel logic tests stay light.
vi.mock('../ide/Ide', () => ({ default: () => <div data-testid="mock-ide">ide</div> }));
vi.mock('../ide/Preview', () => ({ default: () => <div data-testid="mock-preview">preview</div> }));

const PROJECT = 'p1';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

interface MockState {
  buildStatus: string;
  reports: Array<Record<string, unknown>>;
  reportDetail: Record<string, unknown>;
  intentCalls: Array<Record<string, unknown>>;
}

const REPORT = {
  boot_status: 'healthy',
  files_changed: ['backend/src/features/todo/todo.routes.ts', 'frontend/src/features/todo/TodoPage.tsx'],
  features_built: ['Todos'],
  follow_ups: [],
  notes: [],
  commit: 'abcdef1234',
  tests_passed: true,
  summary: 'Built the Todos feature.',
  plan: 'plan',
};

function reportArtifact(): Record<string, unknown> {
  return {
    id: 'br1',
    project_id: PROJECT,
    stage: 'build',
    type: 'code_change',
    version: 1,
    ref: null,
    meta: { kind: 'build_report', boot_status: 'healthy' },
    created_at: '',
  };
}

function installFetch(state: MockState): void {
  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (url.includes('/stages')) {
      return Promise.resolve(
        json([{ stage: 'build', status: state.buildStatus, artifacts: [], updated_at: '' }]),
      );
    }
    if (url.includes('/artifacts?')) return Promise.resolve(json(state.reports));
    if (url.includes('/artifacts/')) return Promise.resolve(json(state.reportDetail));
    if (url.includes('/intent')) {
      state.intentCalls.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return Promise.resolve(
        json({
          stage: 'build',
          action: method,
          from_status: 'empty',
          to_status: 'complete',
          stale: [],
          messages: [],
          artifacts: [],
          run_id: 'r',
        }),
      );
    }
    return Promise.resolve(json({}));
  });
  vi.stubGlobal('fetch', fetchMock);
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<BuildPanel projectId={PROJECT} />, { wrapper });
}

let state: MockState;

beforeEach(() => {
  state = {
    buildStatus: 'complete',
    reports: [reportArtifact()],
    reportDetail: { ...reportArtifact(), content: JSON.stringify(REPORT) },
    intentCalls: [],
  };
});

afterEach(() => vi.unstubAllGlobals());

describe('BuildPanel', () => {
  it('renders the build report and the live-generation IDE', async () => {
    installFetch(state);
    renderPanel();

    expect(await screen.findByTestId('mock-ide')).toBeInTheDocument();
    // Boot status shows in the collapsed report strip; details expand on demand.
    expect(await screen.findByTestId('build-boot-status')).toHaveTextContent('healthy');
    fireEvent.click(screen.getByTestId('build-report-toggle'));
    expect(screen.getByText(/Built the Todos feature\./)).toBeInTheDocument();
    expect(screen.getByText(/Features:/)).toBeInTheDocument(); // the features line rendered
  });

  it('triggers an initial build via proceed', async () => {
    state.reports = [];
    installFetch(state);
    renderPanel();

    const button = await screen.findByTestId('build-proceed');
    expect(button).toHaveTextContent('Build'); // no prior build
    fireEvent.click(button);

    await waitFor(() =>
      expect(state.intentCalls.at(-1)).toMatchObject({ stage: 'build', action: 'proceed' }),
    );
  });

  // Incremental change requests now go through the shared Assistant conversation (a single
  // conversational surface) — covered by ChatPanel.test's refine-on-build case.

  it('skips the build stage', async () => {
    installFetch(state);
    renderPanel();

    fireEvent.click(await screen.findByTestId('build-skip'));
    await waitFor(() =>
      expect(state.intentCalls.at(-1)).toMatchObject({ stage: 'build', action: 'skip' }),
    );
  });

  it('reveals the preview pane on toggle', async () => {
    installFetch(state);
    renderPanel();

    expect(screen.queryByTestId('mock-preview')).not.toBeInTheDocument();
    fireEvent.click(await screen.findByTestId('toggle-preview'));
    expect(await screen.findByTestId('mock-preview')).toBeInTheDocument();
  });
});
