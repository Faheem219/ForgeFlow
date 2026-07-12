import { render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useAuthStore } from '../lib/stores/authStore';
import { routes } from './router';

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(<RouterProvider router={router} />);
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
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ status: 'ok', env: 'test', version: '0' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    ),
  );
  localStorage.clear();
  useAuthStore.getState().clear();
});

afterEach(() => vi.unstubAllGlobals());

describe('router', () => {
  it('redirects unauthenticated users to /login', () => {
    renderAt('/');
    expect(screen.getByText('Sign in to your account')).toBeInTheDocument();
  });

  it('renders the dashboard when authenticated', async () => {
    signIn();
    renderAt('/');
    expect(await screen.findByText('Welcome back')).toBeInTheDocument();
  });

  it('shows a 404 for unknown routes when authenticated', () => {
    signIn();
    renderAt('/does-not-exist');
    expect(screen.getByText(/404/)).toBeInTheDocument();
  });
});
