import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useToastStore } from '../../lib/stores/toastStore';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import type { RepairEscalation } from '../../lib/types';
import { Escalation } from './Escalation';

const PROJECT = 'p1';
const SRC = 'backend/src/features/todos/todos.controller.ts';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const ESCALATION: RepairEscalation = {
  reason: 'stalled',
  summary:
    'I stopped after 2 attempt(s) because the failing tests stopped shrinking. ' +
    '2 test(s) are still failing: rejects an empty title, adds a todo.',
  failing_tests: [
    {
      name: 'rejects an empty title',
      criterion_id: 'ac-empty',
      file: 'todos.test.ts',
      message: 'expected 400, received 201',
    },
  ],
  diffs_tried: [
    { id: 'a1', iteration: 1, target_files: [SRC], diff_ref: 'fs:d1', outcome: 'no_progress' },
    { id: 'a2', iteration: 2, target_files: [SRC], diff_ref: 'fs:d2', outcome: 'no_progress' },
  ],
  metrics: {
    initial_failing: 2,
    final_failing: 2,
    failing_by_iteration: [2, 2],
    regressions_introduced: 0,
    iterations: 2,
    tokens_spent: 900,
    cost_inr: 1.2,
    wall_clock_s: 30,
  },
  resume: { stage: 'build', action: 'refine', hint: 'Describe what to change…' },
};

interface MockState {
  intentCalls: Array<Record<string, unknown>>;
  status: number;
}

function installFetch(state: MockState): void {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>((input, init) => {
      if (String(input).includes('/intent')) {
        state.intentCalls.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        if (state.status >= 400) {
          return Promise.resolve(
            json({ error: { type: 'user_error', message: 'build is busy' } }, state.status),
          );
        }
        return Promise.resolve(
          json({
            stage: 'build',
            action: 'refine',
            from_status: 'complete',
            to_status: 'complete',
            stale: [],
            messages: [],
            artifacts: [],
            run_id: 'r',
          }),
        );
      }
      return Promise.resolve(json({}));
    }),
  );
}

function renderEscalation(onResumed = vi.fn(), escalation: RepairEscalation = ESCALATION) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  render(<Escalation projectId={PROJECT} escalation={escalation} onResumed={onResumed} />, {
    wrapper,
  });
  return onResumed;
}

let state: MockState;

beforeEach(() => {
  state = { intentCalls: [], status: 200 };
  useToastStore.setState({ toasts: [] });
  useWorkspaceStore.setState({ activeStage: 'test' });
});
afterEach(() => vi.unstubAllGlobals());

describe('Escalation', () => {
  it('explains where the loop got stuck and what it already tried', () => {
    installFetch(state);
    renderEscalation();

    expect(screen.getByTestId('escalation-reason')).toHaveTextContent('Stopped making progress');
    expect(screen.getByTestId('escalation-summary')).toHaveTextContent('stopped shrinking');
    expect(screen.getByTestId('escalation-failing')).toHaveTextContent('rejects an empty title');
    expect(screen.getByTestId('escalation-failing')).toHaveTextContent('ac-empty');
    // The diffs already tried, so the user doesn't repeat them.
    expect(screen.getByTestId('escalation-diffs')).toHaveTextContent('#1');
    expect(screen.getByTestId('escalation-diffs')).toHaveTextContent('#2');
  });

  it("names the no-patch stop and shows the agent's own explanation", () => {
    // phase-63: the loop stopped because the fix lay outside the files the agent was given.
    installFetch(state);
    renderEscalation(vi.fn(), {
      ...ESCALATION,
      reason: 'no_patch',
      summary:
        'I stopped after 2 attempt(s) because I could not fix it from the files I was given. ' +
        'The fix has to live in the component that renders the form.',
    });

    expect(screen.getByTestId('escalation-reason')).toHaveTextContent(
      "Couldn't fix it from the files it was given",
    );
    expect(screen.getByTestId('escalation-summary')).toHaveTextContent(
      'the component that renders the form',
    );
  });

  it('names an environment stop as not a code bug, with the fix it needs', () => {
    // phase-65: the sandbox, not the code, was broken, so the loop stopped before any attempt.
    installFetch(state);
    renderEscalation(vi.fn(), {
      ...ESCALATION,
      reason: 'environment',
      summary:
        'I stopped after 0 attempt(s) because the sandbox environment is broken, not the code. ' +
        'Rebuild the image with `make sandbox-build`. Fix the environment, then run the tests again.',
    });

    expect(screen.getByTestId('escalation-reason')).toHaveTextContent(
      'Sandbox environment problem, not a code bug',
    );
    expect(screen.getByTestId('escalation-summary')).toHaveTextContent('make sandbox-build');
  });

  it('submits guidance through the resume contract the controller named', async () => {
    installFetch(state);
    const onResumed = renderEscalation();

    fireEvent.change(screen.getByTestId('guidance-input'), {
      target: { value: 'validate the title before saving' },
    });
    fireEvent.click(screen.getByTestId('submit-guidance'));

    await waitFor(() =>
      // Resuming re-enters Build (phase-24) with the user's guidance.
      expect(state.intentCalls.at(-1)).toMatchObject({
        stage: 'build',
        action: 'refine',
        message: 'validate the title before saving',
      }),
    );
    await waitFor(() => expect(onResumed).toHaveBeenCalled());
    // ...and the workspace follows the pipeline to Build.
    expect(useWorkspaceStore.getState().activeStage).toBe('build');
  });

  it('will not submit empty guidance', () => {
    installFetch(state);
    renderEscalation();

    expect(screen.getByTestId('submit-guidance')).toBeDisabled();
    fireEvent.click(screen.getByTestId('submit-guidance'));
    expect(state.intentCalls).toHaveLength(0);
  });

  it('surfaces a failed resume as a toast', async () => {
    state.status = 400;
    installFetch(state);
    renderEscalation();

    fireEvent.change(screen.getByTestId('guidance-input'), { target: { value: 'try again' } });
    fireEvent.click(screen.getByTestId('submit-guidance'));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.description?.includes('build is busy'))).toBe(true);
    });
  });
});
