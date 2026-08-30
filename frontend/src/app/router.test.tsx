import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useAuthStore } from '../lib/stores/authStore';
import { routes } from './router';

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

function signIn() {
  useAuthStore.getState().setAuth('tok', {
    id: '1',
    email: 'a@example.com',
    role: 'user',
    created_at: '',
  });
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      // The dashboard lists projects; the app shell polls health. Route both.
      const body = String(input).includes('/projects')
        ? []
        : { status: 'ok', env: 'test', version: '0' };
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    }),
  );
  localStorage.clear();
  useAuthStore.getState().clear();
});

afterEach(() => vi.unstubAllGlobals());

describe('router', () => {
  it('serves the public landing page at the root', async () => {
    renderAt('/');
    // Landing is the one `lazy()` route, so this waits on a dynamic import being transformed on
    // first use — not on the DOM settling. Testing Library's 1s default is sized for the latter
    // and the cold chunk can outrun it when the whole suite is competing for workers.
    expect(
      await screen.findByText('in one guided flow.', undefined, { timeout: 15_000 }),
    ).toBeInTheDocument();
  });

  it('redirects unauthenticated users from /dashboard to /login', () => {
    renderAt('/dashboard');
    expect(screen.getByText('Sign in to your account')).toBeInTheDocument();
  });

  it('renders the dashboard when authenticated', async () => {
    signIn();
    renderAt('/dashboard');
    expect(await screen.findByText('No projects yet')).toBeInTheDocument();
  });

  it('shows a 404 for unknown routes when authenticated', () => {
    signIn();
    renderAt('/does-not-exist');
    expect(screen.getByText(/404/)).toBeInTheDocument();
  });
});
