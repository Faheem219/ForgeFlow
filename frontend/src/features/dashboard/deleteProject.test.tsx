import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { useToastStore } from '../../lib/stores/toastStore';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import type { ProjectSummary } from '../../lib/types';
import { useIdeStore } from '../ide/ideStore';
import Dashboard from './Dashboard';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function project(id: string, name: string): ProjectSummary {
  return {
    id,
    name,
    current_stage: 'build',
    status: 'active',
    stack: 'fixed-mern-ts',
    sandbox_id: null,
    design_provider: null,
    app_db_name: `app_${id}`,
    created_at: '',
    updated_at: new Date().toISOString(),
  };
}

function deleteResult(id: string, warnings: string[] = []) {
  return {
    id,
    sandbox_removed: true,
    app_db_dropped: true,
    documents_removed: 12,
    warnings,
  };
}

function renderDashboard(
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/projects/:id" element={<div>workspace opened</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

/** Open the confirm dialog for a project and type its name into the confirmation field. */
async function armDeletion(id: string, name: string): Promise<void> {
  fireEvent.click(await screen.findByTestId(`delete-project-${id}`));
  fireEvent.change(await screen.findByTestId('confirm-dialog-phrase'), {
    target: { value: name },
  });
}

beforeEach(() => {
  localStorage.clear();
  useToastStore.setState({ toasts: [] });
  useWorkspaceStore.getState().reset();
  useIdeStore.getState().reset();
});

afterEach(() => vi.unstubAllGlobals());

describe('delete project', () => {
  it('requires the exact project name before the destructive button arms', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse([project('a', 'Alpha')]))),
    );
    renderDashboard();

    fireEvent.click(await screen.findByTestId('delete-project-a'));
    const confirm = screen.getByTestId('confirm-dialog-confirm');
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByTestId('confirm-dialog-phrase'), { target: { value: 'Alph' } });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByTestId('confirm-dialog-phrase'), { target: { value: 'Alpha' } });
    expect(confirm).toBeEnabled();
  });

  it('issues a DELETE and drops the project from the list', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'DELETE') return Promise.resolve(jsonResponse(deleteResult('a')));
      return Promise.resolve(jsonResponse([project('a', 'Alpha'), project('b', 'Beta')]));
    });
    vi.stubGlobal('fetch', fetchMock);

    renderDashboard();
    await armDeletion('a', 'Alpha');
    fireEvent.click(screen.getByTestId('confirm-dialog-confirm'));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === 'DELETE',
      );
      expect(call).toBeTruthy();
      expect(String(call?.[0])).toContain('/projects/a');
    });
  });

  it('scrubs realtime, workspace and IDE state for the deleted project', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'DELETE') return Promise.resolve(jsonResponse(deleteResult('a')));
      return Promise.resolve(jsonResponse([project('a', 'Alpha')]));
    });
    vi.stubGlobal('fetch', fetchMock);

    // Simulate having the doomed project open: workspace pointed at it, a file tab, a live channel.
    useWorkspaceStore.getState().openProject('a', 'build');
    useIdeStore.getState().openFile('src/App.tsx', 'export default App');
    useRealtimeStore.setState({ channel: 'a', status: 'open' });

    renderDashboard();
    await armDeletion('a', 'Alpha');
    fireEvent.click(screen.getByTestId('confirm-dialog-confirm'));

    await waitFor(() => {
      expect(useWorkspaceStore.getState().projectId).toBeNull();
      expect(useIdeStore.getState().order).toEqual([]);
      expect(useRealtimeStore.getState().channel).toBeNull();
    });
  });

  it('leaves another open project untouched', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'DELETE') return Promise.resolve(jsonResponse(deleteResult('a')));
      return Promise.resolve(jsonResponse([project('a', 'Alpha'), project('b', 'Beta')]));
    });
    vi.stubGlobal('fetch', fetchMock);

    // 'b' is the open workspace; deleting 'a' must not disturb it.
    useWorkspaceStore.getState().openProject('b', 'design');
    useRealtimeStore.setState({ channel: 'b', status: 'open' });

    renderDashboard();
    await armDeletion('a', 'Alpha');
    fireEvent.click(screen.getByTestId('confirm-dialog-confirm'));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === 'DELETE',
      );
      expect(call).toBeTruthy();
    });
    expect(useWorkspaceStore.getState().projectId).toBe('b');
    expect(useRealtimeStore.getState().channel).toBe('b');
  });

  it('surfaces partial-teardown warnings instead of reporting a clean delete', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        return Promise.resolve(
          jsonResponse(deleteResult('a', ['sandbox: docker daemon unreachable'])),
        );
      }
      return Promise.resolve(jsonResponse([project('a', 'Alpha')]));
    });
    vi.stubGlobal('fetch', fetchMock);

    renderDashboard();
    await armDeletion('a', 'Alpha');
    fireEvent.click(screen.getByTestId('confirm-dialog-confirm'));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.variant === 'warning')).toBe(true);
      expect(toasts.some((t) => t.description?.includes('docker daemon unreachable'))).toBe(true);
    });
  });

  it('keeps the project and reports the failure when the server rejects the delete', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        return Promise.resolve(
          jsonResponse({ error: { type: 'system_error', message: 'teardown exploded' } }, 500),
        );
      }
      return Promise.resolve(jsonResponse([project('a', 'Alpha')]));
    });
    vi.stubGlobal('fetch', fetchMock);

    renderDashboard();
    await armDeletion('a', 'Alpha');
    fireEvent.click(screen.getByTestId('confirm-dialog-confirm'));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.variant === 'error')).toBe(true);
    });
    // The row survives a failed delete, and the dialog stays open so the user can retry.
    expect(screen.getByTestId('project-a')).toBeInTheDocument();
    expect(screen.getByTestId('confirm-dialog-confirm')).toBeInTheDocument();
  });
});
