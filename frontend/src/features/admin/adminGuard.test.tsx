import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';

import { useAuthStore } from '../../lib/stores/authStore';
import { RequireAdmin } from '../auth/guards';

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<div>login page</div>} />
        {/* Non-admins are sent to the app home, which lives at /dashboard. */}
        <Route path="/dashboard" element={<div>home</div>} />
        <Route
          path="/admin"
          element={
            <RequireAdmin>
              <div>admin dashboard</div>
            </RequireAdmin>
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

describe('RequireAdmin (admin dashboard guard)', () => {
  it('sends an unauthenticated visitor to /login', () => {
    renderAt('/admin');
    expect(screen.getByText('login page')).toBeInTheDocument();
    expect(screen.queryByText('admin dashboard')).not.toBeInTheDocument();
  });

  it('sends a signed-in non-admin home', () => {
    useAuthStore.getState().setAuth('tok', {
      id: '1',
      email: 'user@example.com',
      role: 'user',
      created_at: '',
    });
    renderAt('/admin');
    expect(screen.getByText('home')).toBeInTheDocument();
    expect(screen.queryByText('admin dashboard')).not.toBeInTheDocument();
  });

  it('lets an admin through', () => {
    useAuthStore.getState().setAuth('tok', {
      id: '2',
      email: 'admin@example.com',
      role: 'admin',
      created_at: '',
    });
    renderAt('/admin');
    expect(screen.getByText('admin dashboard')).toBeInTheDocument();
  });
});
