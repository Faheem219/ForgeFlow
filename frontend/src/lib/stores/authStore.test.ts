import { beforeEach, describe, expect, it } from 'vitest';

import { useAuthStore } from './authStore';

beforeEach(() => {
  localStorage.clear();
  useAuthStore.getState().clear();
});

describe('authStore', () => {
  it('stores token + user and persists them to localStorage', () => {
    useAuthStore.getState().setAuth('tok-123', {
      id: '1',
      email: 'a@example.com',
      role: 'user',
      created_at: '2026-01-01T00:00:00Z',
    });

    expect(useAuthStore.getState().token).toBe('tok-123');
    expect(useAuthStore.getState().user?.email).toBe('a@example.com');
    expect(localStorage.getItem('forgeflow-auth')).toContain('tok-123');
  });

  it('clears the session on logout', () => {
    useAuthStore.getState().setAuth('tok', {
      id: '1',
      email: 'a@example.com',
      role: 'admin',
      created_at: '',
    });

    useAuthStore.getState().clear();

    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
  });
});
