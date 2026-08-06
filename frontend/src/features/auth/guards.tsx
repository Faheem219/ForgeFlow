import type { ReactElement } from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import { useAuthStore } from '../../lib/stores/authStore';

/** Redirects to /login when there is no session. */
export function RequireAuth({ children }: { children: ReactElement }): ReactElement {
  const token = useAuthStore((s) => s.token);
  const location = useLocation();
  if (!token) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return children;
}

/** Requires an admin session; non-admins go home, unauthenticated to /login. */
export function RequireAdmin({ children }: { children: ReactElement }): ReactElement {
  const token = useAuthStore((s) => s.token);
  const role = useAuthStore((s) => s.user?.role);
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  if (role !== 'admin') {
    return <Navigate to="/dashboard" replace />;
  }
  return children;
}
