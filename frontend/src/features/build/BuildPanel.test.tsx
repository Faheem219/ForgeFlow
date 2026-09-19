import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
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
  activity: { build: Record<string, unknown> | null };
  buildStatus: string;
  reports: Array<Record<string, unknown>>;
  reportDetail: Record<string, unknown>;
  intentCalls: Array<Record<string, unknown>>;
}

const REPORT = {
  boot_status: 'healthy',
  files_changed: [
    'backend/src/features/todo/todo.routes.ts',
    'frontend/src/features/todo/TodoPage.tsx',
  ],
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
    if (url.includes('/activity')) return Promise.resolve(json(state.activity));
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
  return { ...render(<BuildPanel projectId={PROJECT} />, { wrapper }), client };
}

let state: MockState;

beforeEach(() => {
  state = {
    activity: { build: null },
    buildStatus: 'complete',
    reports: [reportArtifact()],
    reportDetail: { ...reportArtifact(), content: JSON.stringify(REPORT) },
    intentCalls: [],
  };
  useWorkspaceStore.getState().reset();
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

  it('does not bounce to the next stage on mount, even when the build is already complete', async () => {
    // beforeEach seeds buildStatus: 'complete' — a project reopened for review, not a live finish.
    installFetch(state);
    renderPanel();

    await screen.findByTestId('mock-ide');
    expect(useWorkspaceStore.getState().activeStage).not.toBe('test');
  });

  it('advances to the next stage once an in-flight build genuinely finishes', async () => {
    state.buildStatus = 'in_progress';
    installFetch(state);
    const { client } = renderPanel();
    await screen.findByTestId('mock-ide');
    expect(useWorkspaceStore.getState().activeStage).not.toBe('test'); // not yet — still running

    // The build finishes server-side; the panel's own realtime listener re-fetches stages on this
    // event (see the existing "lists generated files" test for the same pattern). That listener
    // also invalidates `activity`/`build-reports` alongside `stages` — wait for all three to settle,
    // not just the one this test cares about, so no fetch is still in flight past teardown.
    state.buildStatus = 'complete';
    act(() => {
      useRealtimeStore.setState((s) => ({
        events: [
          ...s.events,
          {
            event: 'stage.transition',
            project_id: PROJECT,
            stage: 'build',
            payload: { stage: 'build' },
            seq: 1,
            ts: '',
          },
        ],
      }));
    });

    await waitFor(() => expect(useWorkspaceStore.getState().activeStage).toBe('test'));
    await waitFor(() => expect(client.isFetching()).toBe(0));
  });

  it('lists generated files as their writes stream in', async () => {
    // The plan step writes nothing, so without this surface the panel looks frozen and then dumps
    // every file at once — the exact complaint this panel answers.
    installFetch(state);
    renderPanel();
    await screen.findByTestId('mock-ide');

    act(() => {
      useRealtimeStore.setState({ channel: PROJECT, events: [] });
    });
    act(() => {
      useRealtimeStore.setState((s) => ({
        events: [
          ...s.events,
          {
            event: 'progress',
            project_id: PROJECT,
            stage: 'build',
            payload: { stage: 'build', step: 'implement' },
            seq: 1,
            ts: '',
          },
          {
            event: 'fs.write',
            project_id: PROJECT,
            stage: 'build',
            payload: { path: 'backend/src/todo/model.ts' },
            seq: 2,
            ts: '',
          },
        ],
      }));
    });

    const list = await screen.findByTestId('build-file-list');
    expect(list).toHaveTextContent('backend/src/todo/model.ts');
    expect(screen.getByTestId('build-file-count')).toHaveTextContent('1 file written');
  });

  it('reattaches to a build that is already running after a reload', async () => {
    // The panel mounts fresh (as after a refresh) with no local mutation in flight: the only way it
    // can know a build is running is the server's activity endpoint.
    state.activity = {
      build: {
        run_id: 'r1',
        started_at: '2026-07-25T00:00:00Z',
        step: 'implement',
        label: 'Installing dependencies',
        target: 'backend',
        files: ['backend/src/features/todo/todo.model.ts'],
      },
    };
    installFetch(state);
    renderPanel();

    // It shows what the agent is doing right now, and what it has already written…
    expect(await screen.findByTestId('build-activity-label')).toHaveTextContent(
      'Installing dependencies',
    );
    expect(await screen.findByTestId('build-file-list')).toHaveTextContent(
      'backend/src/features/todo/todo.model.ts',
    );
    // …and refuses to start a competing build.
    expect(await screen.findByTestId('build-proceed')).toBeDisabled();
  });

  it('requests a from-scratch rebuild via the menu', async () => {
    installFetch(state);
    renderPanel();

    fireEvent.click(await screen.findByTestId('build-menu'));
    fireEvent.click(screen.getByTestId('build-fresh'));

    await waitFor(() =>
      expect(state.intentCalls.at(-1)).toMatchObject({
        stage: 'build',
        action: 'proceed',
        payload: { fresh: true },
      }),
    );
  });

  it('reveals the preview pane on toggle', async () => {
    installFetch(state);
    renderPanel();

    expect(screen.queryByTestId('mock-preview')).not.toBeInTheDocument();
    fireEvent.click(await screen.findByTestId('toggle-preview'));
    expect(await screen.findByTestId('mock-preview')).toBeInTheDocument();
  });

  // ---------------------------------------------------------------- phase rail (phase-56)

  it('renders a PRE-REWORK report exactly as before, with no phase rail', async () => {
    // REPORT has no `phases` / `outcome` / `stop_reason` — the whole point of the defensive parse.
    installFetch(state);
    renderPanel();

    expect(await screen.findByTestId('build-boot-status')).toHaveTextContent('healthy');
    expect(screen.queryByTestId('build-phase-list')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('build-report-toggle'));
    expect(screen.queryByTestId('build-stop-reason')).not.toBeInTheDocument();
  });

  it('renders the phase rail from a finished report, with per-row statuses', async () => {
    const phased = {
      ...REPORT,
      phases: [
        { id: 'be-core', title: 'Backend API', kind: 'backend', status: 'done' },
        { id: 'fe-core', title: 'Frontend pages', kind: 'frontend', status: 'failed' },
        { id: 'wiring', title: 'Routing', kind: 'wiring', status: 'pending' },
      ],
      outcome: 'partial',
      stop_reason: 'phases_incomplete',
    };
    state.reportDetail = { ...reportArtifact(), content: JSON.stringify(phased) };
    installFetch(state);
    renderPanel();

    const rail = await screen.findByTestId('build-phase-list');
    const rows = within(rail).getAllByTestId('build-phase-row');
    expect(rows.map((r) => r.getAttribute('data-status'))).toEqual(['done', 'failed', 'pending']);
    expect(rail).toHaveTextContent('Backend API');
    expect(rail).toHaveTextContent('Routing');
  });

  it("shows each phase's evidence — file count and note (phase-64)", async () => {
    const phased = {
      ...REPORT,
      phases: [
        {
          id: 'data',
          title: 'Models',
          kind: 'data',
          status: 'done',
          files: ['backend/src/models/User.ts', 'backend/src/models/Post.ts'],
          note: '',
        },
        {
          id: 'be-auth',
          title: 'Auth API',
          kind: 'backend',
          status: 'failed',
          files: [],
          note: 'wrote nothing after 2 attempt(s)',
        },
      ],
      outcome: 'partial',
      stop_reason: 'no_feature_code',
    };
    state.reportDetail = { ...reportArtifact(), content: JSON.stringify(phased) };
    installFetch(state);
    renderPanel();

    const rail = await screen.findByTestId('build-phase-list');
    const evidence = within(rail).getAllByTestId('build-phase-evidence');
    expect(evidence.map((e) => e.textContent)).toEqual([
      '2 files',
      '0 files · wrote nothing after 2 attempt(s)',
    ]);
    fireEvent.click(screen.getByTestId('build-report-toggle'));
    expect(screen.getByTestId('build-stop-reason')).toHaveTextContent(
      'the app is still the template',
    );
  });

  it('shows the stop reason in plain English when the build did not complete', async () => {
    state.reportDetail = {
      ...reportArtifact(),
      content: JSON.stringify({ ...REPORT, outcome: 'partial', stop_reason: 'environment' }),
    };
    installFetch(state);
    renderPanel();

    fireEvent.click(await screen.findByTestId('build-report-toggle'));
    expect(screen.getByTestId('build-stop-reason')).toHaveTextContent(
      /the sandbox environment, not the code/,
    );
  });

  it('reattaches the phase rail to the right phase after a reload', async () => {
    // Mid-build with no live events (the realtime ring is long since evicted): the durable
    // Run.progress the server reports is the only thing that can place the rail.
    state.activity = {
      build: {
        run_id: 'r1',
        started_at: '2026-07-25T00:00:00Z',
        step: 'phase',
        label: 'Phase 2/3: Frontend pages',
        target: '',
        files: [],
        phase_index: 2,
        phase_total: 3,
        phase_id: 'fe-core',
      },
    };
    state.reportDetail = {
      ...reportArtifact(),
      content: JSON.stringify({
        ...REPORT,
        phases: [
          { id: 'be-core', title: 'Backend API', kind: 'backend', status: 'done' },
          { id: 'fe-core', title: 'Frontend pages', kind: 'frontend', status: 'pending' },
          { id: 'wiring', title: 'Routing', kind: 'wiring', status: 'pending' },
        ],
      }),
    };
    installFetch(state);
    renderPanel();

    const rail = await screen.findByTestId('build-phase-list');
    await waitFor(() =>
      expect(
        within(rail)
          .getAllByTestId('build-phase-row')
          .map((r) => r.getAttribute('data-status')),
      ).toEqual(['done', 'running', 'pending']),
    );
  });

  // ------------------------------------------------- human overrides: approve, and undo a skip

  it('approves the build by hand when verification is holding it', async () => {
    // The stuck case: a build the user can see works, held out of `complete` by the gate — and so
    // out of deploy, with no route back except another (paid, possibly identical) build.
    state.buildStatus = 'awaiting_user';
    installFetch(state);
    renderPanel();

    fireEvent.click(await screen.findByTestId('build-approve'));

    await waitFor(() =>
      expect(state.intentCalls.at(-1)).toMatchObject({
        stage: 'build',
        action: 'proceed',
        payload: { approve: true },
      }),
    );
  });

  it('offers no approval when the stage is already complete', async () => {
    installFetch(state); // beforeEach: buildStatus 'complete'
    renderPanel();

    await screen.findByTestId('build-status');
    expect(screen.queryByTestId('build-approve')).toBeNull();
  });

  it('offers no approval before any build has run', async () => {
    // Approval accepts a build; with none on disk there is nothing to accept.
    state.buildStatus = 'empty';
    state.reports = [];
    installFetch(state);
    renderPanel();

    await screen.findByTestId('build-empty');
    expect(screen.queryByTestId('build-approve')).toBeNull();
  });

  it('offers Un-skip in place of Skip once the stage is skipped', async () => {
    state.buildStatus = 'skipped';
    installFetch(state);
    renderPanel();

    expect(await screen.findByTestId('build-unskip')).toBeInTheDocument();
    expect(screen.queryByTestId('build-skip')).toBeNull();

    fireEvent.click(screen.getByTestId('build-unskip'));
    await waitFor(() =>
      expect(state.intentCalls.at(-1)).toMatchObject({ stage: 'build', action: 'unskip' }),
    );
  });

  it('stays on Build after an un-skip restores it to complete', async () => {
    // The restore looks exactly like a build finishing (-> 'complete'), and the panel advances on
    // that. Here it must not: the user clicked undo to come back, not to be sent onward.
    state.buildStatus = 'skipped';
    installFetch(state);
    renderPanel();

    fireEvent.click(await screen.findByTestId('build-unskip'));
    await waitFor(() => expect(state.intentCalls.at(-1)).toMatchObject({ action: 'unskip' }));

    // Re-baseline the shared event ring first, so this event is new to the panel's cursor
    // whatever earlier tests left behind (see the file-streaming test for the same two-act dance).
    state.buildStatus = 'complete';
    act(() => {
      useRealtimeStore.setState({ channel: PROJECT, events: [] });
    });
    act(() => {
      useRealtimeStore.setState((st) => ({
        events: [
          ...st.events,
          {
            event: 'stage.transition',
            project_id: PROJECT,
            stage: 'build',
            payload: { stage: 'build' },
            seq: 1,
            ts: '',
          },
        ],
      }));
    });

    await waitFor(() => expect(screen.getByTestId('build-status')).toHaveTextContent('Complete'));
    expect(useWorkspaceStore.getState().activeStage).not.toBe('test');
  });

  it('does not show a stop reason on a complete build', async () => {
    state.reportDetail = {
      ...reportArtifact(),
      content: JSON.stringify({ ...REPORT, outcome: 'complete', stop_reason: null }),
    };
    installFetch(state);
    renderPanel();

    fireEvent.click(await screen.findByTestId('build-report-toggle'));
    expect(screen.queryByTestId('build-stop-reason')).not.toBeInTheDocument();
  });
});

