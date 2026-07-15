import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ProjectSummary } from '../../lib/types';
import Dashboard from './Dashboard';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function project(id: string, name: string): ProjectSummary {
  return {
    id,
    name,
    current_stage: 'design',
    status: 'active',
    stack: 'fixed-mern-ts',
    sandbox_id: null,
    design_provider: null,
    app_db_name: `app_${id}`,
    created_at: '',
    updated_at: '',
  };
}

function renderDashboard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects/:id" element={<div>workspace opened</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('Dashboard', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('lists the user projects', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse([project('a', 'Alpha'), project('b', 'Beta')]))),
    );

    renderDashboard();

    expect(await screen.findByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
  });

  it('creates a project and opens its workspace', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'POST') {
        return Promise.resolve(jsonResponse(project('new1', 'Fresh')));
      }
      return Promise.resolve(jsonResponse([]));
    });
    vi.stubGlobal('fetch', fetchMock);

    renderDashboard();

    // Open the create modal (there are two "New project" buttons — header + empty state).
    fireEvent.click(screen.getAllByRole('button', { name: 'New project' })[0]);
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Fresh' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === 'POST',
      );
      expect(postCall).toBeTruthy();
      expect(JSON.parse(String((postCall?.[1] as RequestInit).body))).toEqual({ name: 'Fresh' });
    });

    // Navigation lands on the new project's workspace.
    expect(await screen.findByText('workspace opened')).toBeInTheDocument();
  });
});
