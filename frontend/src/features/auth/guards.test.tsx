import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';

import { useAuthStore } from '../../lib/stores/authStore';
import { RequireAuth } from './guards';

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<div>login page</div>} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <div>secret home</div>
            </RequireAuth>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  useAuthStore.getState().clear();
});

describe('RequireAuth', () => {
  it('redirects to /login when unauthenticated', () => {
    renderAt('/');
    expect(screen.getByText('login page')).toBeInTheDocument();
    expect(screen.queryByText('secret home')).not.toBeInTheDocument();
  });

  it('renders children when authenticated', () => {
    useAuthStore.getState().setAuth('tok', {
      id: '1',
      email: 'a@example.com',
      role: 'user',
      created_at: '',
    });
    renderAt('/');
    expect(screen.getByText('secret home')).toBeInTheDocument();
  });
});