// --- phase-60: scope routing is visible and overridable -----------------------------------------

function withReport(extra: Record<string, unknown>): void {
  state.reportDetail = {
    ...reportArtifact(),
    content: JSON.stringify({ ...REPORT, ...extra }),
  };
  installFetch(state);
}

describe('build scope', () => {
  it('says when a build was an incremental change', async () => {
    withReport({ scope: 'small', scope_reason: 'only the home page component changes' });
    renderPanel();

    const badge = await screen.findByTestId('build-scope');
    expect(badge).toHaveTextContent('Incremental change');
    expect(badge).toHaveAttribute('title', 'only the home page component changes');
  });

  it('says how many phases a planned build ran', async () => {
    withReport({
      scope: 'large',
      scope_reason: 'touches every page',
      phases: [
        { id: 'a', title: 'A', kind: 'backend', status: 'done', note: '', summary: '' },
        { id: 'b', title: 'B', kind: 'frontend', status: 'done', note: '', summary: '' },
      ],
    });
    renderPanel();

    expect(await screen.findByTestId('build-scope')).toHaveTextContent(
      'Planned build \u00b7 2 phases',
    );
  });

  it('shows no badge for a report written before scope routing', async () => {
    withReport({});
    renderPanel();

    await screen.findByTestId('build-status');
    expect(screen.queryByTestId('build-scope')).toBeNull();
  });
});
